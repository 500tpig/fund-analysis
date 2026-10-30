# 浅色主题本地验收（2026-10-30）

范围：[手机体验第二批 2.5](../../mobile-ux-batch2-plan.md)，纳入 craft notes 提议 1–6、8。
基线为 `e99e744`，已包含 2.4 暗色深化 `de711d7`。本次未发布，未访问真实持仓库、模拟库或生产服务。

## 环境与方法

- agent-browser 独立会话管理的 Chromium 153，使用 `set media light/dark` 模拟系统外观。
  四页及交互状态访问 `http://127.0.0.1:8895` 的本轮 `frontend/dist`；构建产物 SHA-256 保存在结果文件中。
- 使用 `/tmp/fund-light-theme-20261030.WfEO62` 内的独立 SQLite 持仓库与 fixture 模拟库。
  行情、基金检索、识图与曲线使用测试 provider；持仓含长名称、大金额、正负收益和混合官方／预估状态。
  准确度正、负、零样例为人造数据，仅用于核对显示。录入合并、暂停／恢复只写入隔离库。
- 临时验收服务以延迟和 503 响应覆盖加载、失败及失败后保留输入。
  图表边界另用临时 Vite 隔离页挂载实际 `FundCurveChart.vue`，不注册应用路由；该页面已移除。
- 量测 document、分栏、抽屉外框及图表的宽度；表格与详情的内部滚动宽度另列在 `tableScroll`／`detailScroll`。
  长名称／大金额样例的桌面表格保留容器内横向滚动，右侧详情保持可见。
- 手机字号按已布局文字的 computed font-size 检查；控件名义命中区计入 CSS 与伪元素扩展。
  表单最后字段聚焦后核对其底边位于固定操作栏上方。桌面字号与表格行高沿用原有密度。
- 菜单、表单、上传、确认与返回使用浏览器输入。图表浏览器读点以键盘验证，触摸分支继续由既有回归脚本覆盖。

## 结果

[results.json](results.json) 保存 176 个状态的颜色、尺寸、字号、命中区与描边量测，以及构建校验值和命令结果。
本目录保留其中 60 张代表截图。

| 视口 | 基础四页 × 双主题 | 详情 | 三页交互状态 | 图表组件样例 | 合计 |
| --- | --- | --- | --- | --- | --- |
| 390×844 | 8 | 14 | 42 | 8 | 72 |
| 430×932 | 8 | 8 | 0 | 0 | 16 |
| 768×1024 | 8 | 8 | 0 | 0 | 16 |
| 1440×900 | 8 | 14 | 42 | 8 | 72 |

- 基础四页的浅深布局尺寸一致，页面、分栏、抽屉外框和图表无横向溢出；手机字号与名义触控尺寸达标。
  示例：[浅色估值 390](valuation-light-390.png)、[浅色估值 1440](valuation-light-1440.png)、
  [深色估值 390](valuation-dark-390.png)、[深色估值 1440](valuation-dark-1440.png)。
- 「估」显示为信息色；平均偏差保留正负号与百分点，使用中性色，三种情况分别为
  [偏高](bias-0-light-390.png)、[偏低](bias-1-light-390.png)、[零值](bias-2-light-390.png)。
- 盘中、历史及基准切换通过，末点与读点描边匹配实际画布／抽屉底色：
  [盘中](intraday-light-390.png)、[历史双序列](history-light-390.png)、[基准菜单](benchmark-menu-light-390.png)。
  组件样例另覆盖 [午休带](component-cna-light-390.png)、[缺口](component-gap-light-390.png)、
  [单点](component-single-light-390.png) 与 [历史双序列](component-history-light-390.png)。
- 持仓覆盖加载、失败、筛选空结果、编辑、保存失败、菜单与删除确认；保存失败后保留 `987.65` 输入。
  未保存表单切换浅／深外观后继续保留该输入。
  示例：[浅色表单及焦点环](holding-form-light-390.png)、[深色保存失败](holding-save-error-dark-390.png)、
  [删除确认](holding-delete-light-390.png)。普通占位文字使用三级文字色，日期输入使用原生前景色，均核对实际 computed style。
- 录入覆盖识别中、候选、合并／替换预览、写入失败与完成；替换确认勾选前提交按钮禁用，失败后仍保留预览。
  示例：[替换预览](import-replace-light-390.png)、[写入失败](import-write-error-light-390.png)。
- 模拟覆盖加载、失败、订单空结果、运行／订单详情、详情失败、暂停失败与暂停结果；恢复后回到运行状态。
  [运行详情](simulation-run-light-390.png) 转到订单详情后只保留一个对话框，返回一次关闭详情且 URL 保持 `/simulation`。
- 两条 `theme-color` 的 media 匹配与当前画布色一致，`color-scheme` 在 HTML 与 CSS 中声明为 `light dark`。
  浏览器未捕获异常为 0。

## 对比度与自动检查

- [contrast.json](contrast.json)：浅／深各 78 项检查均无 FAIL；浅色全部 OK，无 THIN。
  深色有 6 项新增检查为 THIN：弱涨色对面板／悬停面为 4.98／4.68，十字线对画布、面板及各自休市带为 3.44–3.49，均达到门槛。
- [baseline-comparison.json](baseline-comparison.json)：2.4 原有 25 项对比度与 5 档表面层级的数值全部一致。
- [negative-cli-result.json](negative-cli-result.json)：在隔离副本中将浅色正文改为白色，CLI 退出码为 1，浅色 6 项失败，深色 0 项失败。
  `check-contrast.mjs --self-test` 另覆盖透明度、主题覆盖后的别名、缺失／循环 token 与错误色值。
- `pnpm --dir frontend typecheck`、`lint`、`build` 及全部 8 个 `frontend/scripts/*.mjs` 通过。
  包括曲线、抽屉返回栈、交易时段、刷新策略、收益口径、路由与 UI 校验。

未覆盖：iPhone／Android 真机触摸、安全区、软键盘、返回手势、Safari 状态栏与 `theme-color`、屏幕阅读器。
提议 7、9 与 PWA 后置；本轮隔离库的暂停／恢复不代替生产手机验收。
