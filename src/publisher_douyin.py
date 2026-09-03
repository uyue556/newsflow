"""抖音发布器:Playwright RPA 模拟人工在创作者后台发布视频。

发布流程(半自动):
1. 首次使用先手动登录一次(保留登录态):
       python src/publisher_douyin.py --login
   会打开真实浏览器(需 WSLg/桌面环境),扫码/短信登录后关闭即可,
   登录态保存在 DOUYIN_PROFILE 目录,后续自动复用。
2. run_daily.sh 生成视频后调用 --make-ticket 生成待审工单(platform=douyin)
3. 人工审核工单,可修改 title/desc/tags
4. 本脚本打开浏览器自动上传+填写,人工在终端确认后才点击"发布":
       python src/publisher_douyin.py --ticket publish_queue/2026-08-13-douyin.json
   成功后 status=done 并记录链接。

用法:
    python src/publisher_douyin.py --login
    python src/publisher_douyin.py --make-ticket --date 2026-08-13
    python src/publisher_douyin.py --list
    python src/publisher_douyin.py --ticket <path> [--headless]
    python src/publisher_douyin.py --date 2026-08-13 [--dry-run]

注意:
- 需要 GUI 显示(默认 headful)。WSL2 请确认 WSLg 可用;远程环境请 --headless。
- 抖音后台 DOM 会变,若选择器失效请调整下方 *_SELECTOR 常量并重试。
- 抖音风控严格:请控制发布频率,避免同一账号高频连续发布。
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import config
from playwright.sync_api import sync_playwright

# ---------- 常量 ----------
# 登录态目录(放 Linux 原生盘,避免 /mnt/c 慢 & 权限问题)
DOUYIN_PROFILE = Path.home() / "newsflow-douyin-profile"
DOUYIN_HOME_URL = "https://creator.douyin.com/"
# 作品上传页
DOUYIN_UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"

# 默认标签(抖音话题会与简介文本一起写入)
DEFAULT_TAGS = ["AI", "每日快讯", "科技", "资讯"]

# 选择器(抖音 DOM 变更时改这里)
SEL_VIDEO_INPUT = 'input[type="file"]'          # 上传文件 input(取第一个)
SEL_DESC_EDIT = 'div[contenteditable="true"]'   # 作品简介输入区
SEL_ADD_TOPIC = "添加话题"                       # 话题按钮文案
SEL_PUBLISH_BTN = "发布"                        # 发布按钮文案(取第一个)
SEL_AI_DECLARE = "AI生成"                        # AI 内容声明文案(尽力勾选)

UPLOAD_TIMEOUT = 600   # 等待视频上传完成(秒)
ACTION_TIMEOUT = 30    # 常规操作超时(秒)
CONFIRM_PUBLISH = True # 发布前是否需要人工确认

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("publisher_douyin")


# ---------- 工单 ----------

def ticket_path(date_str) -> Path:
    return config.QUEUE_DIR / f"{date_str}-douyin.json"


def make_ticket(date_str):
    processed = config.CONTENT_DIR / f"processed_{date_str}.json"
    if not processed.exists():
        log.error("未找到 %s,请先运行 ai_processor", processed)
        sys.exit(1)
    data = json.loads(processed.read_text(encoding="utf-8"))
    top = data.get("daily_top", [])
    if not top:
        log.error("processed_%s.json 没有 daily_top", date_str)
        sys.exit(1)

    video = config.VIDEO_DIR / f"{date_str}_vertical.mp4"
    if not video.exists():
        log.error("未找到视频 %s,请先运行 video_builder", video)
        sys.exit(1)

    lines = [f"{i + 1}. {it.get('title', '')}" for i, it in enumerate(top)]
    desc = "\n".join(lines) + "\n" + " ".join("#" + t for t in DEFAULT_TAGS)
    title = f"{date_str} AI科技每日快讯 · 精选{len(top)}条"

    ticket = {
        "id": f"{date_str}-douyin",
        "date": date_str,
        "platform": "douyin",
        "status": "pending",
        "created_at": datetime.now(config.CST).strftime("%Y-%m-%d %H:%M:%S"),
        "video": str(video),
        "cover": "",
        "title": title,
        "desc": desc,
        "tags": DEFAULT_TAGS,
        "result": {},
    }
    out = ticket_path(date_str)
    out.write_text(json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("已生成工单 %s (status=pending, 请人工审核后发布)", out)
    return out


def list_tickets():
    if not config.QUEUE_DIR.exists():
        return
    for p in sorted(config.QUEUE_DIR.glob("*-douyin.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        print(f"{p.name:<40} status={t.get('status','?'):<10} title={t.get('title','')}")


def load_ticket(path):
    if not Path(path).exists():
        log.error("工单不存在: %s", path)
        sys.exit(1)
    t = json.loads(Path(path).read_text(encoding="utf-8"))
    if t.get("platform") != "douyin":
        log.error("工单平台不是 douyin: %s", path)
        sys.exit(1)
    if t.get("status") != "pending":
        log.error("工单状态为 %s,跳过(避免重复发布)", t.get("status"))
        sys.exit(1)
    return t


def save_ticket(t):
    p = ticket_path(t["date"])
    p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("工单已更新: %s", p)


# ---------- 浏览器 ----------

def launch(playwright, headless=False):
    """启动带持久登录态的浏览器。"""
    DOUYIN_PROFILE.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(DOUYIN_PROFILE),
        headless=headless,
        viewport={"width": 1366, "height": 900},
        user_agent=config.USER_AGENT,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
        locale="zh-CN",
    )
    page = context.new_page()
    page.set_default_timeout(ACTION_TIMEOUT * 1000)
    return context, page


def screenshot(page, name="douyin_debug"):
    p = config.LOG_DIR / f"{name}.png"
    try:
        page.screenshot(path=str(p), full_page=False)
        log.info("已保存截图: %s", p)
    except Exception as e:  # noqa: BLE001
        log.warning("截图失败: %s", e)


def ensure_logged_in(page):
    """访问上传页并确认已登录,否则提示先 --login。"""
    log.info("打开创作者上传页: %s", DOUYIN_UPLOAD_URL)
    try:
        page.goto(DOUYIN_UPLOAD_URL, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=60_000)
    except Exception as e:  # noqa: BLE001
        log.warning("页面加载等待超时,继续尝试: %s", e)
    url = page.url
    if "login" in url or "passport" in url or "not-login" in url:
        screenshot(page, "douyin_not_logged_in")
        log.error("未登录抖音,请先运行: python src/publisher_douyin.py --login")
        sys.exit(1)
    log.info("已登录,当前页面: %s", url)


def upload_video(page, video_path):
    """选择视频文件并等待上传完成。"""
    if not Path(video_path).exists():
        log.error("视频文件不存在: %s", video_path)
        sys.exit(1)
    log.info("选择视频文件: %s", video_path)
    page.locator(SEL_VIDEO_INPUT).first.set_input_files(str(video_path))
    log.info("等待上传完成(最长 %ds)...", UPLOAD_TIMEOUT)
    t0 = time.time()
    while time.time() - t0 < UPLOAD_TIMEOUT:
        if page.is_visible("text=上传完成", timeout=3000):
            log.info("检测到「上传完成」")
            break
        # 部分版本无文案,改为探测发布按钮是否可点
        btn = page.get_by_text(SEL_PUBLISH_BTN, exact=True).first
        if btn.count() and not btn.is_disabled():
            time.sleep(2)
            break
        time.sleep(3)
    else:
        screenshot(page, "douyin_upload_timeout")
        log.error("视频上传超时(%ds),请手动检查", UPLOAD_TIMEOUT)
        sys.exit(1)


def fill_desc(page, desc):
    """填写作品简介 + 话题。"""
    editor = page.locator(SEL_DESC_EDIT).first
    try:
        editor.click(timeout=10_000)
        editor.fill("")
        page.keyboard.type(desc, delay=5)
    except Exception as e:  # noqa: BLE001
        log.warning("contenteditable 填写失败,尝试其他方式: %s", e)
        try:
            page.evaluate(
                """(text) => {
                    const el = document.querySelector('[contenteditable="true"]');
                    if (el) { el.innerText = text; el.dispatchEvent(new Event('input', {bubbles:true})); }
                }""",
                desc,
            )
        except Exception as e2:  # noqa: BLE001
            screenshot(page, "douyin_fill_desc_failed")
            log.error("填写简介失败: %s", e2)
            sys.exit(1)
    log.info("简介已填写")


def declare_ai(page):
    """尽力勾选「AI 生成内容声明」,找不到则跳过。"""
    for _ in range(2):
        candidates = page.get_by_text(re.compile(SEL_AI_DECLARE))
        count = candidates.count()
        if not count:
            log.info("未发现 AI 生成声明,跳过")
            return
        # 找到后可点击的元素(复选框/开关/文字)
        for el in page.locator("label, [role='checkbox'], [class*='switch'], [class*='checkbox']").all():
            try:
                if SEL_AI_DECLARE in (el.inner_text() or ""):
                    el.click(timeout=3000)
                    log.info("已勾选 AI 生成声明")
                    return
            except Exception:  # noqa: BLE001
                continue
        break


def confirm_and_publish(page, t):
    if CONFIRM_PUBLISH:
        print("\n========== 请人工审核 ==========")
        print(f"标题: {t['title']}")
        print(f"简介:\n{t['desc']}")
        ans = input("确认无误后按回车发布;输入 n 取消 > ").strip().lower()
        if ans in ("n", "no", "取消"):
            log.info("用户取消发布")
            sys.exit(0)
    log.info("点击发布按钮...")
    btn = page.get_by_text(SEL_PUBLISH_BTN, exact=True).first
    btn.click(timeout=30_000)
    # 等待发布结果
    t0 = time.time()
    success = False
    while time.time() - t0 < 60:
        text = ""
        try:
            text = page.locator("body").inner_text(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        if "发布成功" in text or "作品发布成功" in text:
            success = True
            break
        if "审核中" in text or "内容管理" in page.url:
            success = True
            break
        time.sleep(2)
    if success:
        screenshot(page, "douyin_publish_success")
        log.info("发布成功!")
        return page.url
    screenshot(page, "douyin_publish_unclear")
    log.warning("发布结果未能确认,请在浏览器中检查(截图已保存)")
    return page.url


def do_login():
    """手动登录,保留登录态。"""
    with sync_playwright() as p:
        context, page = launch(p, headless=False)
        try:
            page.goto(DOUYIN_HOME_URL, timeout=60_000, wait_until="domcontentloaded")
            log.info("浏览器已打开,请扫码/短信登录抖音创作者后台...")
            screenshot(page, "douyin_login")
            input("登录完成后回到终端按回车退出 > ")
        finally:
            context.close()
    log.info("登录态已保存到 %s", DOUYIN_PROFILE)


def do_publish(t, headless=False):
    with sync_playwright() as p:
        context, page = launch(p, headless=headless)
        try:
            ensure_logged_in(page)
            upload_video(page, t["video"])
            fill_desc(page, t["desc"])
            declare_ai(page)
            url = confirm_and_publish(page, t)
            return url
        finally:
            context.close()


def main():
    ap = argparse.ArgumentParser(description="抖音视频发布器(Playwright RPA)")
    ap.add_argument("--login", action="store_true", help="手动登录一次,保留登录态")
    ap.add_argument("--make-ticket", action="store_true", help="生成待审工单(不发布)")
    ap.add_argument("--list", action="store_true", help="列出全部抖音工单")
    ap.add_argument("--ticket", help="指定工单文件路径")
    ap.add_argument("--date", help="日期 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="只校验不发布")
    ap.add_argument("--headless", action="store_true", help="无头模式(无GUI时)")
    args = ap.parse_args()

    if args.login:
        do_login()
        return

    if args.list:
        list_tickets()
        return

    if args.make_ticket:
        if not args.date:
            args.date = datetime.now(config.CST).strftime("%Y-%m-%d")
        make_ticket(args.date)
        return

    if args.ticket:
        t = load_ticket(args.ticket)
    elif args.date:
        p = ticket_path(args.date)
        if not p.exists():
            log.error("未找到工单 %s,请先 --make-ticket", p)
            sys.exit(1)
        t = load_ticket(p)
    else:
        ap.print_help()
        sys.exit(1)

    if args.dry_run:
        log.info("[dry-run] 视频=%s", t["video"])
        log.info("[dry-run] 标题=%s", t["title"])
        log.info("[dry-run] 简介=\n%s", t["desc"])
        log.info("[dry-run] 标签=%s", t.get("tags"))
        return

    url = do_publish(t, headless=args.headless)
    t["status"] = "done"
    t["result"] = {
        "url": url,
        "published_at": datetime.now(config.CST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_ticket(t)


if __name__ == "__main__":
    main()
