# 手机体验第二批方案：曲线读点、视觉深化与其余页面铺样板

状态：2026-10-30，**2.1–2.3 已实施并通过本地四视口浏览器验收，并作为 `20261030-mobile-ui-2`
发布上线（纯前端）；2.4 暗色深化与 2.5 浅色主题已于 2026-10-30 实施并通过本地对比度与四视口验收，并作为
`20261030-mobile-ui-3` 发布上线（纯前端）；未做真机验收**。2.6 未开工。第一批（手机样板：触摸基础、`100dvh`
与安全区、底部抽屉、返回栈、数值微光）已于 2026-10-29 作为 `20261029-mobile-ui-1` 发布，见
[发布记录](evidence/mobile-ui-release-20261029.md) 与 [移动端响应式计划](mobile-responsive-plan.md)；
第二批发布记录见 2.1–2.3 [发布记录](evidence/mobile-ui-release-20261030.md) 与
2.4–2.5 [发布记录](evidence/mobile-ui-release-20261030-2.md)。

一个对话只做一个子项，启动前由用户确认。2.1、2.2 与 2.3 已分别作为独立交付完成。

## 实施前基线（2026-10-29）

- 手机无法读点：`frontend/src/components/FundCurveChart.vue:318` 为
  `if (e.pointerType !== 'mouse') return`，十字线与 tooltip 只响应鼠标。
  提交 9af818b 的说明也写明「触摸允许纵向滚动，十字线仅响应鼠标」，读点当时有意留空。
- 尺寸写死：`FundCurveChart.vue:52` `height = 290`，`:53` `plot.left = 68`；
  组件内没有任何 `@media`（实测 0 处）。390 px 宽手机上图表 358×290，Y 轴留白占 19%。
- 字号越界：轴标签为 11 px、午休文字为 10 px，低于本项目的手机 12 px 下限。
- tooltip 跟随指针 x 位置（`FundCurveChart.vue:595` 起），手机上会遮挡曲线本身。
- 详情面板内容很长：390×844 实测抽屉内滚动高度 2912 px、可视 740 px，读点需要固定读数区。

## 2.1 曲线手机读点与尺寸档

目标：手机上按住曲线即可读出任意时间点的数值，且不牺牲纵向滚动。

- 指针分支改为同时处理触摸与鼠标（`pointerdown` / `pointermove` / `pointerup`）。
- 保持 `touch-action: pan-y` 不变：横向拖动进入读数、纵向拖动继续滚动页面，不调用
  `preventDefault`，不改手势语义。
- 轻触即读：`pointerdown` 落在绘图区内立即高亮最近点，不必先滑动。
- 读数区固定在图表上方（不跟随手指），显示时间、各序列数值与相对基准的涨跌色；
  松手后保留读数直到再次轻触或离开详情，便于前后对比。
- 读数区复用图例，分时同时显示日期、时间与净值；手机不显示跟随指针的浮动 tooltip。
  本项只固定读数区在图表上方的位置，详情滚动时的吸顶属于 2.2。
- 首次进入手机详情给一次「按住曲线可查看任意时间点」的提示，可关闭；
  使用 `fund-analysis:curve-touch-hint-seen` 保存已展示状态，关闭、重开或刷新页面不重复出现。
- 尺寸分档（用 `@media (max-width: 768px)`，组件内首次出现媒体查询）：
  图高 290 → 240；`plot.left` 68 → 48；轴标签统一为 12 px；Y 轴最多 3 个刻度、
  A 股模式 X 轴保留 09:30 / 11:30·13:00 / 15:00 三处；水平网格线在窄屏降为 1 条。
- 详情当前使用 `session-type="raw"`，继续展示实际起止时间，历史业绩展示实际起止日期；
  不把海外跨日走势套成 A 股时段。A 股模式的三处刻度另用组件样例覆盖四视口。
