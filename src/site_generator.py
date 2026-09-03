"""每日快讯静态站生成器。

读取 content/processed_YYYY-MM-DD.json,输出到 site/:
  - site/index.html              最新一期快讯首页(今日精选 + 全部资讯 + 往期列表)
  - site/daily/YYYY-MM-DD.html   每日详情页
  - site/styles/{mon..sun}.html  七种风格预览页(内容相同,风格各异)
  - site/assets/styles/*.css     七套星期风格样式(mon~sun)+ 共享 site.js

按内容日期的星期自动轮换视觉风格:
  周一 Editorial / 周二 Swiss / 周三 Risograph / 周四 Neo-Brutalism
  周五 Synthwave / 周六 Retro / 周日 Soft Pastel
样式源文件在 assets/styles/,详见 assets/styles/DESIGN.md。

纯本地生成,可直接用浏览器打开 file:// 查看,也可用任意静态服务器托管。
"""

import html
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

SITE_ASSET_DIR = config.SITE_DIR / "assets"
STYLES_SRC_DIR = config.ASSET_DIR / "styles"
STYLES_DEST_DIR = SITE_ASSET_DIR / "styles"

# 按内容日期 weekday() 选择样式:0=周一 ... 6=周日
# (tue/wed 退役:2026-09 起 Swiss 接管周二、Risograph 接管周三,见 BANNED_STYLES.md)
WEEKDAY_STYLES = ["mon", "swiss", "riso", "thu", "fri", "sat", "sun"]

STYLE_NAMES = {
    "mon": ("周一", "Editorial 杂志编辑风"),
    "swiss": ("周二", "Swiss 瑞士国际主义"),
    "riso": ("周三", "Risograph 油墨印刷"),
    "thu": ("周四", "Neo-Brutalism 新粗野主义"),
    "fri": ("周五", "OpenCode 工程终端风"),
    "sat": ("周六", "Retro 复古暖调"),
    "sun": ("周日", "Soft Pastel 柔和粉彩"),
    # 草稿候选(仅生成预览页,不参与星期轮换,选定后再集成)
    "blueprint": ("草稿C", "Blueprint 工程蓝图"),
    "deco": ("草稿E", "Art Deco 装饰艺术"),
}

# 草稿风格列表:仅用于 site/styles/ 预览页与 --style 强制指定,不参与星期自动轮换
EXTRA_PREVIEW_STYLES = ["blueprint", "deco"]

CATEGORY_ORDER = ["AI", "科技硬件", "金融", "政策", "商业", "国际", "其他"]
TOP_LIMIT = 6


def style_for_date(date_str):
    """把日期字符串映射到星期样式名,解析失败时回退 mon。"""
    try:
        return WEEKDAY_STYLES[datetime.strptime(str(date_str), "%Y-%m-%d").weekday()]
    except (ValueError, TypeError):
        return WEEKDAY_STYLES[0]


def copy_styles():
    """把 assets/styles/ 复制到 site/assets/styles/(内容变化才覆写)。"""
    STYLES_DEST_DIR.mkdir(parents=True, exist_ok=True)
    for src in sorted(STYLES_SRC_DIR.glob("*")):
        if not src.is_file():
            continue
        dst = STYLES_DEST_DIR / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size \
                or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)


def script_tag(prefix=""):
    """返回共享脚本引用标签。"""
    return f'<script src="{prefix}assets/styles/site.js" defer></script>'


def esc(text):
    return html.escape(str(text or ""))


def fmt_time(iso):
    """把 ISO 时间格式化为 HH:MM;失败时原样返回。"""
    try:
        dt = datetime.fromisoformat(str(iso))
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return str(iso or "")


def _all_items_of(date):
    """读取某天 processed_{date}.json 的 item 列表;缺失/出错返回 []。"""
    path = config.CONTENT_DIR / f"processed_{date}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("items") or []


