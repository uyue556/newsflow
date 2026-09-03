# 每日快讯自动化工作流

全球(国内源)每日 AI / 金融 / 政策 / 科技新闻采集 → 生成静态网页 → 自动剪辑配音视频 → 半自动发布到 B站与抖音。

## 目录结构

```
newsflow/
├── src/
│   ├── config.py              # 全局配置(路径/新闻源/API/ffmpeg)
│   ├── news_collector.py      # RSS + Playwright 采集
│   ├── ai_processor.py        # DeepSeek 分类/去重/摘要/口播脚本
│   ├── site_generator.py      # 每日快讯静态站(index/ai/daily,按星期轮换 7 套风格)
│   ├── video_builder.py       # Playwright 录屏网站 + edge-tts 配音 + ffmpeg 合成
│   ├── publisher_bilibili.py  # B站上传(第三方库 + cookie)
│   └── publisher_douyin.py    # 抖音发布(Playwright RPA)
├── data/                      # 原始新闻 raw_YYYY-MM-DD.json
├── content/                   # AI 处理产物 processed_YYYY-MM-DD.json
├── video/                     # 视频输出(竖屏/横屏)
├── publish_queue/             # 待审发布工单
├── site/                      # 静态站(index.html / ai.html / daily/*.html)
├── logs/                      # 运行日志
├── assets/                    # 字体 + styles/(七套星期风格样式与 DESIGN.md)
├── tools/rootfs/              # 免 root 解包的 ffmpeg + 依赖库
└── run_daily.sh               # 主流程脚本
```

## 环境要求

- WSL2 Ubuntu(本项目在 `/mnt/c/Users/Administrator/Desktop/Project/newsflow`)
- Python 虚拟环境 `newsflow/.venv`(`./.venv/bin/python`,含 feedparser / requests / beautifulsoup4 / playwright / bilibili-api-python / edge-tts)
- ffmpeg: 已部署在 `tools/rootfs`,运行脚本自动设置 `LD_LIBRARY_PATH`
- API Key: 在 `~/.hermes/.env` 写入 `DEEPSEEK_API_KEY`(见下方配置)

## 快速开始

一键全流程(采集 → AI → 网站 → 视频 → 工单):

```bash
cd /mnt/c/Users/Administrator/Desktop/Project/newsflow
./run_daily.sh
```

常用参数:

```bash
./run_daily.sh --date 2026-08-13    # 指定日期
./run_daily.sh --no-video           # 只更新网站(跳过视频渲染)
./run_daily.sh --no-publish         # 不生成发布工单
./run_daily.sh --skip-collect       # 复用已有 raw_*.json
```

分步执行(等价):

```bash
ROOT=tools/rootfs
export LD_LIBRARY_PATH=$ROOT/usr/lib/x86_64-linux-gnu:$ROOT/usr/lib/x86_64-linux-gnu/pulseaudio
PY=./.venv/bin/python

$PY src/news_collector.py            # 采集
$PY src/ai_processor.py              # AI 分类/精选/脚本
$PY src/site_generator.py            # 生成 site/(index/ai/daily/curated,按星期自动选风格)
$PY src/video_builder.py --date 2026-08-13   # 生成横屏视频(默认;B站版)
$PY src/video_builder.py --date 2026-08-13 --vertical  # 只出竖屏(抖音)
$PY src/video_builder.py --date 2026-08-13 --curated   # 用 content/curated_<date>.json(人工策划)
$PY src/video_builder.py --date 2026-08-13 --voice yunjian --rate=-10%  # 换音色/语速(负值需用 = 连接)
```

配音默认:云哲(YunJhe)男声,沉稳稳重档(语速 `-12%`,音调 `-6Hz`,试听选定);可用 `--voice {xiaoxiao,xiaoyi,yunxi,yunjian,yunyang,yunjhe,wanlung}` 与 `--rate`(如 `-10%`)/`--pitch`(如 `+10Hz`)覆盖。

产物:
- 网页: `site/index.html`(双击用浏览器打开)、`site/ai.html`(AI 板块)、`site/news/YYYY-MM-DD_NN.html`(单条 AI 快讯详情页,`site/ai.html` 卡片上"详情 →"可跳转)、`site/daily/YYYY-MM-DD.html`(往期详情)、`site/curated.html`(播报策划,当天全部条目供人工勾选/排序)、`site/styles/mon~sun.html`(七风格预览页)
- 视频: `video/YYYY-MM-DD_horizontal.mp4`(B站 16:9,默认)、`video/YYYY-MM-DD_vertical.mp4`(抖音 9:16,`--vertical`/`--both` 时生成);片头为整体首页页头(口播"欢迎收看X月X日的每日AI快讯"),正文逐条切换单条快讯详情页
- 工单: `publish_queue/YYYY-MM-DD-bilibili.json`、`YYYY-MM-DD-douyin.json`(半自动,人工审核后发布)

