"""每日快讯视频生成器。

读取 content/processed_YYYY-MM-DD.json:
  - ai_top      AI 板块精选(带 voice_script;--curated 时用人工策划列表替代)

画面流程(Playwright 截图 + ffmpeg 合成,无运镜,每条时长 = 该条 TTS 口播时长):
  1. 片头: 整体首页 site/index.html 页头短暂出现几秒,
     口播 "欢迎收看X月X日的每日AI快讯";
  2. 正文: 每条快讯渲染为独立详情页(单卡排版 + 纯 CSS riseIn 入场动画),
     逐页截图,音画精确同步;
  3. 片尾: 回到首页页头,口播结束语。

输出(默认只出横屏 B站版):
  - video/YYYY-MM-DD_horizontal.mp4 横屏 1920x1080 (B站 16:9)
  - video/YYYY-MM-DD_vertical.mp4   竖屏 1080x1920 (抖音 9:16,需 --vertical / --both)
用法:
  python src/video_builder.py --date 2026-08-13                  # 默认横屏
  python src/video_builder.py --date 2026-08-13 --vertical       # 只出竖屏
  python src/video_builder.py --date 2026-08-13 --both           # 两方向都出
  python src/video_builder.py --date 2026-08-13 --voice yunxi --rate -10%
依赖: 需先运行 site_generator.py 生成 site/(含 index.html 与风格样式);
需要 LD_LIBRARY_PATH 指向 tools/rootfs(Playwright chromium 与 ffmpeg 均需)。
"""

import asyncio
import html
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

# 默认配音:s04 云哲沉稳档(试听选定),可用 --voice / --rate / --pitch 覆盖
TTS_VOICE = "zh-TW-YunJheNeural"
TTS_RATE = "-12%"
TTS_PITCH = "-6Hz"
PAD_BEFORE = 0.35
PAD_AFTER = 0.6
FPS = 25

# 片尾口播(画面用首页 hero 页头截图);片头口播按日期动态生成(见 build_video)
OUTRO_TEXT = "今天的AI播报结束了，我们明天见"

# 可选音色(短名 → edge-tts 全名),--voice 参数取值
VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",   # 晓晓 · 温暖知性女声
    "xiaoyi": "zh-CN-XiaoyiNeural",       # 晓伊 · 活泼年轻女声
    "yunxi": "zh-CN-YunxiNeural",         # 云希 · 阳光少年男声
    "yunjian": "zh-CN-YunjianNeural",     # 云健 · 低沉磁性男声
    "yunyang": "zh-CN-YunyangNeural",     # 云扬 · 专业播音男声
    "yunjhe": "zh-TW-YunJheNeural",       # 云哲 · 沉稳稳重男声(默认)
    "wanlung": "zh-HK-WanLungNeural",     # 云龙 · 港式粤语男声
}

# 星期 → 样式名(与 site_generator 的 WEEKDAY_STYLES 保持一致;tue/wed 退役见 BANNED_STYLES.md)
WEEKDAY_STYLES = ["mon", "swiss", "riso", "thu", "fri", "sat", "sun"]


def load_curated(date):
    """读取 content/curated_{date}.json([{link,index},...]),返回按序取回的条目列表。

    条目取自 processed_{date}.json 的 items[];缺失/出错返回 []。
    """
    path = config.CONTENT_DIR / f"curated_{date}.json"
    if not path.exists():
        return []
    proc = config.CONTENT_DIR / f"processed_{date}.json"
    data = json.loads(proc.read_text(encoding="utf-8")) if proc.exists() else {}
    items = data.get("items") or []
    by_link = {it.get("link"): it for it in items}
    by_index = {it.get("index"): it for it in items}
    try:
        sel = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for s in sel:
        it = by_link.get(s.get("link")) or by_index.get(s.get("index"))
        if it:
            out.append(it)
    return out


def style_for_date(date_str):
    """把日期字符串映射到星期样式名(与 site_generator 一致),失败回退 mon。"""
    try:
        return WEEKDAY_STYLES[datetime.strptime(str(date_str), "%Y-%m-%d").weekday()]
    except (ValueError, TypeError):
        return WEEKDAY_STYLES[0]


def load_style_css(style):
    """读取风格 CSS 文本(优先 site/assets/styles,回退 assets/styles)。"""
    for base in (config.SITE_DIR / "assets" / "styles", config.ASSET_DIR / "styles"):
        css = base / f"{style}.css"
        if css.exists():
            return css.read_text(encoding="utf-8")
    return ""


