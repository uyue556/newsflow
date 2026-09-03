# DAILY·AI 站点设计系统

按**内容发布日期的星期**(周一~周日)自动轮换 7 套视觉风格。所有页面共用同一套 DOM 类名契约与交互脚本,仅样式层切换;`video_builder.py` 的选择器(`article.card.featured` 等)不受影响。

## 风格日历

| 星期 | 文件 | 风格 | 关键词 |
|------|------|------|--------|
| 一 | `mon.css` | Editorial 杂志编辑风 | 纸白衬线、细双线、牛血红 accent、克制留白 |
| 二 | `swiss.css` | Swiss 瑞士国际主义 | 纯白底、Helvetica 黑体、瑞士红 #e63329、可见栅格线、零圆角硬边(tue=Bento 已退役,2026-09 起 swiss 接管周二) |
| 三 | `riso.css` | Risograph 油墨印刷 | 米白纸底半调网点、藏蓝墨×荧光橘套印错位、硬阴影卡片(wed=Aurora Glass 已退役,2026-09 起 riso 接管周三) |
| 四 | `thu.css` | Neo-Brutalism 新粗野主义 | 米白底粗黑描边、硬偏移阴影、柠檬黄/珊瑚红撞色 |
| 五 | `fri.css` | OpenCode 工程终端风(致敬 opencode.ai) | 纸灰单色、等宽字体、方角发丝线、唯一信号绿 #03b000、光标闪烁/[*]/Fig 记号 |
| 六 | `sat.css` | Retro 复古暖调 | 做旧纸面噪点、芥末黄/焦橘、虚线分隔、邮票徽标 |
| 日 | `sun.css` | Soft Pastel 柔和粉彩 | 奶油底粉彩光晕、薰衣草紫/薄荷绿、超大圆角软阴影 |

## 共享契约

### DOM 类名(video_builder / JS 依赖,不可改名)
- Hero:`header.hero > .wrap`、`.badge.hero-badge(::before 圆点)`、`h1.site-title.hero-title`、`.sub.hero-sub`、`.hero-stats > .stat > b[data-target]`
- 区块:`h2.section(.count)`
- 卡片:`.featured-row > article.card.featured.anim.anim-scale.dN`,内部 `span.tag`(首页)/`span.mark`(AI 页)、`span.rank(#01)`、`h3>a`、`p.summary`、`p.voice`、`div.meta(span.src/.time/.score)`
- 导航:`.ai-link-wrap>a.ai-link(span.n)`、`.archive>a(span+.count)`、`.back`
- 页脚:`footer(.sep)`;AI 页根元素 `body.ai`

### 行为(共享 `site.js`)
- IntersectionObserver 给 `.anim` 加 `.visible`(threshold 0.12)
- `[data-target]` 数字滚动:rAF + easeOutCubic 900ms,小数保留 1 位
- `.d0~.d7` 依次递增延迟

### 每套 CSS 必须包含
1. `:root` 语义 tokens(--bg/--surface/--text/--muted/--accent/--shadow…)
2. 完整组件样式(上述全部类名)
3. 入场动效 keyframes(heroFadeSlide/heroPop/badgePulse)+ `.anim` 系统

### 单条快讯详情页(site/news/YYYY-MM-DD_NN.html)
- 生成:site_generator `render_news_detail_html`,每天 ai_top 逐条一页;`site/ai.html` 卡片 meta 内"详情 →"链接跳入
- DOM:复用 hero 契约(badge 显示 `AI 快讯 · NN/NN`)+ `article.card.featured.detail-card.detail-anim.da1`,内部 `h3.detail-title>a`(原文链接)、`p.summary.detail-summary`(取消 line-clamp)、`p.voice`、`div.meta`
- 动画:内联 `DETAIL_CSS`(site_generator / video_builder 两处一致)——`@keyframes riseIn`(translateY(32px)→0,.8s cubic-bezier)+ `.da1~.da3` stagger 延迟 + `prefers-reduced-motion` 降级;纯 CSS 自动播放,不依赖 site.js;hero 动画仍由各风格 CSS 自带 keyframes 提供
- 视频:video_builder 用同构 `detail_inline_html`(CSS 内联、无 JS)逐页截图,入场动画 settle(约 1.6s)后截图
4. `prefers-reduced-motion: reduce` 全量降级
5. ≤720px 移动端适配
6. 自包含:无外链字体/图片,系统字体栈;纹理用 SVG data-uri 或渐变

### 字号基准
正常网页字号(正文 16-17px),**不再使用旧版 `zoom:.33` 缩放约定**。视频渲染缩放由 `video_builder.py` 注入(FONT_CSS zoom,H/V 方向不同值)。

## 无障碍基线
- 正文对比度 ≥ WCAG AA(4.5:1):深底风格正文用 rgba(255,255,255,.6) 以上亮度
- 强调色只用于短文本/数字/边框,长文本一律 --text
- 动效尊重 reduced-motion;焦点可见(浏览器默认 outline 未被移除)

## 扩展新风格
1. 复制任一 css 为新文件,改 `:root` tokens 与装饰细节
2. 保持类名契约与必备块(见上)
3. 在 `site_generator.py` 的 `WEEKDAY_STYLES` 中登记
