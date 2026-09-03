"""B站发布器:读取发布队列中的待审工单,用 bilibili-api + cookie 上传视频。

发布流程(半自动):
1. run_daily.sh 生成视频后调用 --make-ticket 生成待审工单(publish_queue/*.json, status=pending)
2. 人工审核工单内容,可手动修改 title/desc/tags/tid
3. 本脚本读取工单上传,成功后 status=done 并记录 bvid/aid

用法:
    python src/publisher_bilibili.py --make-ticket --date 2026-08-13
    python src/publisher_bilibili.py --list
    python src/publisher_bilibili.py --ticket publish_queue/2026-08-13-bilibili.json
    python src/publisher_bilibili.py --date 2026-08-13            # 自动找该日期 pending 工单
    python src/publisher_bilibili.py --date 2026-08-13 --dry-run  # 只校验不上传
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import config

from bilibili_api import Credential
from bilibili_api import sync
from bilibili_api.video_uploader import (
    VideoMeta,
    VideoUploader,
    VideoUploaderEvents,
    VideoUploaderPage,
)

# 默认分区:科技-数码
DEFAULT_TID = 130
# 默认标签(最多 10 个)
DEFAULT_TAGS = ["AI", "每日快讯", "科技", "资讯"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("publisher_bilibili")


# ---------- 工单 ----------

def ticket_path(date_str, platform="bilibili") -> Path:
    return config.QUEUE_DIR / f"{date_str}-{platform}.json"


def make_ticket(date_str):
    """根据已处理的精选内容生成待审工单(只生成,不发布)。"""
    processed = config.CONTENT_DIR / f"processed_{date_str}.json"
    if not processed.exists():
        log.error("未找到 %s,请先运行 ai_processor", processed)
        sys.exit(1)
    data = json.loads(processed.read_text(encoding="utf-8"))
    top = data.get("daily_top", [])
    if not top:
        log.error("processed_%s.json 没有 daily_top", date_str)
        sys.exit(1)

    video = config.VIDEO_DIR / f"{date_str}_horizontal.mp4"
    if not video.exists():
        log.error("未找到视频 %s,请先运行 video_builder", video)
        sys.exit(1)

    lines = [f"{i + 1}. {it.get('title', '')}" for i, it in enumerate(top)]
    desc = "\n".join(lines)
    title = f"{date_str} AI科技每日快讯 · 精选{len(top)}条"
    tags = list(DEFAULT_TAGS)

    ticket = {
        "id": f"{date_str}-bilibili",
        "date": date_str,
        "platform": "bilibili",
        "status": "pending",
        "created_at": datetime.now(config.CST).strftime("%Y-%m-%d %H:%M:%S"),
        "video": str(video),
        "cover": "",
        "title": title,
        "desc": desc,
        "tags": tags,
        "tid": DEFAULT_TID,
        "result": {},
    }
    out = ticket_path(date_str)
    out.write_text(json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("已生成工单 %s (status=pending, 请人工审核后发布)", out)
    return out


def list_tickets():
    if not config.QUEUE_DIR.exists():
        return
    for p in sorted(config.QUEUE_DIR.glob("*-bilibili.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        print(f"{p.name:<40} status={t.get('status','?'):<10} title={t.get('title','')}")


def load_ticket(path):
    if not Path(path).exists():
        log.error("工单不存在: %s", path)
        sys.exit(1)
    t = json.loads(Path(path).read_text(encoding="utf-8"))
    if t.get("platform") != "bilibili":
        log.error("工单平台不是 bilibili: %s", path)
        sys.exit(1)
    if t.get("status") != "pending":
        log.error("工单状态为 %s,跳过(避免重复发布)", t.get("status"))
        sys.exit(1)
    return t


def save_ticket(t):
    p = ticket_path(t["date"], "bilibili")
    p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("工单已更新: %s", p)


# ---------- 上传 ----------

def load_credential():
    sessdata = config.get_env("BILI_SESSDATA")
    bili_jct = config.get_env("BILI_BILI_JCT")
    buvid3 = config.get_env("BILI_BUVID3")
    missing = [n for n, v in [
        ("BILI_SESSDATA", sessdata),
        ("BILI_BILI_JCT", bili_jct),
    ] if not v]
    if missing:
        log.error("缺少 B站 cookie 环境变量: %s (写入 ~/.hermes/.env)", ", ".join(missing))
        log.error("从浏览器 F12 → Application → Cookies 复制,域名 bilibili.com")
        sys.exit(1)
    return Credential(
        sessdata=sessdata,
        bili_jct=bili_jct,
        buvid3=buvid3 or None,
    )


def _progress_cb(name, data):
    if name in (VideoUploaderEvents.PREUPLOAD.value,
                VideoUploaderEvents.PRE_PAGE.value,
                VideoUploaderEvents.AFTER_PAGE.value):
        page = (data.get("page") or {}).title if isinstance(data, dict) else data
        log.info("[上传] %s: %s", name, page or "")
    elif name == VideoUploaderEvents.PRE_CHUNK.value:
        log.info("[上传] 分块上传中: %.2f%%", data.get("progress", 0) * 100)
    elif name == VideoUploaderEvents.COMPLETED.value:
        log.info("[上传] 完成 %s", data)
    elif name in (VideoUploaderEvents.FAILED.value, VideoUploaderEvents.SUBMIT_FAILED.value,
                  VideoUploaderEvents.PREUPLOAD_FAILED.value):
        log.error("[上传] 失败 %s: %s", name, data.get("err") if isinstance(data, dict) else data)
    elif name in (VideoUploaderEvents.AFTER_COVER.value,
                  VideoUploaderEvents.AFTER_PAGE_SUBMIT.value,
                  VideoUploaderEvents.AFTER_SUBMIT.value):
        log.info("[上传] %s", name)


def upload_ticket(t):
    video_path = t["video"]
    if not Path(video_path).exists():
        log.error("视频文件不存在: %s", video_path)
        sys.exit(1)

    cred = load_credential()

    page = VideoUploaderPage(path=video_path, title=t.get("title", ""))
    meta = VideoMeta(
        tid=t.get("tid", DEFAULT_TID),
        title=t.get("title", ""),
        desc=t.get("desc", ""),
        cover=t.get("cover") or "",
        tags=t.get("tags") or DEFAULT_TAGS,
        original=True,
        no_reprint=True,
    )

    uploader = VideoUploader(pages=[page], meta=meta, credential=cred)
    uploader.add_event_listener(VideoUploaderEvents.PREUPLOAD.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.PRE_PAGE.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.AFTER_PAGE.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.PRE_CHUNK.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.COMPLETED.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.FAILED.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.SUBMIT_FAILED.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.PREUPLOAD_FAILED.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.AFTER_COVER.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.AFTER_PAGE_SUBMIT.value, _progress_cb)
    uploader.add_event_listener(VideoUploaderEvents.AFTER_SUBMIT.value, _progress_cb)

    log.info("开始上传到 B站: %s", video_path)
    log.info("标题: %s | 分区 tid=%s | 标签: %s", t["title"], meta.tid, ",".join(meta.tags))
    t0 = time.time()
    result = sync(uploader.start())
    elapsed = time.time() - t0

    bvid = result.get("bvid", "")
    aid = result.get("aid", 0)
    log.info("上传成功! bvid=%s aid=%s 耗时 %.0fs", bvid, aid, elapsed)
    log.info("稿件地址: https://www.bilibili.com/video/%s", bvid)
    return bvid, aid


def main():
    ap = argparse.ArgumentParser(description="B站视频发布器(读取待审工单上传)")
    ap.add_argument("--make-ticket", action="store_true", help="生成待审工单(不发布)")
    ap.add_argument("--list", action="store_true", help="列出全部 B站工单")
    ap.add_argument("--ticket", help="指定工单文件路径")
    ap.add_argument("--date", help="日期 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="只校验不实际上传")
    args = ap.parse_args()

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
        log.info("[dry-run] 标签=%s tid=%s", t.get("tags"), t.get("tid"))
        return

    bvid, aid = upload_ticket(t)
    t["status"] = "done"
    t["result"] = {"bvid": bvid, "aid": aid, "published_at": datetime.now(config.CST).strftime("%Y-%m-%d %H:%M:%S")}
    save_ticket(t)


if __name__ == "__main__":
    main()
