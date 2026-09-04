"""AI 处理模块:DeepSeek 分类/去重/评分/摘要/脚本生成。

输入: data/raw_YYYY-MM-DD.json
输出: content/processed_YYYY-MM-DD.json
  - 每条: 原始字段 + category(分类) + score(选题分) + summary(摘要) + voice_script(口播脚本)
  - 并生成每日选题排名 daily_top(精选 N 条,人工审核用)
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import config

CATEGORIES = ["AI", "科技硬件", "金融", "政策", "商业", "国际", "其他"]
MAX_INPUT_ITEMS = 200


def deepseek_chat(messages, temperature=0.4, max_tokens=2000, retries=2):
    """调用 DeepSeek chat completions。失败重试。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("未找到 DEEPSEEK_API_KEY,请检查 ~/.hermes/.env")
    url = f"{config.DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            print(f"  DeepSeek 调用失败(尝试{attempt+1}): {exc}")
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError("DeepSeek 调用失败")


def extract_json_array(text):
    """从 LLM 输出中提取 JSON 数组(容忍前后缀文本和代码块)。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    return json.loads(text[start:end + 1])


def ai_classify_batch(items):
    """分批调用 DeepSeek 做分类+评分+摘要(每批 BATCH 条)。"""
    if not items:
        return []
    BATCH = 25
    result_map = {}
    for start in range(0, len(items), BATCH):
        chunk = items[start:start + BATCH]
        lines = []
        for i, it in enumerate(chunk, start=start):
            title = it["title"][:120]
            summary = (it.get("summary") or "")[:150]
            lines.append(f"{i}. 标题: {title} | 摘要: {summary}")
        prompt = f"""你是资深新闻编辑。请对以下 {len(chunk)} 条新闻做分类与选题评估。

分类只能是: {"、".join(CATEGORIES)}
评分规则(0-10): 高价值、与AI/科技/金融/政策强相关、适合大众传播的给高分;纯娱乐、广告、低质量内容给低分。
摘要: 用不超过35个字概括核心信息(中文)。

输出严格的 JSON 数组,每个元素: {{"index": 序号, "category": "分类", "score": 分数, "summary": "摘要"}}

新闻列表:
{chr(10).join(lines)}"""
        out = deepseek_chat([
            {"role": "system", "content": "你只输出合法 JSON,不要任何解释。"},
            {"role": "user", "content": prompt},
        ], max_tokens=4000)
        parsed = extract_json_array(out)
        for p in parsed:
            if isinstance(p, dict) and "index" in p:
                result_map[int(p["index"])] = p
        print(f"  批 {start//BATCH + 1}: 解析 {len(parsed)} 条")

    enriched = []
    for i, it in enumerate(items):
        r = result_map.get(i, {})
        cat = r.get("category", "其他")
        if cat not in CATEGORIES:
            cat = "其他"
        it["index"] = i
        it["category"] = cat
        it["score"] = float(r.get("score", 0) or 0)
        it["raw_summary"] = it.get("summary", "")   # 保留 RSS 原文(约600字)备用
        it["summary"] = (r.get("summary") or "").strip() or it["summary"][:60]
        enriched.append(it)
    return enriched


def ai_generate_script(daily):
    """为每日精选列表生成口播脚本(单条新闻一条)与整期快讯文案。"""
    result = []
    for it in daily:
        result.append(_script_one(it))
    return result


def _script_one(it):
    """为单条新闻生成 60-70 秒口播脚本(300-350字) + 150-200 字详情页正文摘要(detail_text)。"""
    title = it["title"][:120]
    summary = (it.get("raw_summary") or it.get("summary") or "")[:600]
    prompt = f"""根据下面的新闻信息,输出严格的 JSON 对象,不要任何解释:
{{"voice": "300-350字中文口播快讯,时长约60-70秒,适合短视频配音,语气客观带节奏感,结构:开头一句钩子引出事件,中间讲清事件经过与关键数据/人名/产品名,结尾给影响或展望;直接输出文本,不要引号", "detail": "150-200字中文详情摘要,保留关键数据、人名、产品名与结论,条理清晰,适合网页正文阅读"}}