## 播报策划(手动筛选当天播报内容)

默认视频用 AI 自动精选板块。若想人工决定当天播报哪几条、按什么顺序,可用策划页:

1. 浏览器打开 `site/curated.html`(当天全部条目,全类别,含摘要/评分/口播稿)。
2. 勾选要播报的条目;可用 **↑ / ↓ / 置顶 / 置底** 调整播报顺序。
3. 点 **导出 JSON**,下载得到 `curated_<date>.json`,把它放到 `content/` 目录。
4. 运行 `video_builder.py --date <date> --curated`;`run_daily.sh` 检测到该文件也会自动用 `--curated`。

> 未勾选的条目、`--curated` 时缺 `voice_script` 的条目,会用 `summary` 作为 TTS 兜底;不额外调用 DeepSeek。

## 站点风格(按星期自动轮换)

页面样式按**内容日期的星期**切换,共 7 套(源文件 `assets/styles/`,规范见 `assets/styles/DESIGN.md`):

| 星期 | 风格 | 星期 | 风格 |
|------|------|------|------|
| 一 | Editorial 杂志编辑风 | 五 | OpenCode 工程终端风 |
| 二 | Swiss 瑞士国际主义 | 六 | Retro 复古暖调 |
| 三 | Risograph 油墨印刷 | 日 | Soft Pastel 柔和粉彩 |
| 四 | Neo-Brutalism 新粗野主义 | | |

> 注: tue(Bento)/wed(Aurora Glass) 已于 2026-09 退役,由 swiss/riso 接管周二/周三,原因见 `assets/styles/BANNED_STYLES.md`;blueprint/deco 为备选草稿,可用 `--style` 预览。

预览/调试时可强制指定风格(全部页面统一):

```bash
$PY src/site_generator.py --style fri   # 可选 mon/swiss/riso/thu/fri/sat/sun/blueprint/deco
```

## 发布(半自动,人工审核)

### B站(第三方库 + cookie)

1. 浏览器 F12 → Application → Cookies(bilibili.com),复制三个值写入 `~/.hermes/.env`:

   ```
   BILI_SESSDATA=xxx
   BILI_BILI_JCT=xxx
   BILI_BUVID3=xxx
   ```

2. 审核工单(可手动改 title/desc/tags),然后:

   ```bash
   ./.venv/bin/python src/publisher_bilibili.py --list
   ./.venv/bin/python src/publisher_bilibili.py --date 2026-08-13
   ```

### 抖音(Playwright RPA)

1. 首次手动登录保留登录态(需 GUI,WSL2 请确认 WSLg 可用):

   ```bash
   ./.venv/bin/python src/publisher_douyin.py --login
   ```

2. 审核工单后发布(打开浏览器自动上传+填写,终端确认后才点"发布"):

   ```bash
   ./.venv/bin/python src/publisher_douyin.py --date 2026-08-13
   ```

   无 GUI 环境加 `--headless`。登录态保存在 `~/newsflow-douyin-profile`。

## 定时调度

已配置 WSL cron(每天 07:30 CST 运行 `run_daily.sh`):

```bash
crontab -l   # 查看
crontab -e   # 修改
```

注意: **WSL2 只在运行时才执行 cron**。若机器常关机,建议改用 Windows 任务计划程序:
创建任务 → 触发器(每天)→ 操作填:

```
程序:  wsl.exe
参数:  -d <发行版> -u administrator -- bash -lc "cd /mnt/c/Users/Administrator/Desktop/Project/newsflow && ./run_daily.sh >> logs/cron.log 2>&1"
```

## 配置(config.py)

| 配置 | 说明 |
|---|---|
| `RSS_SOURCES` | 8 个国内 RSS 源(IT之家/极客公园/量子位/InfoQ/开源中国/爱范儿/雷锋网/钛媒体) |
| `HTML_SOURCES` | 华尔街见闻(需 Playwright,直连反爬) |
| `USE_EN_SOURCES` | 是否启用英文源(TechCrunch,默认关闭) |
| `DEEPSEEK_API_KEY` | 从 `~/.hermes/.env` 读取 |
| 视频参数 | B站横屏 1920x1080,抖音竖屏 1080x1920,25fps |

## 风险提示

- 抖音风控严格:避免高频发布、保持登录态稳定;DOM 变更时调整 `publisher_douyin.py` 顶部选择器常量。
- B站 cookie 会过期,需定期刷新。
- edge-tts 偶发失败,已内置 5 次重试(含切换备选音色);仍失败时手工运行 video_builder 补生成。
- 国内新闻源可能失效,替换 `config.py` 中对应 URL 即可。