def load_curated(date):
    """读取 content/curated_{date}.json,返回有序条目列表(含全字段)。

    文件为 [{link, index}, ...] 形式(来自策划页导出);按 link 从当天 items[]
    取回完整条目;找不到 link 时回退按 index 匹配。文件缺失返回 []。
    """
    path = config.CONTENT_DIR / f"curated_{date}.json"
    if not path.exists():
        return []
    try:
        sel = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    by_link = {}
    by_index = {}
    for it in _all_items_of(date):
        if it.get("link"):
            by_link[it["link"]] = it
        by_index[it.get("index")] = it
    out = []
    for s in sel:
        link = s.get("link")
        it = by_link.get(link) or by_index.get(s.get("index"))
        if it:
            out.append(it)
    return out


def load_all_days():
    """读取 content/ 下全部 processed_*.json,按日期倒序返回。"""
    days = []
    for f in sorted(config.CONTENT_DIR.glob("processed_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[site] 跳过 {f.name}: {e}")
            continue
        day = data.get("date") or f.stem.split("_")[-1]
        items = data.get("items") or []
        top = data.get("daily_top") or []
        ai = data.get("ai_top") or []
        days.append({
            "date": day,
            "file": f.name,
            "items": items,
            "top": top,
            "ai": ai,
        })
    days.sort(key=lambda d: d["date"], reverse=True)
    return days


def render_items(items, featured=False):
    """渲染资讯卡片列表 HTML。"""
    parts = []
    for i, it in enumerate(items, 1):
        score = it.get("score", 0)
        voice = it.get("voice_script", "")
        voice_html = f'<p class="voice">{esc(voice)}</p>' if featured and voice else ""
        rank_html = f'<span class="rank">#{i:02d}</span>' if featured else ""
        d = i % 8
        parts.append(f"""
      <article class="card{' featured' if featured else ''} anim anim-scale d{d}">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
          <span class="tag">{esc(it.get('category', '其他'))}</span>
          {rank_html}
        </div>
        <h3><a href="{esc(it.get('link', '#'))}" target="_blank" rel="noopener">{esc(it.get('title'))}</a></h3>
        <p class="summary">{esc(it.get('summary', ''))}</p>
        {voice_html}
        <div class="meta">
          <span class="src">{esc(it.get('source', ''))}</span>
          <span style="display:inline-flex;gap:12px">
            <span class="time">{fmt_time(it.get('published'))}</span>
            <span class="score">★ {score:.1f}</span>
          </span>
        </div>
      </article>""")
    return "\n".join(parts)


def render_day_html(day, style):
    """渲染单个日期的完整 HTML 页面(仅最重要 TOP_LIMIT 条)。"""
    date = day["date"]
    top = list(day["top"])[:TOP_LIMIT]

    num_sources = len({it.get("source") for it in top})
    top_html = render_items(top, featured=True) if top else "    <p class=\"sub\">当日暂无精选。</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日快讯 · {date} · AI 科技资讯精选</title>
<meta name="description" content="{date} 每日 AI / 科技快讯精选 TOP {len(top)}。">
<link rel="stylesheet" href="../assets/styles/{style}.css">
</head>
<body>
<header class="hero">
  <div class="wrap">
    <span class="badge hero-badge">今日精选</span>
    <h1 class="site-title hero-title">{date}</h1>
    <p class="sub hero-sub">AI · 科技资讯精选</p>
    <div class="hero-stats">
      <span class="stat"><b data-target="{len(top)}">{len(top)}</b> 条精选</span>
      <span class="stat"><b data-target="{num_sources}">{num_sources}</b> 个来源</span>
    </div>
  </div>
</header>
<div class="wrap">
  <h2 class="section anim" id="featured">今日精选 <span class="count">({len(top)})</span></h2>
  <div class="featured-row">
{top_html}
  </div>
  <a class="back" href="../index.html">← 返回首页</a>
  <footer>每日快讯 · 自动生成于 {datetime.now(config.CST).strftime('%Y-%m-%d %H:%M')}<span class="sep">·</span>本地静态站</footer>
</div>
{script_tag("../")}
</body>
</html>
"""


def render_ai_items(items, date=None):
    """渲染 AI 板块卡片(带评分布与单条详情页链接)。"""
    parts = []
    for i, it in enumerate(items, 1):
        score = it.get("score", 0)
        voice = it.get("voice_script", "")
        voice_html = f'<p class="voice">{esc(voice)}</p>' if voice else ""
        d = i % 8
        detail_link = ""
        if date:
            detail_link = (f'<a class="detail-link" href="news/{date}_{i:02d}.html" '
                           f'style="color:var(--accent);font-weight:700;text-decoration:none">'
                           f"详情 →</a>")
        parts.append(f"""
      <article class="card featured anim anim-scale d{d}">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
          <span class="mark">DAILY · AI</span>
          <span class="rank">#{i:02d}</span>
        </div>
        <h3><a href="{esc(it.get('link', '#'))}" target="_blank" rel="noopener">{esc(it.get('title'))}</a></h3>
        <p class="summary">{esc(it.get('summary', ''))}</p>
        {voice_html}
        <div class="meta">
          <span class="src">{esc(it.get('source', ''))}</span>
          <span style="display:inline-flex;gap:12px">
            <span class="time">{fmt_time(it.get('published'))}</span>
            <span class="score">★ {score:.1f}</span>
            {detail_link}
          </span>
        </div>
      </article>""")
    return "\n".join(parts)


def render_ai_html(day, style):
    """渲染 AI 每日速递页(AI TOP 10)。"""
    date = day["date"]
    ai = day.get("ai") or [it for it in day["items"] if it.get("category") == "AI"]
    ai = list(ai)[:10]
    avg_score = sum(it.get("score", 0) for it in ai) / len(ai) if ai else 0

    ai_html = render_ai_items(ai, date) if ai else "    <p class=\"sub\">当日暂无 AI 消息。</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 每日速递 · {date}</title>
<meta name="description" content="{date} AI 行业要闻 TOP {len(ai)}。">
<link rel="stylesheet" href="assets/styles/{style}.css">
</head>
<body class="ai">
<header class="hero">
  <div class="wrap">
    <span class="badge hero-badge">AI 板块</span>
    <h1 class="site-title hero-title">AI 每日速递</h1>
    <p class="sub hero-sub">{date} · 大模型 · 智能体 · 前沿突破</p>
    <div class="hero-stats">
      <span class="stat"><b data-target="{len(ai)}">{len(ai)}</b> 条要闻</span>
      <span class="stat"><b data-target="{avg_score:.1f}">{avg_score:.1f}</b> 平均关注度</span>
      <span class="stat"><b data-target="{len({it.get('source') for it in ai})}">{len({it.get('source') for it in ai})}</b> 个来源</span>
    </div>
  </div>
</header>
<div class="wrap">
  <h2 class="section anim" id="top">今日 AI TOP <span class="count">({len(ai)})</span></h2>
  <div class="featured-row">
{ai_html}
  </div>
  <a class="back" href="index.html">← 返回首页</a>
  <footer>AI 每日速递 · 本地静态站<span class="sep">·</span>生成于 {datetime.now(config.CST).strftime('%Y-%m-%d %H:%M')}</footer>
</div>
{script_tag()}
</body>
</html>
"""


def render_index_html(days, style):
    """渲染首页:最新一期最重要的 TOP_LIMIT 条 + 往期归档。"""
    latest = days[0]
    date = latest["date"]
    top = list(latest["top"])[:TOP_LIMIT]

    archive_links = []
    for d in days[1:]:
        archive_links.append(
            f"""      <a href="daily/{d['date']}.html"><span>{d['date']}</span><span class="count">{len(d['top']) or len(d['items'])} 条精选</span></a>"""
        )
    archive_html = "\n".join(archive_links) if archive_links else '    <p class="sub">暂无往期。</p>'

    num_sources = len({it.get("source") for it in top})
    top_html = render_items(top, featured=True) if top else "    <p class=\"sub\">当日暂无精选。</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日快讯 · AI 科技资讯聚合</title>
<meta name="description" content="{date} 每日 AI / 科技快讯,今日精选 TOP {len(top)}。">
<link rel="stylesheet" href="assets/styles/{style}.css">
</head>
<body>
<header class="hero">
  <div class="wrap">
    <span class="badge hero-badge">每日精选</span>
    <h1 class="site-title hero-title">今日科技要闻</h1>
    <p class="sub hero-sub">{date} · 每日 AI / 科技要闻精选</p>
    <div class="hero-stats">
      <span class="stat"><b data-target="{len(top)}">{len(top)}</b> 条精选</span>
      <span class="stat"><b data-target="{num_sources}">{num_sources}</b> 个来源</span>
    </div>
  </div>
</header>
<div class="wrap">
  <h2 class="section anim" id="featured">今日精选 <span class="count">({len(top)})</span></h2>
  <div class="featured-row">
{top_html}
  </div>
  <div class="ai-link-wrap anim anim-fade-left">
    <a class="ai-link" href="ai.html">AI 每日速递 · {date} <span class="n">→</span></a>
  </div>
  <h2 class="section anim">往期归档</h2>
  <div class="archive anim anim-fade-right">
{archive_html}
  </div>
  <footer>每日快讯 · 本地静态站<span class="sep">·</span>生成于 {datetime.now(config.CST).strftime('%Y-%m-%d %H:%M')}</footer>
</div>
{script_tag()}
</body>
</html>
"""


CURATED_CSS = """
.curate-toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;
  justify-content:space-between;margin:0 0 20px;padding:16px 18px;
  border:1px solid var(--line);border-radius:12px;background:var(--surface)}