- 单点不显示交互读数；分时按实际最小采样间隔的半步吸附，超出范围的缺口和 A 股午休不读点。
  对比序列缺少所选时间点时显示「—」，不再沿用前值或首点。
- 手机金额轴使用紧凑数值（如 `+1.23K`），上方读数仍保留完整金额与两位小数。
- 曲线可通过左右方向键、Home / End 读点；触摸或键盘读数不会被鼠标离开事件清除。
  基准选择器与菜单选项的手机触控高度补齐到 44 px。

### 2.1 本地验收（2026-10-29）

使用隔离 SQLite 样例库与组件边界样例，未读取真实持仓库或模拟库。浏览器为 agent-browser
管理的 Chromium，分时与历史主流程使用本轮 `frontend/dist` 构建。触摸通过浏览器原生输入验证，
包含 `pointercancel` 和详情实际滚动，
不以手动触发 PointerEvent 代替手势验收。测量结果见 [results.json](evidence/mobile-curve-20261029/results.json)。

| 视口 | 详情图表尺寸 | 左边距 / 轴字号 / 网格线 | 结果与截图 |
| --- | --- | --- | --- |
| 390×844 | 358×240 px | 48 px / 12 px / 1 条 | 通过：[分时](evidence/mobile-curve-20261029/intraday-390.png)、[历史双序列](evidence/mobile-curve-20261029/history-390.png) |
| 430×932 | 398×240 px | 48 px / 12 px / 1 条 | 通过：[分时](evidence/mobile-curve-20261029/intraday-430.png)、[历史双序列](evidence/mobile-curve-20261029/history-430.png) |
| 768×1024 | 736×240 px | 48 px / 12 px / 1 条 | 通过：[分时](evidence/mobile-curve-20261029/intraday-768.png)、[历史双序列](evidence/mobile-curve-20261029/history-768.png) |
| 1440×900 | 362×290 px | 68 px / 12 px / 5 条 | 通过：[分时](evidence/mobile-curve-20261029/intraday-1440.png)、[历史双序列](evidence/mobile-curve-20261029/history-1440.png) |

- 四视口无页面、详情或图表横向溢出，曲线及详情内可见文字不小于 12 px。三档手机的区间按钮、
  基准选择按钮均达到 44×44 px；提示关闭按钮为 44×44 px，菜单选项高 44 px。
- 三档手机的分时与历史曲线均通过轻触、横向拖动、松手保留、纵向滚动验收；
  读数区高度与相对图表位置不变，纵向手势触发取消后恢复先前读数，指针事件未调用 `preventDefault`。
- A 股组件的三处手机刻度、桌面四处刻度通过四视口测量，
  示例截图：[390](evidence/mobile-curve-20261029/cna-390.png)、[430](evidence/mobile-curve-20261029/cna-430.png)、
  [768](evidence/mobile-curve-20261029/cna-768.png)、[1440](evidence/mobile-curve-20261029/cna-1440.png)。
- 边界样例通过：[单点](evidence/mobile-curve-20261029/single-390.png)、
  [断档](evidence/mobile-curve-20261029/gap-390.png)、[跨日](evidence/mobile-curve-20261029/overnight-390.png)、
  [基准缺值](evidence/mobile-curve-20261029/comparison-gap-390.png)、
  [金额轴与完整读数](evidence/mobile-curve-20261029/money-390.png)、[首次提示](evidence/mobile-curve-20261029/hint-390.png)。
  提示关闭后刷新重开不再出现；桌面鼠标离开后清除十字线，图高与详情分栏保持原尺寸。
- 空数据转为有数据时重新绑定尺寸观察，浏览器实测 SVG 坐标与容器均为 358×240 px、左边距 48 px，
  不再沿用空容器挂载时的 720×290 默认坐标。
