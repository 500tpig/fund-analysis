# 界面设计研究笔记：深色金融看板的原则、现状与下一步

2026-10-30 整理，基于 `main` 的 `448c3fe`。本文只做研究与提议，不是实施授权：2.5 浅色主题按
`docs/mobile-ux-batch2-plan.md` 仍需单独确认立项；正在进行的 2.4 以该文件第 2.4 节为准，本文不重复
2.4 已覆盖的表面色阶、边框瘦身、圆角与投影、数字等宽右对齐、图表网格与零线、时长 token、
`.ui-press`、焦点环与骨架屏。验收尺子仍是 `docs/design-standards.md`，本文提议若被采纳，
先改那份标准再改代码。

## 1. 顶级设计怎么想

每条原则给出来源链接与一句原文要点（英文原文节选，后附中文意思），再写一句对本项目的含义。
所有引文均在 2026-10-30 从原页面实际读取；读取不到原文的说法没有写入。

### 1.1 层级靠表面明度，越靠前越亮

- 来源：[Apple HIG · Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)
  「the elevated colors are brighter, making foreground interfaces appear to advance」：
  浮层用更亮的 elevated 底色，让前景向前。
- 来源：[Android Developers · Material 3 in Compose](https://developer.android.com/develop/ui/compose/designsystems/material3)
  「Material 3 represents elevation mainly using tonal color overlays」：M3 主要用色调而非阴影表达高度。
- 来源：[Material Components for Android · Dark theme](https://github.com/material-components/material-components-android/blob/master/docs/theming/Dark.md)
  「Shadows are less effective in an app using a dark theme」：深色下阴影对比弱，看不清。
- 来源：[IBM Carbon · Color](https://carbondesignsystem.com/elements/color/overview/)
  「In the dark themes, layers become one step lighter with each added layer」，并要求
  「Do not apply components that are darker than the background unless using high-contrast mode」。
- 本项目：2.4 正在落实这一条；本文只把它作为 2.5 的前提，浅色主题的层级规则与深色不同（见 1.8）。

### 1.2 底色用深灰，不用纯黑

- 来源：[Material Components for Android · Dark theme](https://github.com/material-components/material-components-android/blob/master/docs/theming/Dark.md)
  「background and surface colors are dark grey instead of black, which increases visibility for shadows
  and also reduces eye strain for light text」：深灰让阴影可见，也减轻浅色文字的刺眼感。
- 来源：[Material Components for Android · Color](https://github.com/material-components/material-components-android/blob/master/docs/theming/Color.md)
  的 M3 色彩角色表：暗色 Background／Surface 取 `neutral6`，容器依次为 `neutral4`、`neutral10`、
  `neutral12`、`neutral17`、`neutral22`，最亮的 Surface Bright 为 `neutral24`。
- 本项目：画布 `#0A0C0B` 已避开纯黑；该表给出了一组可参照的「每层抬多少」的色调间距。

### 1.3 强调色在深色底上去饱和

- 来源：[Android Developers · Wear OS Material 2.5 Color](https://developer.android.com/design/ui/wear/guides/m2-5/styles/color)
  「Saturated colors also produce optical vibrations against a dark background, which can cause eye strain」：
  高饱和色在深色底上会「振动」，造成眼疲劳；同页建议用较浅色调（200–50）而非 900–500。
- 来源：[impeccable · operate.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/operate.md)
  在产品界面禁忌中列出「Heavy color or full-saturation accents on inactive states」。
- 本项目：涨 `#E0787B`、跌 `#54C397`、品牌金 `#C9AE70` 已是去饱和取值；这一取值只适用于深色底（见 2.3 的白底实测）。

### 1.4 对比度设下限：正文 4.5:1，小字争取 7:1，图形 3:1

- 来源：[Apple HIG · Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)
  「strive for a contrast ratio of 7:1, especially in small text」：自定义前景与背景至少 4.5:1，小字争取 7:1。
- 来源：[W3C · Understanding SC 1.4.11 Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast)
  图表中的线属于需要 3:1 的图形对象，并提醒细线经抗锯齿后实际更淡，
  「best practice would be for authors to avoid particularly thin lines」。
- 本项目：`frontend/scripts/check-contrast.mjs` 已覆盖文字与曲线主色，但还没覆盖基准线、焦点环和浅色主题（见 2.3）。

### 1.5 一种颜色只表达一种意思：品牌色、涨跌色、状态色分开

- 来源：[Apple HIG · Color](https://developer.apple.com/design/human-interface-guidelines/color)
  「Avoid using the same color to mean different things」：状态、可交互性等信息的用色要全局一致。
- 来源：[IBM Carbon · Data visualization color palettes](https://carbondesignsystem.com/data-visualization/color-palettes/)
  数据系列用分类／单色／发散色板，状态另有专用的 alert 色板：「Alert colors are used to reflect status」。
- 来源：[impeccable · operate.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/operate.md)
  「Accent color used for primary actions, current selection, and state indicators only, not decoration」。
- 本项目：`docs/design-standards.md` 已规定暖金只用于品牌、焦点与基金曲线，状态色不复用涨跌色；现状仍有越界（见 2.1）。

### 1.6 颜色不能是唯一信号

- 来源：[W3C · Understanding SC 1.4.1 Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color)
  「Color is not used as the only visual means of conveying information」；色相不同但明度差达到 3:1 也可算额外区分。
- 来源：[Apple HIG · Color](https://developer.apple.com/design/human-interface-guidelines/color)
  「Avoid relying solely on color to differentiate between objects, indicate interactivity, or communicate essential information」。
- 本项目：金额已带 `+`／`-` 符号（`frontend/src/shared/formatters.ts:31-37`），涨跌不只靠红绿；
  红绿两色之间的对比度只有 1.35:1，符号必须保留。选中态仍有只靠浅色差的地方（见提议 9）。

### 1.7 用语义 token，不写死色值；换主题就是给同名角色换值

- 来源：[Apple HIG · Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)
  「Avoid using hard-coded color values or colors that don't adapt」。
- 来源：[IBM Carbon · Color](https://carbondesignsystem.com/elements/color/overview/)
  「Color token names and roles are the same across themes, only the assigned value will change with the theme」。
- 来源：[Radix Colors · Understanding the scale](https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale)
  12 级色阶各有用途：1–2 应用背景，3–5 组件常态／悬停／按下，6–8 边框与焦点环，9–10 实色，11–12 文字。
- 来源：[impeccable · colorize.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/colorize.md)
  「Prefer explicit colors over chains of translucent overlays when alpha would make contrast context-dependent」：
  半透明叠加会让对比度随底色变化，优先用明确色值。
- 本项目：`:root` 已有语义 token，但样式与组件里仍有大量直接写的 `rgba()`（见 2.2）。

### 1.8 深浅两套分别设计，不做机械反转

- 来源：[Apple HIG · Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)
  「these colors aren't necessarily inversions of their light counterparts」。
- 来源：[IBM Carbon · Color](https://carbondesignsystem.com/elements/color/overview/)
  浅色主题的层在 White 与 Gray 10 之间交替（「layers alternate between White and Gray 10」），深色主题逐层变亮，两套层级模型不同。
- 来源：[Radix Colors · Installation](https://www.radix-ui.com/colors/docs/overview/installation)
  每个色阶分别提供浅色与深色文件（如 `gray.css` 与 `gray-dark.css`）。
- 来源：[impeccable · colorize.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/colorize.md)
  「Light and dark themes are each composed, not mechanically inverted」。
- 本项目：2.5 不能拿深色色值套到白底上，涨跌、品牌金、状态色都要另定一套（见 2.3）。

### 1.9 外观跟随系统，慎做应用内单独开关

- 来源：[Apple HIG · Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)
  「Avoid offering an app-specific appearance setting」：单独开关让人多调一处，还可能误以为应用没响应系统设置。
- 来源：[MDN · light-dark()](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/light-dark)
  需在 `:root` 设 `color-scheme: light dark`；并提醒「If the user has made a preference, you generally should not override their preferences」。
- 来源：[MDN · theme-color](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/meta/name/theme-color)
  `<meta name="theme-color">` 可带 `media="(prefers-color-scheme: …)"` 分别给浅深两色；该特性标注为 Limited availability。
- 本项目：这条直接回答 2.5 启动前的「默认跟随系统还是手动切换」（见提议 3）。

### 1.10 表格数字等宽，浏览器默认值也要纳入设计

- 来源：[MDN · font-variant-numeric](https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric)
  `tabular-nums`「numbers are all of the same size, allowing them to be easily aligned like in tables」。
- 来源：[impeccable · craft-floor.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/craft-floor.md)
  文字选区、光标、滚动条、焦点环与「the numerals in tabular data」都带浏览器默认值，应从调色板统一。
- 本项目：`.font-numeric` 与等宽右对齐属 2.4；2.5 需要把选区色、滚动条色等同样做成随主题变化的 token（见 2.2）。

### 1.11 密度服务任务，克制靠减少装饰而非稀释信息

- 来源：[impeccable · operate.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/operate.md)
  产品界面可以有「dense information when users need it」；同时「The tool should disappear into the task」。
- 来源：[impeccable · quieter.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/quieter.md)
  降噪是「Fewer background accents, flatter cards, less color, less motion」。
- 来源：[IBM Carbon · Color](https://carbondesignsystem.com/elements/color/overview/)
  中性灰为主，其他颜色「used sparingly and purposefully」。
- 本项目：与 2.4「不允许为了观感牺牲表格密度」一致；浅色主题同样不放大行高或留白来「显得干净」。

### 1.12 动效只服务状态变化；减弱动效时保留有意义的反馈

- 来源：[impeccable · operate.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/operate.md)
  「Motion conveys state, not decoration. State change, feedback, loading, reveal: nothing else」，多数过渡 150–250 ms。
- 来源：[Apple HIG · Motion](https://developer.apple.com/design/human-interface-guidelines/motion)
  「Don't add motion for the sake of adding motion」，并要求动效可选、不作为传达重要信息的唯一方式。
- 来源：[impeccable · animate.md](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/animate.md)
  「Reduced motion means fewer and gentler animations, not disabling all motion」：减弱动效时去掉位移，保留表达状态的透明度与颜色变化。
- 本项目：时长 token 属 2.4；减弱动效的策略目前是全部归零（见提议 7）。

## 2. 本项目现状对照

以下只读代码得出，行号以 `448c3fe` 为准。对比度用与 `check-contrast.mjs` 相同的 WCAG 公式计算。

### 2.1 同一颜色承担多种含义（对应 1.5、1.6）

- 「估」标记用品牌金：`frontend/src/views/FundsView.vue:271-274` 的 `badgeClass` 把「估」映射为
  `is-accent`，`frontend/src/assets/main.css:294-297` 的 `.inline-badge.is-accent` 用
  `--brand-accent` 着色。这是数据状态标记，按 `docs/design-standards.md` 不属于暖金的三种用途。
- 基准线与「平」同色：`frontend/src/theme/index.ts:10` 的 `chartColors.benchmark` 取
  `var(--text-tertiary)`，值为 `#85827A`，与 `--color-flat`（平盘色）完全相同。曲线上的基准线和列表里的「平」读起来是同一种语义。
- 估值偏差用涨跌色：`frontend/src/components/FundDetailPanel.vue:782` 的「平均偏差」按正负套
  `value-rise`／`value-fall`。偏差为正表示估值系统性偏高，属于准确度指标，不是行情涨跌；
  用 A 股红绿会被读成「涨／跌」。改不改、改成什么属于产品语义，需要用户确认。
- 已做对的部分：金额带正负号；状态色（`--status-*`）与涨跌色是两套 token。

### 2.2 色值绕过 token，单一主题写死在多处（对应 1.7、1.9）

- `frontend/src/assets/main.css` 在 `:root` 块（第 40–120 行）之后仍有 26 行直接写的 `rgba()`。其中：
  - 同一状态出现两种色相：`.alert-banner--ok`（第 924–928 行）用 `rgba(126, 168, 197, …)`，
    而 `--status-info` 是 `#79A7BD`（121, 167, 189）；`.alert-banner--warn`、`.entry-review-badge`、
    `.entry-issue-list span`（第 930–934、1148、1164 行）用 `rgba(214, 166, 93, …)`，而 `--status-warning` 是
    `#D6924D`（214, 146, 77）。`.inline-badge.is-warning`（第 290 行）与 `.status-badge--warning`（第 576 行）又用后者。
  - 错误红在不同组件里用了 0.06、0.10、0.12、0.18、0.28 五种透明度（第 581、920–921、1314–1332 行）。
  - 手机底栏 `rgba(8, 10, 9, 0.96)`（第 2118 行）、选区与滚动条（第 147、197–215、656–660 行）都是只适合深色的字面量。
- 组件内：`FundCurveChart.vue:819` 休市带 `rgba(255, 255, 255, 0.022)`、`:855` 十字线
  `rgba(241, 239, 233, 0.35)`，都以白色为基，换到浅底上会消失；`ui/Button.vue:30` 主按钮悬停 `#F4F1E8`、
  `:36` 危险按钮 `rgba(216, 121, 121, …)`；`BenchmarkSelect.vue:196,237` 两处 `rgba(243, 239, 230, 0.06)`。
- 第二事实源：`main.css:3-37` 的 `@theme` 块把 `--color-surface`、`--color-rise` 等再写了一遍十六进制值。
  代码中没有找到使用这些 Tailwind 工具类（如 `bg-surface`、`text-rise`）的地方，切主题时它们不会跟着变。
- 根与外壳只认深色：`main.css:41` 只有 `color-scheme: dark`；全项目没有 `prefers-color-scheme`、
  `prefers-contrast`、`forced-colors` 媒体查询；`frontend/index.html:7` 的 `theme-color` 是 `#090B0A`，不等于任何 token。

### 2.3 深色取值放到白底上全部不达标（对应 1.4、1.8）

现有 token 直接用于浅色底的实测（`#F6F5F1` 是假设的浅色画布，仅用于估量）：

| token | 值 | 白底 `#FFFFFF` | 假设浅底 `#F6F5F1` | 正文门槛 |
| --- | --- | --- | --- | --- |
| 涨 `--market-up-strong` | `#E0787B` | 2.94 | 2.70 | 4.5 |
| 跌 `--market-down-strong` | `#54C397` | 2.18 | 2.00 | 4.5 |
| 品牌金 `--brand-accent` | `#C9AE70` | 2.15 | 1.97 | 4.5（焦点环 3） |
| 曲线主色 `--chart-line-primary` | `#CDB16F` | 2.08 | 1.90 | 3 |
| 三级文字 `--text-tertiary` | `#85827A` | 3.84 | 3.52 | 4.5 |
| 状态 `--status-live` | `#79A7BD` | 2.60 | 2.38 | 3 |
| 警告 `--status-warning` | `#D6924D` | 2.60 | 2.38 | 3 |
| 错误 `--status-error` | `#D87979` | 3.03 | 2.78 | 3 |

- 度量工具也只认一套主题：`frontend/scripts/check-contrast.mjs:17-35` 的 `readTokens` 对每个 token
  只取第一次出现的值，加了浅色块也测不到；`PAIRS` 没有基准线、焦点环、状态色底与字的组合。

## 3. 下一步提议

按影响／成本排序。每条只改列出的文件；涉及产品语义的标「需确认」。全部提议都不新增页面、
不改信息架构，也不碰 AI 与模拟功能。

| # | 提议 | 影响／成本 | 所属批次 |
| --- | --- | --- | --- |
| 1 | 语义色纠偏 | 高／低 | 2.4 合并后的小修，或并入 2.5 |
| 2 | 对比度脚本支持双主题 | 高／低 | 2.5 前置 |
| 3 | 外观策略：跟随系统 | 高／低 | 2.5 决策 |
| 4 | 状态色、图表与外壳色值 token 化 | 高／中 | 2.5 前置 |
| 5 | 浅色语义色单独定值 | 高／中 | 2.5 |
| 6 | `@theme` 改为引用 `:root` | 中／低 | 2.5 前置 |
| 7 | 减弱动效时保留非位移反馈 | 中／低 | 后续批次 |
| 8 | 图表浅色适配 | 中／中 | 2.5 |
| 9 | 选中态加非颜色提示，适配高对比度与强制色 | 中／中 | 后续批次 |

### 提议 1：语义色纠偏

- 目标文件：`frontend/src/views/FundsView.vue`（`badgeClass`）、`frontend/src/assets/main.css`
  （`.inline-badge.is-accent`，新增 `--chart-line-benchmark`）、`frontend/src/theme/index.ts`
  （`chartColors.benchmark`）、`frontend/src/components/FundDetailPanel.vue:782`（需确认）。
- 期望效果：「估」改用中性或 `--status-info` 样式；基准线有独立 token，取值与 `--color-flat` 不同且对面板 ≥3:1；
  平均偏差改为中性色加「偏高／偏低」文字（需确认）。暖金只剩品牌、焦点、基金曲线三种用途。
- 验收：`rg -n "brand-accent|is-accent" frontend/src` 的每一处都能归入三种用途；`check-contrast.mjs`
  新增「基准线／面板 ≥3」一项并通过；估值页与详情面板 390 与 1440 视口截图对照。

### 提议 2：对比度脚本支持双主题

- 目标文件：`frontend/scripts/check-contrast.mjs`。
- 期望效果：按选择器块分别解析深色与浅色 token（如 `:root` 与 `@media (prefers-color-scheme: light)` 内的 `:root`），
  每套主题各跑一遍 `PAIRS`；新增组合：基准线／面板（3）、焦点环 `--brand-accent`／画布与面板（3）、
  涨跌／浮层（4.5）、各状态文字／其状态底色（4.5）。
- 验收：脚本输出两张表；把任一浅色 token 改成不达标值时退出码为 1；深色结果与 2.4 基线一致。

### 提议 3：外观策略定为跟随系统

- 目标文件：`frontend/src/assets/main.css`（`:root` 改为 `color-scheme: light dark`，浅色值放进
  `@media (prefers-color-scheme: light)`）、`frontend/index.html`（两条带 `media` 的 `theme-color`，值等于各自的 `--bg-canvas`）。
- 期望效果：首版不做应用内开关、不做持久化（依据 1.9）。若之后确需手动切换，再做「跟随系统／浅／深」三态，
  存 `localStorage`，并在 `index.html` 首帧前应用，避免闪白；这一步需单独确认。
- 验收：Chrome DevTools Rendering 分别模拟 light／dark，四页无闪烁、无残留深色块；iPhone Safari 切换系统外观后状态栏颜色随之变化（真机项）。
  `theme-color` 的 `media` 支持有限，不支持的浏览器回落到默认值，记录实测结果即可。

### 提议 4：状态色、图表与外壳色值 token 化

- 目标文件：`frontend/src/assets/main.css`（2.2 列出的 26 行，2.4 负责的投影除外）、`ui/Button.vue:30,36`、
  `BenchmarkSelect.vue:196,237`、`FundCurveChart.vue:819,855`。
- 期望效果：在 `:root` 新增 `--status-{info,warning,error}-bg`、`--status-*-border`、`--action-primary-hover`、
  `--nav-translucent-bg`、`--selection-bg`、`--scrollbar-thumb`、`--chart-crosshair`、`--chart-break-zone` 等明确色值（依据 1.7，
  不用多层半透明叠加）；警告与信息各统一为一种色相；错误红透明度收敛到两档。
- 验收：`rg -n "rgba?\(|#[0-9a-fA-F]{3,8}\b" frontend/src --glob '*.vue'` 无结果；`main.css` 中的字面色值只出现在 token 块内；
  深色四视口截图除统一色相处外无可见变化。建议在 2.4 合并后再做，避免与边框瘦身同改 `main.css` 冲突。

### 提议 5：浅色语义色单独定值

- 目标文件：`frontend/src/assets/main.css` 的浅色 token 块；`docs/design-standards.md` 的颜色语义一节（先改标准，由该文档负责人执行）。
- 期望效果：涨仍为红、跌仍为绿（A 股口径不变），取更深的色调，使正文 ≥4.5:1；品牌金取深一档，焦点环对相邻底色 ≥3:1；
  曲线主色与基准线 ≥3:1；三级文字 ≥4.5:1。层级按 Carbon 浅色模型处理（画布与面板交替），不照搬深色的「逐层变亮」。
- 验收：提议 2 的脚本在浅色主题全部 OK，涨跌与三级文字不出现 THIN；四视口浅色截图；正负号仍在。

### 提议 6：`@theme` 改为引用 `:root`

- 目标文件：`frontend/src/assets/main.css:3-37`。
- 期望效果：颜色类条目改为 `@theme inline { --color-surface: var(--bg-surface); … }`，或删除未使用的颜色条目。
  Tailwind 文档说明：theme 变量引用其他变量时应使用 `inline`，否则工具类可能解析到意外的值。
- 验收：`pnpm build` 通过；构建产物 CSS 中 `#101310` 等深色字面量只出现在 `:root` 定义里；四页截图无变化。

### 提议 7：减弱动效时保留非位移反馈

- 目标文件：`frontend/src/assets/main.css:1967-1975`（全局减弱动效规则）、`:2831-2844`（`value-flash`）。
- 期望效果：`prefers-reduced-motion: reduce` 下继续取消位移类动画（抽屉滑入、`dead-end` 抖动），
  保留 `value-flash` 这类只改背景色的状态反馈（依据 1.12）。这会改变 `docs/design-standards.md`「reduced-motion 下关闭动效」的口径，需先改标准。
- 验收：DevTools 模拟 reduce 后，抽屉无滑动、按钮无抖动，估值刷新时数值单元格仍有一次底色提示；`frontend/scripts` 全部脚本通过。

### 提议 8：图表浅色适配

- 目标文件：`frontend/src/components/FundCurveChart.vue`（`.curve-chart__break-zone`、`__crosshair`、`__last-point`、`__hover-point`）。
- 期望效果：休市带与十字线取提议 4 的图表 token；末点与悬停点的描边改为图表实际所在面板的底色 token（现为
  `--bg-sunken` 与 `--bg-base`），浅色下不出现深色描边圈。网格与零线的取值仍归 2.4，这里只补浅色值。
- 验收：浅色主题下盘中、历史、缺口、单点四种曲线在 390 与 1440 视口截图；十字线与曲线对面板 ≥3:1；
  `frontend/scripts/test-curve-chart.mjs` 通过。

### 提议 9：选中态加非颜色提示，适配高对比度与强制色

- 目标文件：`frontend/src/views/FundsView.vue:835-836`（`.mobile-filter-tab`）、`frontend/src/assets/main.css`
  （新增 `@media (prefers-contrast: more)` 与 `@media (forced-colors: active)`）。
- 期望效果：手机筛选 Tab 选中与未选中之间，文字只差 2.09:1（`#F1EFE9` 对 `#AAA79F`），底色只差 1.06:1，
  达不到 1.4.1 认可的 3:1 明度差；加下划线或字重变化作为第二提示。`prefers-contrast: more` 下三级文字改用次要文字值、
  边框取 `--border-strong`。强制色模式下 `box-shadow` 会被强制为 `none`（MDN），而 `.alert-banner` 与
  `ui/Button.vue` 的 outline 变体用内阴影画边界，需在该模式下补 `border`。
- 验收：DevTools 模拟 `prefers-contrast: more` 与 `forced-colors: active` 的四页截图：选中 Tab、提示条边界、焦点环均可辨；
  macOS「增强对比度」真机复核一次（Apple HIG 建议在 Increase Contrast 下测试）。

## 4. 来源清单

访问日期均为 2026-10-30。

- Apple Human Interface Guidelines · Dark Mode：<https://developer.apple.com/design/human-interface-guidelines/dark-mode>
- Apple Human Interface Guidelines · Color：<https://developer.apple.com/design/human-interface-guidelines/color>
- Apple Human Interface Guidelines · Motion：<https://developer.apple.com/design/human-interface-guidelines/motion>
- Android Developers · Material Design 3 in Compose（Elevation 一节）：<https://developer.android.com/develop/ui/compose/designsystems/material3>
- Material Components for Android · Dark theme：<https://github.com/material-components/material-components-android/blob/master/docs/theming/Dark.md>
- Material Components for Android · Color（M3 色彩角色表与 Using Surface Colors）：<https://github.com/material-components/material-components-android/blob/master/docs/theming/Color.md>
- Android Developers · Wear OS Material 2.5 Color（该页面向手表，只引用其去饱和条款）：<https://developer.android.com/design/ui/wear/guides/m2-5/styles/color>
- IBM Carbon Design System · Color overview：<https://carbondesignsystem.com/elements/color/overview/>
- IBM Carbon Design System · Data visualization color palettes：<https://carbondesignsystem.com/data-visualization/color-palettes/>
- Radix Colors · Understanding the scale：<https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale>
- Radix Colors · Installation：<https://www.radix-ui.com/colors/docs/overview/installation>
- impeccable.style 首页与文档：<https://impeccable.style/>、<https://impeccable.style/docs>
- impeccable 参考文件（GitHub `pbakaus/impeccable`，`main` 分支）：
  - operate.md：<https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/operate.md>
  - colorize.md：<https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/colorize.md>
  - animate.md：<https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/animate.md>
  - quieter.md：<https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/quieter.md>
  - craft-floor.md：<https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/craft-floor.md>
- W3C · Understanding WCAG 2.2 SC 1.4.1 Use of Color：<https://www.w3.org/WAI/WCAG22/Understanding/use-of-color>
- W3C · Understanding WCAG 2.2 SC 1.4.11 Non-text Contrast：<https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast>
- MDN · font-variant-numeric：<https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric>
- MDN · light-dark()：<https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/light-dark>
- MDN · meta name="theme-color"：<https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/meta/name/theme-color>
- MDN · prefers-contrast：<https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-contrast>
- MDN · forced-colors：<https://developer.mozilla.org/en-US/docs/Web/CSS/@media/forced-colors>
- Tailwind CSS · Theme variables（Referencing other variables 一节）：<https://tailwindcss.com/docs/theme>

引文为原文节选，中文部分为转述。