.curate-toolbar .cu-info{font-size:15px;color:var(--muted);line-height:1.6}
.curate-toolbar b{color:var(--text)}
.btn{font:inherit;font-size:15px;padding:10px 18px;border-radius:10px;cursor:pointer;
  border:1px solid var(--line);background:var(--surface);color:var(--text);transition:.2s}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-text,#fff)}
.btn.primary:hover{filter:brightness(1.08)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.curate-hint{font-size:13px;color:var(--muted);margin:0 0 14px}
#curate-item{display:flex;gap:14px;align-items:flex-start;padding:16px 18px;
  border:1px solid var(--line);border-radius:12px;background:var(--surface);
  cursor:pointer;position:relative;transition:box-shadow .2s,border-color .2s;user-select:none}
#curate-item+.picker-item{margin-top:10px}
#curate-item:hover{border-color:var(--accent)}
#curate-item.picked{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
#curate-item .c-ctrl{display:flex;flex-direction:column;gap:4px;align-items:center;min-width:34px}
#curate-item .c-ctrl button{font:inherit;font-size:14px;line-height:1;width:30px;height:30px;
  border:1px solid var(--line);border-radius:8px;background:var(--surface);cursor:pointer;color:var(--muted)}
#curate-item .c-ctrl button:hover{color:var(--text);border-color:var(--text)}
#curate-item input.c-check{width:20px;height:20px;accent-color:var(--accent);cursor:pointer;margin-top:8px}
#curate-item .c-body{flex:1;min-width:0}
#curate-item .c-cat{color:var(--accent);font-size:13px;font-weight:600}
#curate-item .c-cat::before{content:"#";opacity:.7}
#curate-item h3{font-size:18px;line-height:1.5;margin:6px 0 8px;font-weight:700}
#curate-item h3 a{color:var(--text);text-decoration:none}
#curate-item h3 a:hover{text-decoration:underline}
#curate-item .c-summary{font-size:14px;color:var(--muted);line-height:1.7;margin:0 0 8px}
#curate-item .c-voice{font-size:13px;color:var(--muted);line-height:1.6;
  padding:8px 12px;border-left:3px solid var(--accent);background:var(--bg);
  border-radius:0 8px 8px 0;margin:0 0 8px}