def esc(text):
    return html.escape(str(text or ""))


def fmt_time(iso):
    """把 ISO 时间格式化为 HH:MM;失败时原样返回。"""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%H:%M")
    except (ValueError, TypeError):
        return str(iso or "")


# 详情页扩展样式:单条快讯独立页(与 site_generator.DETAIL_CSS 保持一致;
# 复用七套风格 tokens,纯 CSS riseIn 入场动画自动播放,不依赖 JS)
DETAIL_CSS = """
/* ---------- 详情页扩展:单条快讯独立页 ---------- */
.detail-hero{padding:64px 0 40px}
.detail-card{max-width:900px;margin:0 auto;width:100%}
.detail-title{font-size:clamp(26px,3vw,42px);font-weight:800;line-height:1.42;
  letter-spacing:-.01em;margin:0 0 18px}
.detail-title a{color:inherit;text-decoration:none}
.detail-summary{display:block;overflow:visible;-webkit-line-clamp:unset;
  font-size:clamp(17px,1.5vw,21px);line-height:1.9;color:var(--text)}
.detail-card .voice{display:block;overflow:visible;-webkit-line-clamp:unset;
  font-size:clamp(15px,1.3vw,18px);line-height:1.8;margin-top:24px;padding:14px 18px}
.detail-card .meta{font-size:14px;margin-top:26px;padding-top:18px}
@keyframes riseIn{from{opacity:0;transform:translateY(32px)}to{opacity:1;transform:none}}
.detail-anim{animation:riseIn .8s cubic-bezier(.22,.61,.21,1) both}
.da1{animation-delay:.15s}
.da2{animation-delay:.32s}
.da3{animation-delay:.5s}
@media (prefers-reduced-motion:reduce){.detail-anim{animation:none}}
@media (max-width:720px){.detail-title{font-size:24px}.detail-hero{padding:48px 0 32px}}
"""


def detail_inline_html(date, rank, total, item, style):
    """构建单条快讯独立详情页 HTML(与 site/news/ 详情页同构,供逐页截图)。

    风格 CSS 以 <style> 内联注入;字体由 FONT_CSS_TPL 注入
    (相对路径基于当前页面 URL,即 site/index.html,因此需先 goto 首页)。
    """
    css_text = load_style_css(style)
    title = item.get("title", "")
    category = item.get("category", "AI")
    score = float(item.get("score", 0) or 0)
    summary = item.get("detail_text", "") or item.get("summary", "")
    voice = item.get("voice_script", "")
    voice_html = f'<p class="voice">{esc(voice)}</p>' if voice else ""
    source = item.get("source", "")
    link = item.get("link", "#")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AI 快讯 · {date}</title>
<style>{css_text}</style>
<style>{DETAIL_CSS}</style>
</head>
<body class="ai">
<header class="hero detail-hero">
  <div class="wrap">
    <span class="badge hero-badge">AI 快讯 · {rank:02d}/{total:02d}</span>
    <h1 class="site-title hero-title">每日AI快讯</h1>
    <p class="sub hero-sub">{date} · {esc(category)}</p>
    <div class="hero-stats">
      <span class="stat"><b>{score:.1f}</b> 关注度</span>
      <span class="stat"><b>{esc(source)}</b> 来源</span>
    </div>
  </div>
</header>
<div class="wrap">
  <h2 class="section">快讯详情 <span class="count">#{rank:02d}</span></h2>
  <article class="card featured detail-card detail-anim da1">
    <h3 class="detail-title"><a href="{esc(link)}">{esc(title)}</a></h3>
    <p class="summary detail-summary">{esc(summary)}</p>
    {voice_html}
    <div class="meta">
      <span class="src">{esc(source)}</span>
      <span style="display:inline-flex;gap:12px">
        <span class="time">{fmt_time(item.get('published'))}</span>
        <span class="score">★ {score:.1f}</span>
      </span>
    </div>
  </article>
