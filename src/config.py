"""全局配置:路径、新闻源、DeepSeek API、ffmpeg 环境。"""

import os
from datetime import timedelta, timezone
from pathlib import Path

# ---------- 路径 ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONTENT_DIR = PROJECT_ROOT / "content"
VIDEO_DIR = PROJECT_ROOT / "video"
QUEUE_DIR = PROJECT_ROOT / "publish_queue"
SITE_DIR = PROJECT_ROOT / "site"
LOG_DIR = PROJECT_ROOT / "logs"
ASSET_DIR = PROJECT_ROOT / "assets"
ROOTFS = PROJECT_ROOT / "tools" / "rootfs"

for d in (DATA_DIR, CONTENT_DIR, VIDEO_DIR, QUEUE_DIR, SITE_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------- 新闻源(国内源,均可直连) ----------
RSS_SOURCES = {
    "IT之家": "https://www.ithome.com/rss/",
    "极客公园": "https://www.geekpark.net/rss",
    "量子位": "https://www.qbitai.com/feed",
    "InfoQ中文": "https://www.infoq.cn/feed",
    "开源中国": "https://www.oschina.net/news/rss",
    "爱范儿": "https://www.ifanr.com/feed",
    "雷锋网": "https://www.leiphone.com/feed",
    "钛媒体": "https://www.tmtpost.com/rss",
}
HTML_SOURCES = {
    "华尔街见闻": {
        "url": "https://wallstreetcn.com/news/global",
        "link_pattern": "/articles/",
    },
}
# 可选英文源(需 DeepSeek 翻译,默认关闭)
RSS_SOURCES_EN = {
    "TechCrunch": "https://techcrunch.com/feed/",
}
USE_EN_SOURCES = False

# ---------- DeepSeek API ----------
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# ---------- ffmpeg ----------
FFMPEG_BIN = ROOTFS / "usr" / "bin" / "ffmpeg"
FFPROBE_BIN = ROOTFS / "usr" / "bin" / "ffprobe"
FF_LD_LIBRARY_PATH = ":".join([
    str(ROOTFS / "usr" / "lib" / "x86_64-linux-gnu"),
    str(ROOTFS / "usr" / "lib" / "x86_64-linux-gnu" / "pulseaudio"),
])

# ---------- 视频参数 ----------
# B站: 横屏 16:9;抖音: 竖屏 9:16
VIDEO_HORIZONTAL = {"width": 1920, "height": 1080}
VIDEO_VERTICAL = {"width": 1080, "height": 1920}
FPS = 25

# ---------- 其它 ----------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
CST = timezone(timedelta(hours=8))

# 日志
LOG_FILE = LOG_DIR / "newsflow.log"


def get_env(key, default=""):
    """读取环境变量或 ~/.hermes/.env 中的密钥(兼容 KEY=value 与 export KEY=value)。"""
    if os.environ.get(key):
        return os.environ[key]
    env_path = Path(os.path.expanduser("~/.hermes/.env"))
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            line = line.lstrip("export ")
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return default


DEEPSEEK_API_KEY = get_env("DEEPSEEK_API_KEY")
