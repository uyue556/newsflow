"""新闻采集模块。

- RSS 源: 36氪 / IT之家 / 极客公园 / 知乎日报(可选 TechCrunch,需翻译)
- HTML 源: 华尔街见闻 global 列表页
输出: data/raw_YYYY-MM-DD.json  (采集时间窗口内, 每源最多 MAX_PER_SOURCE 条)
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import config

CST = config.CST
MAX_PER_SOURCE = 30


def safe_get(url, headers=None, timeout=config.REQUEST_TIMEOUT):
    """带超时和 UA 的 GET,失败抛异常。"""
    h = {"User-Agent": config.USER_AGENT}
    if headers:
        h.update(headers)
    resp = requests.get(url, headers=h, timeout=timeout)
    resp.raise_for_status()
    return resp


def clean_html(text):
    """去除 HTML 标签与多余空白。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_pub_time(entry):
    """从 feedparser 条目中提取发布时间,返回 UTC 或 None。"""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return time.struct_time(t)
            except Exception:
                continue
    return None


def collect_rss(source_name, feed_url, max_items=MAX_PER_SOURCE):
    """采集单个 RSS 源。"""
    items = []
    try:
        data = safe_get(feed_url).content
        feed = feedparser.parse(data)
    except Exception as exc:
        print(f"  [RSS] {source_name} 失败: {exc}")
        return items

    for entry in feed.entries[:max_items]:
        title = clean_html(entry.get("title", ""))
        link = entry.get("link", "")
        summary = clean_html(entry.get("summary", ""))
        if not title:
            continue
        pub = parse_pub_time(entry)
        items.append({
            "source": source_name,
            "title": title,
            "link": link,
            "summary": summary[:600],
            "published": time.strftime("%Y-%m-%dT%H:%M:%S", pub) if pub else None,
        })
    print(f"  [RSS] {source_name}: {len(items)} 条")
    return items


def _parse_wsn_anchors(html):
    """从华尔街见闻 HTML 中解析 (title, link, published) 列表。"""
    import bs4
    soup = bs4.BeautifulSoup(html, "html.parser")
    results = []
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"^/articles/\d+")):
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)
        h2 = a.find("h2")
        title = clean_html(h2.get_text(" ", strip=True)) if h2 else ""
        t = a.find("time", attrs={"datetime": True})
        results.append((title, "https://wallstreetcn.com" + href,
                        t["datetime"] if t and t.get("datetime") else None))
    return results


def collect_wallstreetcn(max_items=MAX_PER_SOURCE):
    """采集华尔街见闻 global 列表页。Playwright 优先(SSR 反爬),requests 兜底。"""
    items = []
    try:
        anchors = []
        # 1) Playwright(完整渲染,拿标题+时间)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(
                user_agent=config.USER_AGENT, viewport={"width": 1280, "height": 800})
            page.goto(config.HTML_SOURCES["华尔街见闻"]["url"], timeout=40000)
            page.wait_for_timeout(5000)
            data = page.eval_on_selector_all(
                'a[href*="/articles/"]',
                "els => els.map(e => ({href: e.getAttribute('href'), "
                "text: (e.innerText || '').replace(/\\s+/g, ' ').trim()}))")
            browser.close()
        for it in data:
            href = it.get("href", "")
            if "/articles/" not in href:
                continue
            anchors.append((it.get("text", ""), href, None))
        print(f"  [HTML] 华尔街见闻(playwright): {len(anchors)} 条")
        if not anchors:
            raise RuntimeError("playwright empty")
    except Exception:
        # 2) requests 兜底(有时直接返回完整 SSR 页面)
        try:
            html = safe_get(config.HTML_SOURCES["华尔街见闻"]["url"]).text
            anchors = _parse_wsn_anchors(html)
            print(f"  [HTML] 华尔街见闻(requests): {len(anchors)} 条")
        except Exception as exc:
            print(f"  华尔街见闻失败: {exc}")
            return items

    for title, link, published in anchors[:max_items]:
        if not title:
            continue
        items.append({
            "source": "华尔街见闻",
            "title": title,
            "link": link,
            "summary": "",
            "published": published,
        })
    return items


def in_window(published, window_hours=36, now=None):
    """判断发布时间是否在窗口内。无时间则保留。"""
    if not published:
        return True
    try:
        pub = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return (now - pub) <= timedelta(hours=window_hours) and pub <= now + timedelta(hours=1)
    except Exception:
        return True


def collect_all(window_hours=36):
    """采集全部源,按时间倒序合并。"""
    all_items = []
    for name, url in config.RSS_SOURCES.items():
        all_items.extend(collect_rss(name, url))
    all_items.extend(collect_wallstreetcn())
    if config.USE_EN_SOURCES:
        for name, url in config.RSS_SOURCES_EN.items():
            all_items.extend(collect_rss(name, url))

    # 去重(同源+同标题)
    seen = set()
    dedup = []
    for item in all_items:
        key = (item["source"], item["title"][:40])
        if key in seen:
            continue
        seen.add(key)
        if in_window(item["published"], window_hours):
            dedup.append(item)

    # 按发布时间倒序
    def sort_key(it):
        if it["published"]:
            try:
                return datetime.fromisoformat(it["published"].replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0
        return 0

    dedup.sort(key=sort_key, reverse=True)
    return dedup


def main():
    parser = argparse.ArgumentParser(description="新闻采集")
    parser.add_argument("--date", help="采集日期 YYYY-MM-DD,默认今天")
    parser.add_argument("--window", type=int, default=36, help="时间窗口(小时)")
    args = parser.parse_args()

    date_str = args.date or datetime.now(CST).strftime("%Y-%m-%d")
    items = collect_all(window_hours=args.window)

    out_path = config.DATA_DIR / f"raw_{date_str}.json"
    payload = {"date": date_str, "collected_at": datetime.now(CST).isoformat(),
               "count": len(items), "items": items}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成: {len(items)} 条 -> {out_path}")


if __name__ == "__main__":
    main()
