#!/usr/bin/env bash
# 每日快讯主流程:采集 → AI处理 → 生成网站 → 生成视频 → 生成发布工单
#
# 用法:
#   ./run_daily.sh                 # 全流程(默认今天 CST)
#   ./run_daily.sh --date 2026-08-13
#   ./run_daily.sh --no-video      # 跳过视频渲染(只更新网站+工单)
#   ./run_daily.sh --no-publish    # 不生成发布工单
#   ./run_daily.sh --skip-collect  # 复用已有 raw_*.json(不重新采集)
#
# 说明:
#   - 本脚本只生成内容与待审工单(半自动),不会自动发布。
#   - 发布见 README: B站填 cookie 后执行 publisher_bilibili.py,抖音 --login 后执行 publisher_douyin.py。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$PROJECT_ROOT/.venv/bin/python"
ROOTFS="$PROJECT_ROOT/tools/rootfs"
export LD_LIBRARY_PATH="$ROOTFS/usr/lib/x86_64-linux-gnu:$ROOTFS/usr/lib/x86_64-linux-gnu/pulseaudio"

DATE="$(date -d '+8 hours' +%F)"
DO_VIDEO=1
DO_PUBLISH=1
DO_COLLECT=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --date) DATE="$2"; shift 2 ;;
        --no-video) DO_VIDEO=0; shift ;;
        --no-publish) DO_PUBLISH=0; shift ;;
        --skip-collect) DO_COLLECT=0; shift ;;
        *) echo "未知参数: $1" >&2; exit 1 ;;
    esac
done

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run_${DATE}.log"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"; }

fail() { log "!! 失败: $*"; exit 1; }

log "==== 每日快讯 $DATE 开始 ===="

if [[ $DO_COLLECT -eq 1 ]]; then
    log "[1/5] 采集新闻..."
    "$PY" "$PROJECT_ROOT/src/news_collector.py" --date "$DATE" >>"$LOG_FILE" 2>&1 \
        || fail "news_collector 失败"
fi

log "[2/5] DeepSeek 分类/摘要/脚本..."
"$PY" "$PROJECT_ROOT/src/ai_processor.py" --date "$DATE" >>"$LOG_FILE" 2>&1 \
    || fail "ai_processor 失败"

log "[3/5] 生成静态站..."
"$PY" "$PROJECT_ROOT/src/site_generator.py" >>"$LOG_FILE" 2>&1 \
    || fail "site_generator 失败"

if [[ $DO_VIDEO -eq 1 ]]; then
    log "[4/5] 渲染视频(竖屏+横屏,约需数分钟)..."
    CURATED="$PROJECT_ROOT/content/curated_${DATE}.json"
    if [[ -f "$CURATED" ]]; then
        log "[4/5] 检测到策划文件,使用人工播报列表(--curated)"
        "$PY" "$PROJECT_ROOT/src/video_builder.py" --date "$DATE" --curated >>"$LOG_FILE" 2>&1 \
            || fail "video_builder 失败"
    else
        "$PY" "$PROJECT_ROOT/src/video_builder.py" --date "$DATE" >>"$LOG_FILE" 2>&1 \
            || fail "video_builder 失败"
    fi
else
    log "[4/5] 跳过视频渲染(--no-video)"
fi

if [[ $DO_PUBLISH -eq 1 ]]; then
    log "[5/5] 生成发布工单..."
    "$PY" "$PROJECT_ROOT/src/publisher_bilibili.py" --make-ticket --date "$DATE" >>"$LOG_FILE" 2>&1 \
        || log "!! B站工单生成失败(不影响流程)"
    "$PY" "$PROJECT_ROOT/src/publisher_douyin.py" --make-ticket --date "$DATE" >>"$LOG_FILE" 2>&1 \
        || log "!! 抖音工单生成失败(不影响流程)"
else
    log "[5/5] 跳过发布工单(--no-publish)"
fi

log "==== 完成 $DATE,日志: $LOG_FILE ===="
log "检查 site/index.html,然后到 publish_queue/ 审核工单后发布"