- 自动检查通过：`pnpm typecheck`、`pnpm lint`、`pnpm build` 及 `frontend/scripts/*.mjs`
  全部 7 个脚本，含 `check-routes.mjs`、新增 `test-curve-chart.mjs` 与既有 `test-drawer-history.mjs`。
  曲线回归执行组件真实 setup，覆盖触摸／鼠标／键盘、滚动取消、单点、缺口、基准缺值、跨日与金额轴。

未覆盖：真实 iPhone／Android 的触摸、侧滑返回、返回键、安全区与软键盘；未发布到生产环境。
后续子项按各节状态单独启动。

## 2.2 详情面板手机细化

状态：2026-10-29 本地实施与浏览器验收完成，未发布、未做真机验收。

- 顶部读数条与抽屉头部的层级、间距统一，滚动详情时读数条留在可视区。
- 详情各分区（行情、收益、持仓、准确度、关联）在手机上的间距与分隔统一到样板的节奏。
- 曲线与下方区块之间补固定的分隔，避免长滚动中「曲线贴着下一块」。

实施结果：

- 手机使用一个吸顶区，复用当前曲线的现有图例；读数条到达抽屉头部下沿后保持可见。
  分时、历史业绩与已记录官方测算曲线通过触摸或键盘读点切换，不复制读数或绘制第二份图例。
- 只在实际读点时切换吸顶内容。区间按钮仍随曲线滚动；基准选择器留在所属图例中，
  打开菜单不会搬动正在点击的控件。Esc 关闭菜单后，抽屉继续保持打开。
- 分区间距与顶部内边距统一为 20 px，使用 1 px 分隔线；数据行文字为 13 px 字号、20 px 行高，
  比较表与收益记录的行高不低于 32 px。标题保留 15 px，辅助文字不低于 12 px。
- 空数据与单点不显示吸顶区；关闭或返回后随详情一起清理。桌面图例保留原位，
  曲线图高、数据行字号和曲线切换区间距保持原样。不改后端 API、收益口径和模拟行为。

### 2.2 本地验收（2026-10-29）

使用本轮 `frontend/dist` 构建、隔离样例库及浏览器原生触摸输入；未访问真实库或生产服务。
完整测量与断言结果见 [results.json](evidence/mobile-detail-20261029/results.json)。

| 视口 | 结果 | 截图 |
| --- | --- | --- |
| 390×844 | 三类曲线可接管吸顶区；纵向滚动保留读数，关闭按钮可点 | [分区间距](evidence/mobile-detail-20261029/curve-spacing-390.png)、[分时吸顶](evidence/mobile-detail-20261029/intraday-sticky-390.png)、[历史吸顶](evidence/mobile-detail-20261029/history-sticky-390.png)、[收益吸顶](evidence/mobile-detail-20261029/official-sticky-390.png) |
| 430×932 | 同上，无横向溢出或小于 12 px 的详情文字 | [分区间距](evidence/mobile-detail-20261029/curve-spacing-430.png)、[分时吸顶](evidence/mobile-detail-20261029/intraday-sticky-430.png)、[历史吸顶](evidence/mobile-detail-20261029/history-sticky-430.png)、[收益吸顶](evidence/mobile-detail-20261029/official-sticky-430.png) |
| 768×1024 | 同上，头部与吸顶条之间无内容穿透 | [分区间距](evidence/mobile-detail-20261029/curve-spacing-768.png)、[分时吸顶](evidence/mobile-detail-20261029/intraday-sticky-768.png)、[历史吸顶](evidence/mobile-detail-20261029/history-sticky-768.png)、[收益吸顶](evidence/mobile-detail-20261029/official-sticky-768.png) |
| 1440×900 | 吸顶区隐藏，两份图例均在原位；图表 362×290 px，切换区间距仍为 14 px | [桌面](evidence/mobile-detail-20261029/desktop-1440.png) |

- 三档手机的分时与历史主曲线完全滚出后仍保留选中读数；收益曲线验证了吸顶内容接管与滚动保留。
  指针事件未调用 `preventDefault`，纵向手势继续通过 `pointercancel` 交给浏览器。
