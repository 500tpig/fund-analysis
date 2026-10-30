# 界面设计标准

2026-10-30 建立。适用于全部前端页面与后续 UI 批次（2.4 暗色深化、浅色主题、PWA 与第三批候选），
作为每批交付的验收尺子。token 取值以 `frontend/src/assets/main.css` 为唯一来源；本文件规定
「哪些值用在什么地方」，两者冲突时先改本文件再改代码。

## 颜色语义

- 暖金 `--brand-accent` #C9AE70 只用于品牌、焦点与基金曲线（曲线线色 `--chart-line-primary` #CDB16F），
  不做通用强调色或大面积背景。
- 涨跌固定 A 股口径：涨 `--market-up-strong` #E0787B（红）、跌 `--market-down-strong` #54C397（绿）、
  平 `--color-flat` #85827A。系统状态单独一套，不复用涨跌色：`--status-live` #79A7BD、
  `--status-warning` #D6924D、`--status-error` #D87979。
- 深色画布实测对比度（底色 `--bg-surface` #101310）：主文字 16.3:1、次要 7.8:1、三级 6.3:1（`#98958D`）、
  涨 6.4:1、跌 8.6:1、金 8.7:1，全部达到 WCAG AA。12 px 小字优先使用
  `--text-secondary`；新颜色先加进 `:root`／`@theme`，不在页面里直接写色值。

## 层次与表面

- 层次用表面色阶表达（由暗到亮）：`--bg-sunken` #030404 → `--bg-canvas` #0A0C0B → `--bg-surface` #101310 → `--bg-surface-hover` #161A17 → `--bg-elevated` #1C201D → `--bg-elevated-hover` #222723。
  同一元素不同时用细描边与宽阴影；弹层用 `--shadow-overlay`。
- 圆角只用三档：控件 `--radius-control` 8 px、容器与面板 `--radius-container`／`--radius-panel` 10 px、
  手机底部抽屉顶部 18 px（`main.css:2587` 的 ≤768 px 规则）。不新增档位。

## 字体与数字

- 字体取 `--font-ui` 系统栈；数字统一 `.font-numeric`（`tabular-nums lining-nums`），
  金额与百分比等宽、固定小数位、右对齐。
- 手机字号下限 12 px；手机列表名称与金额 15 px、副行与表头 13 px。
- 「数值不跳动」优先于字号美观：同列数字保持等宽，涨跌色不改变字重。

## 动效

- 时长只用四档 token：`--dur-micro` 120 ms（点击反馈）、`--dur-ui` 200 ms（状态与浮层）、
  `--dur-panel` 与 `--dur-page` 280 ms（面板与页面）；单次动画不超过 300 ms。
- 只动 `transform` 与 `opacity`，不用宽高、外边距或颜色做位移。
- 手势 1:1 跟手、松手才吸附，动画可中断；不做整页转场、视差滚动与滚动劫持。
- `prefers-reduced-motion: reduce` 下关闭动效；`main.css` 已有总开关，组件内新增动画需自查。

## 触控与尺寸

- 可点元素命中区不小于 44×44 px（可用伪元素扩展，如抽屉关闭按钮）；可点元素
  `touch-action: manipulation`，不依赖 hover。
- 键盘可达：`:focus-visible` 统一 2 px `--brand-accent` 描边、偏移 2 px；，抽屉打开时焦点进入关闭按钮、关闭后回到触发行。
- 四视口 390×844、430×932、768×1024、1440×900 无横向溢出、无文字覆盖、无固定元素遮挡。

## 状态完成度

- 每个页面覆盖空、加载、失败与边界状态（单点／缺口曲线、空列表、失败后保留输入、删除确认）。
- 复用既有状态类与组件（`.skeleton`、`is-unreadable`、`EmptyState`、`ToastContainer`），
  不新增并行实现。

## 每批交付的验收清单

- 四视口无横向溢出、无小于 12 px 文字、无小于 44×44 px 触控元素，桌面布局无退化。
- 动效取自 token、只动 `transform` 与 `opacity`、reduced-motion 可关。
- `pnpm typecheck`、`pnpm lint`、`pnpm build` 与 `frontend/scripts` 全部脚本通过。
- 真机项（安全区、软键盘、返回手势、Safari）在生产站点验收，不用浏览器结果替代。