标题: {title}
摘要: {summary}"""

    def _call():
        return deepseek_chat([
            {"role": "system", "content": "你只输出合法 JSON 对象,不要任何解释。"},
            {"role": "user", "content": prompt},
        ], temperature=0.8, max_tokens=900).strip()

    voice, detail = "", ""
    # 口播 60-70 秒约 300-350 字;过短(疑似截断或偷懒)时重试一次
    for _ in range(2):
        out = _call()
        start, end = out.find("{"), out.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(out[start:end + 1])
                voice = (parsed.get("voice") or "").strip()
                detail = (parsed.get("detail") or "").strip()
            except (ValueError, TypeError):
                pass
        if len(voice) >= 270:
            break
    if not voice:
        # 兜底:把整段输出当口播文本
        voice = out.replace("{", "").replace("}", "").replace('"', "").strip()[:300]
    it["voice_script"] = voice
    if detail:
        it["detail_text"] = detail
    return it


def load_raw(date_str):
    path = config.DATA_DIR / f"raw_{date_str}.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到 {path},请先运行 news_collector.py")
    return json.loads(path.read_text(encoding="utf-8"))


def is_composite(title):
    """判断标题是否把多条子新闻揉成一条(合成/聚合条目)。

    触发条件:标题含全角/半角分号(分号通常用来并列多个独立事件),
    或含“早报/晚报/晨报/汇总/合集/盘点”等聚合类特征词。
    """
    t = str(title or "")
    if ("；" in t) or (";" in t):
        return True
    return any(h in t for h in ("早报", "晚报", "晨报", "早班车", "汇总", "合集", "盘点"))


def build_category_top(items, category, limit=4, min_score=5.5):
    """从指定分类中选高分 TOP 条目(按分数),返回待生成口播脚本的列表。

    会过滤掉把多条子新闻揉成一条的合成标题(如含分号并列多个事件、
    “早报/晚报”类聚合简报),保证一个栏目只讲一条新闻。
    """
    sel = [it for it in items
           if it.get("category") == category and it.get("score", 0) >= min_score
           and not is_composite(it.get("title"))]
    sel.sort(key=lambda x: x["score"], reverse=True)
    return sel[:limit]


def main():
    parser = argparse.ArgumentParser(description="AI 处理")
    parser.add_argument("--date", help="处理日期 YYYY-MM-DD,默认今天")
    parser.add_argument("--top", type=int, default=6, help="每日精选条数")
    parser.add_argument("--skip-script", action="store_true", help="跳过脚本生成")
    args = parser.parse_args()

    date_str = args.date or datetime.now(config.CST).strftime("%Y-%m-%d")
    raw = load_raw(date_str)
    items = raw["items"][:MAX_INPUT_ITEMS]

    print(f"AI 处理 {len(items)} 条...")
    items = ai_classify_batch(items)

    # 精选: 只保留 AI 类别,按分数取 TOP(过滤合成/聚合条目)
    good = [it for it in items
            if it["category"] == "AI" and it["score"] >= 5.5
            and not is_composite(it.get("title"))]
    good.sort(key=lambda x: x["score"], reverse=True)
    daily = good[:args.top]

    # AI 板块 TOP: 单独从 AI 分类取前 10(视频 AI 段用)
    ai_top = build_category_top(items, "AI", limit=10)

    if not args.skip_script:
        if daily:
            print(f"生成 {len(daily)} 条精选口播脚本...")
            daily = ai_generate_script(daily)
        if ai_top:
            print(f"生成 {len(ai_top)} 条 AI 口播脚本...")
            ai_top = ai_generate_script(ai_top)

    out = {
        "date": date_str,
        "processed_at": datetime.now(config.CST).isoformat(),
        "total": len(items),
        "items": items,
        "daily_top": daily,
        "ai_top": ai_top,
    }
    out_path = config.CONTENT_DIR / f"processed_{date_str}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成: 处理{len(items)}条,精选{len(daily)}条,AI板块{len(ai_top)}条 -> {out_path}")
    for it in daily:
        print(f"  [精选 {it['category']}] {it['score']:.1f} {it['title'][:40]}")
    for it in ai_top:
        print(f"  [AI板块] {it['score']:.1f} {it['title'][:40]}")


if __name__ == "__main__":
    main()