</div>
</body>
</html>
"""


# 注入到 site 页面的 @font-face 与竖屏适配样式
# 新版站点样式按正常网页字号设计(无 zoom 基准),
# 视频 viewport 即输出分辨率,这里按方向放大整体比例以获得海报级可读字号。
H_ZOOM = 1.5    # 横屏 1920x1080
V_ZOOM = 1.75   # 竖屏 1080x1920

FONT_CSS_TPL = """
:root{{zoom:{zoom}!important}}
@font-face{{font-family:'SiteFont';font-weight:400;font-style:normal;
src:url('assets/fonts/simhei.ttf') format('truetype')}}
@font-face{{font-family:'SiteFont';font-weight:700;font-style:normal;
src:url('assets/fonts/msyhbd.ttc') format('truetype')}}
body,button,input,textarea{{font-family:'SiteFont','Microsoft YaHei',sans-serif !important}}
"""

VERTICAL_CSS = """
.wrap{max-width:100%;padding:0 28px 72px}
.featured-row{grid-template-columns:1fr}
.hero{padding:64px 0 40px}
h1.site-title{font-size:clamp(56px,11vw,92px)}
.sub{font-size:26px}
.card.featured h3{font-size:42px;line-height:1.5}
.card .summary{font-size:28px;-webkit-line-clamp:4}
.card .voice{font-size:27px;-webkit-line-clamp:3}
.card .meta{font-size:23px}
"""


def _run(cmd, timeout=600):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = config.FF_LD_LIBRARY_PATH
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(str(c) for c in cmd)}\n{p.stderr[-3000:]}")
    return p


def tts(text, out_path):
    """edge-tts 合成 mp3,带 60s 超时,失败重试 4 次(含切换备选音色)。

    默认音色/语速/音调取模块级 TTS_VOICE/TTS_RATE/TTS_PITCH,
    可通过 --voice / --rate 覆盖。
    """
    voices = [TTS_VOICE, "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunyangNeural"]
    loop = asyncio.new_event_loop()
    try:
        for attempt in range(5):
            try:
                asyncio.set_event_loop(loop)
                voice = voices[attempt % len(voices)]
                c = __import__("edge_tts").Communicate(
                    text, voice=voice, rate=TTS_RATE, pitch=TTS_PITCH)
                loop.run_until_complete(
                    asyncio.wait_for(c.save(str(out_path)), timeout=60))
                if out_path.stat().st_size == 0:
                    raise RuntimeError("生成空文件")
                return
            except Exception as e:
                print(f"[video] TTS 失败(第{attempt+1}/5 次): {e}")
                if attempt == 4:
                    raise
                time.sleep(4 * (attempt + 1))
    finally:
        loop.close()


def probe_duration(path):
    p = _run([
        str(config.FFPROBE_BIN), "-v", "error",
        "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
        str(path),
    ])
    return float(p.stdout.strip())


def concat_videos(seg_paths, out_path):
    """用 concat demuxer 拼接同参数视频段。"""
    list_file = out_path.with_suffix(".txt")
    list_file.write_text("".join(f"file '{p}'\n" for p in seg_paths), encoding="utf-8")
    cmd = [
        str(config.FFMPEG_BIN), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]
    _run(cmd)
    list_file.unlink(missing_ok=True)


def concat_audios(mp3_paths, out_path):
    """拼接音频,每段前加 PAD_BEFORE 静音、后加 PAD_AFTER 静音,与视频段逐一对齐。"""
    n = len(mp3_paths)
    inputs = []
    for p in mp3_paths:
        inputs += ["-i", str(p)]
    fc = []
    parts = []
    for i in range(n):
        fc.append(f"[{i}:a]adelay={int(PAD_BEFORE * 1000)}:all=1,"
                  f"apad=pad_dur={PAD_AFTER:.3f}[a{i}]")
        parts.append(f"[a{i}]")
    fc.append(f"{''.join(parts)}concat=n={n}:v=0:a=1[out]")
    cmd = [
        str(config.FFMPEG_BIN), "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    _run(cmd)


def make_mp3s(section_name, items, tmp):
    """为一个板块的所有条目生成 TTS mp3,返回 {mp3_path, dur} 列表。"""
    result = []
    for i, item in enumerate(items):
        mp3 = tmp / f"{section_name}_{i:02d}.mp3"
        if not mp3.exists():
            tts(item.get("voice_script") or item.get("summary") or item.get("title"), mp3)
        try:
            a_dur = probe_duration(mp3)
        except RuntimeError:
            print(f"[video] mp3 损坏,重新生成 {mp3.name}")
            mp3.unlink(missing_ok=True)
            tts(item.get("voice_script") or item.get("summary") or item.get("title"), mp3)
            a_dur = probe_duration(mp3)
        result.append({"mp3": mp3, "dur": a_dur + PAD_BEFORE + PAD_AFTER})
    return result


def ensure_site_assets():
    """把本地字体复制到 site/assets/fonts/,供 @font-face 引用。"""
    dst = config.SITE_DIR / "assets" / "fonts"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("simhei.ttf", "msyhbd.ttc"):
        src = config.ASSET_DIR / "fonts" / name
        if src.exists():
            target = dst / name
            if not target.exists() or target.stat().st_size != src.stat().st_size:
                shutil.copy(src, target)


def capture_intro_hero(page, width, height, tmp, vertical=False):
    """打开整体首页 site/index.html,截取页头画面(片头/片尾画面,短暂出现几秒)。"""
    index_file = config.SITE_DIR / "index.html"
    if not index_file.exists():
        raise SystemExit(f"缺少页面 {index_file},请先运行 site_generator.py")
    page.goto(index_file.as_uri(), wait_until="load")
    page.wait_for_timeout(400)
    page.add_style_tag(content=FONT_CSS_TPL.format(zoom=V_ZOOM if vertical else H_ZOOM))
    if vertical:
        page.add_style_tag(content=VERTICAL_CSS)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1500)   # 等 hero 入场动画与数字滚动结束
    png = tmp / f"hero_{width}x{height}.png"
    page.screenshot(path=str(png))
    return png


def capture_detail_pages(page, date, items, width, height, tmp, style, vertical=False,
                         label="AI 每日速递"):
    """逐条注入独立详情页 HTML,等待 riseIn 入场动画结束后整屏截图。

    基 URL 为 site/index.html(需先调用 capture_intro_hero),
    保证 FONT_CSS_TPL 的字体相对路径可用。
    """
    total = len(items)
    paths = []
    for i, item in enumerate(items):
        page.set_content(detail_inline_html(date, i + 1, total, item, style),
                         wait_until="load")
        page.add_style_tag(content=FONT_CSS_TPL.format(zoom=V_ZOOM if vertical else H_ZOOM))
        if vertical:
            page.add_style_tag(content=VERTICAL_CSS)
        card = page.locator("article.card.featured").first
        card.evaluate("el => el.scrollIntoView({block:'center', behavior:'instant'})")
        page.wait_for_timeout(1600)   # 等 hero + riseIn 动画结束再截图
        png = tmp / f"detail_{width}x{height}_{i:02d}.png"
        page.screenshot(path=str(png))
        paths.append(png)
        print(f"[video] [{label}] 截图 {i+1}/{total}: {item.get('title', '')[:20]}")
    return paths


def render_segment_from_shot(png_path, duration, width, height, out_path):
    """对单张截图做静态展示(无运镜),生成长度为 duration 的视频段。"""
    cmd = [
        str(config.FFMPEG_BIN), "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1",
        "-i", str(png_path),
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(out_path),
    ]
    _run(cmd)


def build_video(date, orientation="both", max_items=None, voice=None, rate=None, pitch=None,
                curated=False):
    global TTS_VOICE, TTS_RATE, TTS_PITCH
    if voice:
        TTS_VOICE = VOICES.get(voice, voice)
    if rate:
        TTS_RATE = rate
    if pitch:
        TTS_PITCH = pitch
    proc = config.CONTENT_DIR / f"processed_{date}.json"
    if not proc.exists():
        raise SystemExit(f"缺少 {proc}")
    data = json.loads(proc.read_text(encoding="utf-8"))

    # 条目列表:默认 AI 板块精选;--curated 时用人工策划列表(跨类别)替代
    if curated:
        items = load_curated(date)
        if not items:
            raise SystemExit(f"[video] {date} 没有 content/curated_{date}.json,"
                             f"请先在 site/curated.html 勾选并导出。")
        label = "我的策划"
    else:
        items = list(data.get("ai_top") or [])
        label = "AI 每日速递"
    if max_items:
        items = items[:max_items]
    if not items:
        print(f"[video] {date} 没有可播报条目,跳过。")
        return
    print(f"[video] 使用{'人工策划列表' if curated else 'AI 精选'}: {len(items)} 条")

    style = style_for_date(date)
    tmp = config.VIDEO_DIR / f".tmp_{date}"
    tmp.mkdir(parents=True, exist_ok=True)

    # 0) TTS 只生成一次(两方向共用)
    mp3_specs = make_mp3s("ai", items, tmp)

    # 0.1) 片头/片尾口播(片头带日期;画面均为首页 hero 页头)
    dt = datetime.strptime(date, "%Y-%m-%d")
    intro_text = f"欢迎收看{dt.month}月{dt.day}日的每日AI快讯"
    intro_mp3 = tmp / "intro.mp3"
    outro_mp3 = tmp / "outro.mp3"
    if not intro_mp3.exists():
        tts(intro_text, intro_mp3)
    if not outro_mp3.exists():
        tts(OUTRO_TEXT, outro_mp3)
    intro_dur = probe_duration(intro_mp3) + PAD_BEFORE + PAD_AFTER
    outro_dur = probe_duration(outro_mp3) + PAD_BEFORE + PAD_AFTER

    # 0.5) 站点资产与浏览器依赖
    ensure_site_assets()
    os.environ.setdefault("LD_LIBRARY_PATH", config.FF_LD_LIBRARY_PATH)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("缺少 playwright,请先 pip install playwright")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            for orient, out_name, size in (
                    ("vertical", f"{date}_vertical.mp4", config.VIDEO_VERTICAL),
                    ("horizontal", f"{date}_horizontal.mp4", config.VIDEO_HORIZONTAL)):
                if orientation not in ("both", orient):
                    continue
                width, height = size.values()
                vertical = (orient == "vertical")

                # 1) 截图:首页页头(片头/片尾) + 每条快讯独立详情页
                ctx = browser.new_context(viewport={"width": width, "height": height})
                page = ctx.new_page()
                try:
                    hero_png = capture_intro_hero(page, width, height, tmp, vertical=vertical)
                    shots = capture_detail_pages(page, date, items, width, height, tmp,
                                                 style, vertical=vertical, label=label)
                finally:
                    page.close()
                    ctx.close()

                # 2) 渲染分段并拼接:片头 + 逐条详情页 + 片尾
                intro_seg = tmp / f"intro_{width}x{height}.mp4"
                render_segment_from_shot(hero_png, intro_dur, width, height, intro_seg)
                seg_paths = [intro_seg]
                for i, spec in enumerate(mp3_specs):
                    seg = tmp / f"seg_{i:02d}_{width}x{height}.mp4"
                    render_segment_from_shot(shots[i], spec["dur"], width, height, seg)
                    seg_paths.append(seg)
                    print(f"[video] [{label}] 段{i+1}/{len(items)} "
                          f"时长 {spec['dur']:.1f}s: {items[i].get('title', '')[:20]}")
                outro_seg = tmp / f"outro_{width}x{height}.mp4"
                render_segment_from_shot(hero_png, outro_dur, width, height, outro_seg)
                seg_paths.append(outro_seg)

                full_v = tmp / f"full_{width}x{height}.mp4"
                concat_videos(seg_paths, full_v)

                # 3) 音频拼接与音视频合并
                audio_full = tmp / f"{orient}_only.m4a"
                concat_audios([intro_mp3, *[sp["mp3"] for sp in mp3_specs], outro_mp3],
                              audio_full)
                out = config.VIDEO_DIR / out_name
                cmd = [
                    str(config.FFMPEG_BIN), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(full_v), "-i", str(audio_full),
                    "-c:v", "copy", "-c:a", "copy", "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest",
                    str(out),
                ]
                _run(cmd)
                dur = probe_duration(out)
                print(f"[video] 已生成 {out.name} ({dur:.1f}s, {len(items)}条 + 片头片尾)")
        finally:
            browser.close()

    # 清理临时目录
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="生成每日快讯视频")
    ap.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    ap.add_argument("--vertical", action="store_true", help="只出竖屏(抖音)")
    ap.add_argument("--horizontal", action="store_true", help="只出横屏(B站,默认)")
    ap.add_argument("--both", action="store_true", help="竖屏+横屏都出")
    ap.add_argument("--max-items", type=int, default=None, help="只取前 N 条精选")
    ap.add_argument("--voice", choices=sorted(VOICES), default=None,
                    help=f"配音音色(默认 {TTS_VOICE}),可选: "
                         f"{', '.join(f'{k}({v})' for k, v in VOICES.items())}")
    ap.add_argument("--rate", default=None,
                    help=f"语速(默认 {TTS_RATE},如 -10% 放慢 / +10% 加快)")
    ap.add_argument("--pitch", default=None,
                    help=f"音调(默认 {TTS_PITCH},如 +10Hz / -10Hz)")
    ap.add_argument("--curated", action="store_true",
                    help="使用 content/curated_<date>.json 人工策划列表(跨类别),替代自动 ai_top")
    args = ap.parse_args()

    if args.both:
        orientation = "both"
    elif args.vertical and not args.horizontal:
        orientation = "vertical"
    else:
        orientation = "horizontal"
    build_video(args.date, orientation=orientation, max_items=args.max_items,
                voice=args.voice, rate=args.rate, pitch=args.pitch,
                curated=args.curated)


if __name__ == "__main__":
    main()