#curate-item .c-meta{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--muted)}
#curate-item .c-score{color:var(--accent);font-weight:600}
.picker-item+.picker-item{margin-top:10px}
.curate-filter{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}
.curate-filter .cf-btn{font:inherit;font-size:14px;line-height:1;padding:8px 14px;border-radius:999px;
  border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer;transition:.2s}
.curate-filter .cf-btn:hover{border-color:var(--accent);color:var(--text)}
.curate-filter .cf-btn.on{background:var(--accent);border-color:var(--accent);color:var(--accent-text,#fff)}
.curate-filter .cf-btn .cf-count{font-size:12px;opacity:.8;margin-left:4px}
#curate-item.hide{display:none}
@media (max-width:720px){#curate-item{flex-direction:column}.c-ctrl{flex-direction:row}
  #curate-item .c-ctrl button{width:26px;height:26px}}
"""


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


def render_news_detail_html(day, item, rank, total, style):
    """渲染单条 AI 快讯详情页 site/news/{date}_{rank:02d}.html。

    复用七套风格 CSS 的 tokens 与 hero/卡片契约;额外注入 DETAIL_CSS
    (单卡排版 + riseIn 入场动画,纯 CSS 自动播放,不依赖 JS)。
    hero 元素的 heroFadeSlide/badgePulse 动画由风格 CSS 自带,自动播放。
    """
    date = day["date"]
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · AI 快讯详情 · {date}</title>
<meta name="description" content="{date} AI 快讯 #{rank:02d}:{esc(title)}">
<link rel="stylesheet" href="../assets/styles/{style}.css">
<style>{DETAIL_CSS}</style>
</head>
<body class="ai">
<header class="hero detail-hero">
  <div class="wrap">
    <span class="badge hero-badge">AI 快讯 · {rank:02d}/{total:02d}</span>
    <h1 class="site-title hero-title">每日AI快讯</h1>
    <p class="sub hero-sub">{date} · {esc(category)}</p>
    <div class="hero-stats">
      <span class="stat"><b data-target="{score:.1f}">{score:.1f}</b> 关注度</span>
      <span class="stat"><b>{esc(source)}</b> 来源</span>
    </div>
  </div>
</header>
<div class="wrap">
  <h2 class="section">快讯详情 <span class="count">#{rank:02d}</span></h2>
  <article class="card featured detail-card detail-anim da1">
    <h3 class="detail-title"><a href="{esc(link)}" target="_blank" rel="noopener">{esc(title)}</a></h3>
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
  <a class="back" href="../ai.html">← 返回 AI 每日速递</a>
  <footer>每日AI快讯 · 单条详情<span class="sep">·</span>生成于 {datetime.now(config.CST).strftime('%Y-%m-%d %H:%M')}</footer>
</div>
{script_tag("../")}
</body>
</html>
"""


def render_curated_html(day, style):
    """渲染策划编辑页 site/curated.html:全类别条目,可勾选排序并导出 JSON。

    若已有 content/curated_{date}.json,预先勾选其中条目。
    """
    date = day["date"]
    items = day["items"]
    curated = load_curated(date)
    selected_links = {it["link"] for it in curated} if curated else set()

    # 板块过滤栏:按分类顺序展示实际出现的板块(含计数),另加"全部"
    present_cats = [c for c in CATEGORY_ORDER if any(it.get("category", "其他") == c for it in items)]
    extra_cats = sorted({it.get("category", "其他") for it in items} - set(CATEGORY_ORDER))
    present_cats += extra_cats

    parts = []
    for i, it in enumerate(items):
        link = it.get("link", "")
        title = it.get("title", "")
        category = it.get("category", "其他")
        score = it.get("score", 0)
        summary = it.get("summary", "")
        source = it.get("source", "")
        time = fmt_time(it.get("published"))
        voice = it.get("voice_script", "")
        picked = ' picked' if link and link in selected_links else ''
        voice_html = f'<div class="c-voice">🎙 {esc(voice)}</div>' if voice else ''
        parts.append(f"""
      <article id="curate-item" class="picker-item{picked}" data-link="{esc(link)}" data-index="{i}" data-category="{esc(category)}">
        <div class="c-ctrl">
          <button class="c-top" type="button" title="置顶">⏫</button>
          <button class="c-up" type="button" title="上移">↑</button>
          <button class="c-down" type="button" title="下移">↓</button>
          <button class="c-bottom" type="button" title="置底">⏬</button>
          <input type="checkbox" class="c-check" title="勾选播报">
        </div>
        <div class="c-body">
          <div><span class="c-cat">{esc(category)}</span></div>
          <h3><a href="{esc(link)}" target="_blank" rel="noopener">{esc(title)}</a></h3>
          <p class="c-summary">{esc(summary)}</p>
          {voice_html}
          <div class="c-meta">
            <span>{esc(source)}</span><span>{esc(time)}</span>
            <span class="c-score">★ {score:.1f}</span>
          </div>
        </div>
      </article>""")
    items_html = "\n".join(parts)
    filter_html = "".join(
        f'<button type="button" class="cf-btn" data-cat="{esc(c)}">{esc(c)}'
        f'<span class="cf-count">{sum(1 for it in items if it.get("category", "其他") == c)}</span></button>'
        for c in present_cats)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>播报策划 · {date}</title>
<meta name="description" content="{date} 播报内容勾选与排序。">
<link rel="stylesheet" href="assets/styles/{style}.css">
<style>{CURATED_CSS}</style>
</head>
<body data-curate-date="{date}">
<header class="hero">
  <div class="wrap">
    <span class="badge hero-badge">播报策划</span>
    <h1 class="site-title hero-title">今日播报策划</h1>
    <p class="sub hero-sub">{date} · 勾选并排序要播报的内容</p>
    <div class="hero-stats">
      <span class="stat"><b data-target="{len(items)}">{len(items)}</b> 条待选</span>
      <span class="stat"><b id="curate-stats" data-target="0">0</b> 已勾选</span>
    </div>
  </div>
</header>
<div class="wrap">
  <div class="curate-toolbar">
    <div class="cu-info">勾选要播报的条目,可用 <b>↑/↓/置顶/置底</b> 调整顺序;最后点击右上角 <b>导出 JSON</b> 下载到本地。</div>
    <button id="curate-export" class="btn primary" type="button" disabled>导出 JSON</button>
  </div>
  <p class="curate-hint">导出后请把 <code>curated_{date}.json</code> 放到 <code>content/</code> 目录,再运行 <code>video_builder.py --date {date} --curated</code>。</p>
  <div class="curate-filter" id="curate-filter">
    <button type="button" class="cf-btn on" data-cat="__all">全部<span class="cf-count">{len(items)}</span></button>
    {filter_html}
  </div>
  <div id="curate-list">
{items_html}
  </div>
  <a class="back" href="index.html">← 返回首页</a>
  <footer>播报策划 · 本地静态站<span class="sep">·</span>生成于 {datetime.now(config.CST).strftime('%Y-%m-%d %H:%M')}</footer>
</div>
{script_tag()}
<script src="assets/styles/curated.js" defer></script>
</body>
</html>
"""


def render_preview_html(days, style):
    """渲染单风格预览页(最新一期内容 × 指定风格),输出到 site/styles/。"""
    latest = days[0]
    date = latest["date"]
    top = list(latest["top"])[:TOP_LIMIT]
    weekday, name = STYLE_NAMES.get(style, ("", style))
    num_sources = len({it.get("source") for it in top})
    top_html = render_items(top, featured=True) if top else "    <p class=\"sub\">当日暂无精选。</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} · 风格预览 · {date}</title>
<meta name="description" content="{weekday}风格 {name} 预览页,内容为 {date} 快讯精选。">
<link rel="stylesheet" href="../assets/styles/{style}.css">
</head>
<body>
<header class="hero">
  <div class="wrap">
    <span class="badge hero-badge">风格预览{(' · ' + weekday) if weekday else ''}</span>
    <h1 class="site-title hero-title">{esc(name)}</h1>
    <p class="sub hero-sub">{date} · 样式文件 assets/styles/{style}.css</p>
    <div class="hero-stats">
      <span class="stat"><b data-target="{len(top)}">{len(top)}</b> 条精选</span>
      <span class="stat"><b data-target="{num_sources}">{num_sources}</b> 个来源</span>
    </div>
  </div>
</header>
<div class="wrap">
  <h2 class="section anim" id="featured">今日精选 <span class="count">({len(top)})</span></h2>
  <div class="featured-row">
{top_html}
  </div>
  <a class="back" href="../index.html">← 返回首页</a>
  <footer>{name} · 星期风格轮换预览<span class="sep">·</span>生成于 {datetime.now(config.CST).strftime('%Y-%m-%d %H:%M')}</footer>
</div>
{script_tag("../")}
</body>
</html>
"""


def generate_style_previews(days):
    """生成风格预览页 site/styles/{mon..sun}.html + 草稿风格预览。"""
    preview_dir = config.SITE_DIR / "styles"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for s in dict.fromkeys([*WEEKDAY_STYLES, *EXTRA_PREVIEW_STYLES]):
        page = preview_dir / f"{s}.html"
        page.write_text(render_preview_html(days, s), encoding="utf-8")
        print(f"[site] 已生成 {page.relative_to(config.SITE_DIR)}(风格 {s} · {STYLE_NAMES[s][1]})")


def generate(force_style=None):
    days = load_all_days()
    if not days:
        print("[site] content/ 下没有 processed_*.json,无法生成。")
        return 1

    copy_styles()
    legacy_css = SITE_ASSET_DIR / "style.css"
    if legacy_css.exists():
        legacy_css.unlink()
        print("[site] 已移除旧版 assets/style.css")
    (config.SITE_DIR / "daily").mkdir(parents=True, exist_ok=True)
    news_dir = config.SITE_DIR / "news"
    news_dir.mkdir(parents=True, exist_ok=True)

    index_style = ai_style = force_style or style_for_date(days[0]["date"])
    (config.SITE_DIR / "index.html").write_text(
        render_index_html(days, index_style), encoding="utf-8")
    print(f"[site] 已生成 index.html(最新 {days[0]['date']} · 风格 {index_style}, "
          f"{len(days[0]['items'])} 条)")

    (config.SITE_DIR / "ai.html").write_text(
        render_ai_html(days[0], ai_style), encoding="utf-8")
    print(f"[site] 已生成 ai.html({len(days[0].get('ai', []))} 条 AI 要闻 · 风格 {ai_style})")

    (config.SITE_DIR / "curated.html").write_text(
        render_curated_html(days[0], ai_style), encoding="utf-8")
    print(f"[site] 已生成 curated.html({len(days[0].get('items', []))} 条待选 · 风格 {ai_style})")

    for d in days:
        page = config.SITE_DIR / "daily" / f"{d['date']}.html"
        page_style = force_style or style_for_date(d["date"])
        page.write_text(render_day_html(d, page_style), encoding="utf-8")
        print(f"[site] 已生成 {page.relative_to(config.SITE_DIR)} ({len(d['items'])} 条 · 风格 {page_style})")

        # 单条 AI 快讯详情页(与 ai.html TOP 列表同序,详情链接按 rank 对应)
        ai_items = list(d.get("ai") or [])[:10]
        for rank, it in enumerate(ai_items, 1):
            news_page = news_dir / f"{d['date']}_{rank:02d}.html"
            news_page.write_text(
                render_news_detail_html(d, it, rank, len(ai_items), page_style),
                encoding="utf-8")
        if ai_items:
            print(f"[site] 已生成 {len(ai_items)} 条 AI 快讯详情页 ({d['date']})")

    generate_style_previews(days)

    print(f"[site] 完成,输出目录: {config.SITE_DIR}")
    return 0


def clean_extra_pages():
    """删除 daily/ 与 news/ 下已不在 processed 数据里的旧页面。"""
    days = load_all_days()
    valid = {d["date"] for d in days}
    daily_dir = config.SITE_DIR / "daily"
    if not daily_dir.exists():
        daily_dir = None
    if daily_dir:
        for f in daily_dir.glob("*.html"):
            if f.stem not in valid:
                f.unlink()
                print(f"[site] 清理过期页面 {f.name}")
    news_dir = config.SITE_DIR / "news"
    if news_dir.exists():
        valid_news = set()
        for d in days:
            n = min(len(d.get("ai") or []), 10)
            for r in range(1, n + 1):
                valid_news.add(f"{d['date']}_{r:02d}")
        for f in news_dir.glob("*.html"):
            if f.stem not in valid_news:
                f.unlink()
                print(f"[site] 清理过期详情页 {f.name}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="生成每日快讯静态站")
    ap.add_argument("--clean", action="store_true", help="清理过期页面后生成")
    ap.add_argument("--style", choices=WEEKDAY_STYLES + EXTRA_PREVIEW_STYLES, default=None,
                    help="强制全部页面使用指定风格(默认按内容日期星期自动轮换)")
    args = ap.parse_args()
    if args.clean:
        clean_extra_pages()
    return generate(force_style=args.style)


if __name__ == "__main__":
    sys.exit(main())