- 吸顶菜单的 7 个选项均实测为 118×44 px，命中正确；未激活曲线中的菜单也能一次打开，
  不抢占当前读数。键盘读点后焦点仍在曲线上，返回一次即可关闭详情，URL 保持 `/valuation`，没有残留吸顶区。
- `pnpm typecheck`、`pnpm lint`、`pnpm build` 与 `frontend/scripts/*.mjs` 全部 7 个脚本通过。
  既有曲线回归补入读数归属、单点／空数据与桌面不吸顶的断言；抽屉返回栈脚本继续通过。

未覆盖：iPhone／Android 真机、Safari 与屏幕阅读器实测；未发布到生产环境。

## 2.3 其余三页铺样板

把第一批的规范复用到 `/holdings`、`/import`、`/simulation`：触摸基础与安全区已随公共层生效，
本项做页面内的字号节奏、行高、抽屉内表单间距、按钮尺寸与状态反馈的对齐，不改业务逻辑与接口。

状态：2026-10-29 本地实施与浏览器验收完成，未发布、未做真机验收。

实施结果：

- 手机持仓列表采用 15 px 名称／金额、13 px 副行，行高为 21／18 px；模拟列表采用
  15／13 px 字号、22／20 px 行高。录入页分区标题、候选名称和主要金额采用 15 px，
  表单标签采用 13 px，辅助标记与基金代码不低于 12 px。
- 持仓编辑表单按 20 px 分组，标签到输入框为 6 px；手机与 768 px 视口均使用单列，
  录入候选字段间距同为 20 px。修正原有 `space-y-*` 被全局 margin 规则覆盖的问题，
  分组实际使用 flex gap；桌面继续保留持仓双列与录入三列字段布局。
- 按钮、输入框、折叠入口与订单筛选达到 44 px；模拟运行详情的「查看订单」入口达到
  68×44 px。桌面复制与行菜单通过命中区扩展达到 44×44 px，且不跨到相邻行。
  公共导航的高度补到 44 px，桌面导航说明由 11 px 补到 12 px。
- 持仓行、候选匹配、写入方式、折叠入口与模拟订单使用既有表面色反馈按下状态，焦点环继续有效。
  保留既有加载、失败、完成、删除确认、替换确认与返回栈，不增加提示组件、依赖或业务逻辑。

### 2.3 本地验收（2026-10-29）

沿用 2.1／2.2 的隔离样例与构建产物验收方式：agent-browser 管理的 Chromium 访问本轮
`frontend/dist`，使用独立 SQLite 持仓库与 fixture 模拟库。识图与行情使用测试 provider，
未访问真实库、识图服务或生产服务。测量方法见 [验收记录](evidence/mobile-pages-20261029/README.md)，
完整结果见 [results.json](evidence/mobile-pages-20261029/results.json)。

| 视口 | 结果 | 三页截图 |
| --- | --- | --- |
| 390×844 | 26 个状态通过；无横向溢出、文字低于 12 px 或命中区低于 44×44 px | [持仓](evidence/mobile-pages-20261029/holdings-390.png)、[录入](evidence/mobile-pages-20261029/import-candidates-390.png)、[模拟](evidence/mobile-pages-20261029/simulation-390.png) |
| 430×932 | 25 个状态通过，同上 | [持仓](evidence/mobile-pages-20261029/holdings-430.png)、[录入](evidence/mobile-pages-20261029/import-candidates-430.png)、[模拟](evidence/mobile-pages-20261029/simulation-430.png) |
| 768×1024 | 25 个状态通过，同上；持仓与录入表单仍为单列 | [持仓](evidence/mobile-pages-20261029/holdings-768.png)、[录入](evidence/mobile-pages-20261029/import-candidates-768.png)、[模拟](evidence/mobile-pages-20261029/simulation-768.png) |
| 1440×900 | 25 个状态通过，同上；桌面表格与分栏保持 | [持仓](evidence/mobile-pages-20261029/holdings-1440.png)、[录入](evidence/mobile-pages-20261029/import-candidates-1440.png)、[模拟](evidence/mobile-pages-20261029/simulation-1440.png) |

