# 手机体验第二批 2.1–2.3 发布（20261030-mobile-ui-2）

2026-10-30 发布，**已上线**。纯前端发布：只替换 `frontend/dist`，不含后端、数据库与依赖变更。
基线为线上实际运行的 `20261029-mobile-ui-1`，来源提交 `aaab3c1`（2.1／2.2）与 `0d6965f`（2.3）。

## 范围与依据

- 发布内容：曲线手机读点（轻触／横向读点、松手保留、纵向滚动、首次提示、紧凑尺寸档）、
  详情面板手机细化（读数条吸顶、分区间距与字号统一）与持仓／录入／模拟三页铺样板。
- 本批不含数据模型、接口与后端改动；`docs`、`AGENTS.md`、`src`、`tests`、`deploy` 保持线上版本，
  仓库内较新的文档提交未混入。
- [候选与清单](../../reports/releases/20261030-mobile-ui-2/RELEASE.json)：88 个文件，1 个修改
  （`frontend/dist/index.html`）、18 个新增、17 个删除、69 个不变；无 schema 变化。
- 本地验收记录：[曲线读点与尺寸档](../evidence/mobile-curve-20261029/results.json)、
  [详情面板细化](../evidence/mobile-detail-20261029/results.json)、
  [三页铺样板](../evidence/mobile-pages-20261029/README.md)。

## 发布前只读核对（2026-10-30 00:33:12 +0800 / 服务器 2026-10-29 16:33:12 UTC）

原始输出见 [指纹记录](../../reports/releases/20261030-mobile-ui-2/pre-deploy-fingerprint.json)。

| 项目 | 值 |
| --- | --- |
| `current` / `previous` | `20261029-mobile-ui-1` / `20261028-write-origin-1` |
| 四个 unit | `fund-analysis.service`、两个 timer、`cloudflared.service` 均 active/enabled |
| `/api/funds` | 200 |
| 主库行数 | 持仓 22（最大 rowid 62）、transactions 11、data_source_runs 5548、估值档案 21 |
| 模拟库行数 | accounts 1、orders 2、runs 2 |
| 磁盘 | `/dev/sda1` 96G 总 / 24G 已用 / 73G 可用 / 25% |
| 线上 dist | 20 个文件逐文件 SHA-256 已记录 |

## 发布

`deploy/install-release.sh ~/fund-analysis-20261030-mobile-ui-2.tar.gz 20261030-mobile-ui-2`
（归档 978,072 字节，SHA-256 `4fd5eaed0de95f36760ba8ced3cde7c25ac24edae6fdc656cb703134f0a7c572`，
上传后服务器侧复算一致）。安装过程：数据库一致性备份
`/var/lib/fund-analysis/backups/pre-20261030-mobile-ui-2.db`；停服务与 timer、切 `current`、
启动并 30 次健康检查后恢复 timer，日志末行 `Active release: /opt/fund-analysis/releases/20261030-mobile-ui-2`。

## 发布后核对

| 项目 | 结果 |
| --- | --- |
| `current` / `previous` | `20261030-mobile-ui-2` / `20261029-mobile-ui-1` |
| 四个 unit | 全部 active/enabled |
| `/api/funds` | 200 |
| 部署产物与候选包 | dist 21 个文件逐文件 SHA-256 一致 |
| 上一个构建 | `20261029-mobile-ui-1` 的 dist 20 个文件与发布前记录相符，可回滚 |
| 生产库 | 持仓 22、transactions 11、data_source_runs 5548、估值档案 21、模拟库 1/2/2，与发布前相同 |
| 经 SSH 隧道浏览器检查（390×844） | 四个页面无横向溢出、底部导航可见；列表名称与金额 15 px、副行 13 px；详情为底部抽屉（顶部留 12 px、顶部圆角 18 px）；返回关闭抽屉且 URL 不变 |

手机读数条吸顶在线上复核通过：合成一次读点后 `.fund-detail-panel__readout` 高 77 px 且为
`position: sticky`，把面板正文滚动 760 px 后它的顶部仍在 104 px，与固定头部下沿重合、读数保持可见
（[截图](../../reports/releases/20261030-mobile-ui-2/qa/tunnel-390-readout-sticky-verified.png)）。

桌面 1440×900 经同一隧道检查：无横向溢出、底部导航隐藏、左侧 rail 216 px；点击基金行后右侧内联
详情列填充（桌面用内联面板而非抽屉，见 `frontend/src/views/FundsView.vue:1204`），与既有行为一致
（[截图](../../reports/releases/20261030-mobile-ui-2/qa/tunnel-1440-drawer-diagnose.png)）。

## 回滚

[rollback-code-only.sh](../../reports/releases/20261030-mobile-ui-2/rollback-code-only.sh)：
校验当前确为本次候选、目标 `20261029-mobile-ui-1` 可执行、且其 dist 与发布前记录的
20 个哈希逐一相符，再停服务与 timer、切回软链、启动并健康检查后恢复 timer。
本批不涉及数据库，回滚不需要恢复任何数据。

## 未覆盖与后续

- 真机（iPhone／Android）安全区、返回手势、软键盘遮挡与 Safari 仍待用户在生产站点验收。
- 收尾窗口的 `20261029-fund-profiles-1` 候选包含发布前的旧 `frontend/dist`；本次发布后它的
  基线再次变化，发布前需以 `20261030-mobile-ui-2` 为基线重切（或只保留 4 个后端文件差异）。
- 数值微光的实时触发与读点动效未在真机观察，本次只确认规则、类绑定与吸顶行为生效。