- 101 个状态覆盖持仓列表／筛选空结果、增改表单、菜单、删除确认和保存失败；
  录入选择、识别中／失败、候选与展开字段、合并／替换预览、写入中／失败和完成；
  模拟列表、订单筛选、两类详情、详情失败、暂停中／失败和暂停结果。写入仅作用于隔离样例库。
- [持仓表单](evidence/mobile-pages-20261029/holding-create-390.png) 内滚动到底后，
  [最后字段与焦点环](evidence/mobile-pages-20261029/holding-form-end-390.png) 保持在固定操作栏上方；
  保存失败保留输入。替换确认勾选前禁用提交，删除操作继续先打开确认框。
- 模拟 [运行详情](evidence/mobile-pages-20261029/simulation-run-390.png) 跳到
  [订单详情](evidence/mobile-pages-20261029/simulation-order-390.png) 后只有一个对话框；
  四视口均可返回一次关闭详情，URL 保持 `/simulation`。
- 桌面持仓数据行仍为 61／59 px，字号与行高仍为 14／22 px；持仓编辑抽屉仍宽 520 px，
  模拟详情仍宽 620 px、圆角为 0。复制与行菜单的扩展区域通过实际命中测试，尺寸 44×44 px，
  均留在本行内。键盘 `/` 可聚焦搜索框，显示 2 px 焦点环。
- `pnpm typecheck`、`pnpm lint`、`pnpm build` 与 `frontend/scripts/*.mjs` 全部 7 个脚本通过，
  包含曲线与抽屉返回栈回归。没有修改 API、数据结构或提交逻辑。

未覆盖：iPhone／Android 真机、安全区、软键盘、Safari 与屏幕阅读器实测；未发布到生产环境。
2.4、2.5 已完成并随 `20261030-mobile-ui-3` 发布；2.6 未启动。

## 2.4 暗色主题深化（视觉层，不改信息架构）

- 层级：减少边框依赖，用表面色阶（`--bg-surface` / `--bg-elevated` / `--bg-sunken`）表达层次；
  统一卡片、抽屉、弹层的圆角与投影档位。
- 对比度：正文与次要文字达到 WCAG AA（正文 ≥4.5:1、次要 ≥3:1），现在 `--text-secondary`
  与 `--text-tertiary` 在深色画布上的比值需要实测后调整。
- 数字排版：金额与百分比统一等宽数字、固定小数位与右对齐；涨幅色与跌幅色在深色底上的
  可辨性单独校核（现为 `#E0787B` / `#54C397`）。
- 图表：网格、零线、基准线与填充透明度的层次重新定值；线宽在 2× 以上屏幕上不低于 1.5 px。
- 状态：焦点环、骨架屏、加载与空状态、微动效时长统一取自 token。
- 验收含四视口截图与对比度实测，桌面与手机同时看，不允许为了观感牺牲表格密度。

### 实施前基线（2026-10-30 实测）

- 对比度（`node frontend/scripts/check-contrast.mjs`）：主文字 16.27、次要 7.78、三级 4.87（余量小于 0.5，标 THIN）、
  涨 6.36、跌 8.58、品牌金 8.70、状态色 6.17–7.20、曲线主色 9.01，全部过 WCAG AA；三级文字在面板底色上余量最小。
- 表面层级缺陷：`--bg-elevated` 与 `--bg-surface-hover` 取值相同（#161A17），浮层与悬停无法靠色阶区分。
- 边框依赖：`main.css` 37 处、组件 17 处使用 `--border-*` 表达分隔。
- 时长硬编码：样式表与组件内非 token 时长 200ms×4、280ms×3、120ms×3、180ms×2、150ms×2、0.15s×2、160ms×1；
  `value-flash` 900ms 与 `dead-end shake` 320ms 为刻意例外。
- `.ui-press` 已定义并在 6 个文件使用；`font-numeric` 覆盖 9 个组件。
- 图表：网格 `stroke-width` 1、零线 1.25、主曲线 1.75／2；`--chart-grid` alpha 0.18、`--chart-zero-line` alpha 0.58。
- 度量工具：`frontend/scripts/check-contrast.mjs`（可用 `--json`）。

### 实施与验收结果（2026-10-30）

- 表面色阶（由暗到亮）：`--bg-sunken` `#030404` < `--bg-canvas` `#0A0C0B` < `--bg-surface` `#101310` < `--bg-surface-hover` `#161A17` < `--bg-elevated` `#1C201D` < `--bg-elevated-hover` `#222723`；三级文字 `--text-tertiary` 提亮至 `#98958D`（面板上 6.25:1、浮层悬停上 5.08:1），`--border-subtle` 下调至 `#1D221E`。
- 边框与 token 统一：`main.css` 删 11 处、留 26 处同面色发丝分隔；组件删 9 处、留 18 处，浮层统一去描边只用 `--shadow-overlay`。圆角、时长、缓动、焦点环与图表笔触全部取自 token，删除侧栏宽度过渡。
- 数字、按压与状态：`formatNav` 固定 4 位小数；新增 `.ui-press--row` 铺至手机列表行与底部导航（行高 60/76 px、底部导航 50 px 不变）；曲线与风险加载复用 `.skeleton--block`。
- 验收：`node frontend/scripts/check-contrast.mjs`（25 项文字对比度 + 5 档相邻表面层级全部 OK，无 THIN）、`verify-ui-polish.mjs`、`typecheck`、`lint`、`build`、`check-routes` 及全部前端脚本通过；390/430/768/1440 四视口 × 四页面与 6 个交互状态浏览器截图核对无溢出、无退化。

## 2.5 浅色主题（独立交付）

状态：2026-10-30 本地实施与浏览器验收完成，并随 `20261030-mobile-ui-3` 发布；未做真机验收。基线为 2.4（`de711d7`）。
确认纳入 [craft notes](ui-craft-notes.md) 提议 1–6、8；提议 7（减弱动效）与 9（高对比度／强制色）后置。

实施结果：

- 外观跟随 `prefers-color-scheme`，不增加手动开关或持久化；HTML 的两条 `theme-color` 分别对应浅／深画布色。
  浅色使用中性灰白表面、深金品牌／基金曲线与深灰实底主按钮，红涨绿跌和正负号保留。
- `main.css` 是颜色唯一来源，删除无调用的 `@theme` 颜色副本；按钮、基准选择器、状态底色／边界、
  遮罩、底栏、选区与滚动条均取自 token。输入框占位文字使用三级文字色。
- 「估」改用信息色；平均偏差使用中性色，正／负分别标「偏高／偏低」，零值只显示 `0.00 百分点`。
  其他通用强调回到中性或状态色；基准线复用 2.4 的 `--chart-benchmark`。
- 图表分别定义浅色网格、零线、基准线、十字线和休市带；末点／读点描边跟随实际画布或抽屉底色。
  沿用原有尺寸、读点、菜单和返回栈。
- 验收发现并补齐两处布局约束：档案来源链接的手机触控高度达到 44 px；估值列表补 `min-w-0`，
  长名称／大金额样例下右侧详情保持在可视区，桌面表格沿用容器内滚动。

### 2.5 本地验收（2026-10-30）

验收使用 agent-browser 管理的独立 Chromium 会话、本轮 `frontend/dist`、隔离 SQLite 持仓库与 fixture 模拟库；
识图、行情和曲线均使用测试 provider。录入与暂停／恢复只作用于隔离库，不代替生产暂停验收。
方法、截图与完整结果见 [验收记录](evidence/light-theme-20261030/README.md)。

- 四页 × 390／430／768／1440 px × 浅／深两主题，共 32 个基础状态通过，浅深布局尺寸一致；
  页面、分栏、抽屉外框和图表无横向溢出，手机文字不小于 12 px、控件命中区不小于 44×44 px。
- 44 个详情状态、84 个持仓／录入／模拟状态与 16 个图表组件样例通过，覆盖基准菜单与双序列、
  平均偏差正／负／零、未保存输入跨外观切换、加载／空／失败、删除／替换确认、运行／订单详情及返回。
  图表样例另覆盖 A 股午休、缺口和单点；浏览器键盘读点与既有触摸回归脚本通过。
- 双主题各 78 项检查无 FAIL，浅色全部 OK、无 THIN；深色新增检查有 6 项 THIN（弱涨色与十字线），
  均达到对应门槛。2.4 原有 25 项对比度与 5 档层级的数值保持一致。
  透明度合成、主题覆盖后的别名解析、缺失／循环 token 与浅色失败负例通过自检。
- `pnpm typecheck`、`pnpm lint`、`pnpm build` 与全部 8 个前端脚本通过。

未覆盖：iPhone／Android 真机、Safari 状态栏与 `theme-color` 行为、安全区、软键盘、返回手势、屏幕阅读器。
已于 2026-10-30 随 `20261030-mobile-ui-3` 发布（[发布记录](evidence/mobile-ui-release-20261030-2.md)）：
线上经 SSH 隧道四页 × 390／1440 × 深浅两主题检查通过；提议 7、9 与 2.6 继续等待单独启动。

## 2.6 PWA（独立交付）

`manifest.webmanifest`、图标（PNG，可用本机 `rsvg-convert` 从 SVG 生成）、
`apple-mobile-web-app-*` 元信息、standalone 下的安全区与返回栏适配、真机安装与更新验证。
依赖 2.1 之后的返回栈行为稳定，安装后没有系统返回栏，全靠应用内栈。

## 验收

- 四视口 390×844、430×932、768×1024、1440×900 无横向溢出、无小于 12 px 的文字、
  无小于 44×44 px 的触控元素，桌面无退化。
- 手指按住曲线任意位置能读出时间与数值；纵向滑动仍能滚动详情；松手后读数保留。
- `pnpm typecheck`、`pnpm lint`、`node scripts/check-routes.mjs`、`pnpm build` 与
  `frontend/scripts` 下全部脚本通过；`scripts/test-drawer-history.mjs` 继续通过。
- 真机：iPhone 侧滑返回、Android 返回键、安全区、持仓与录入表单的软键盘遮挡。

## 不做

- 看涨／看跌社区投票、加仓／减仓下单入口：真实交易在支付宝执行，应用不提供下单入口。
- 整页转场、视差滚动、滚动劫持、拖拽关闭面板：与「克制的产品级」口径和现有文档一致。
- 曲线双击缩放、双指缩放、区间切换（近 1 月／近 1 年）：属新功能，列入后续候选。
- 隐藏金额开关、收益日历、估值准确度榜、重仓股当日涨跌：仍为第三批候选，需用户确认。

## 本批之外的未完成项

- 阶段 7 收尾（另一窗口）：P0-A 暂停／恢复验收、撤销会话实测、Access cookie SameSite 记录、
  2027 年交易日历、交易日前只读指纹补档；`20261029-fund-profiles-1` 候选已于 10-30 以
  `20261030-mobile-ui-3` 为基线重切，待发布。
- 交易日真实时段验收（roadmap 后续任务 1）：盘中、收盘后至 19:00、净值部分披露后各看一次；
  顺带处理手机页头「刷新失败」措辞与 `latest_official` 下筛选行只剩「全部」时的显示。
- 多账号：产品语义变更（认证、数据隔离、托管他人数据），需先改 roadmap 立项。
- 阶段 8–11（AI 解释、新闻与候选计划、AI 模拟、操作日记）：未启动，未授权。
