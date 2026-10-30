# 手机样板与抽屉返回栈修复发布（20261029-mobile-ui-1）

2026-10-29 发布，**已上线**。这是纯前端发布：只替换 `frontend/dist`，不含后端、数据库与依赖变更。
基线为线上实际运行的 `20261028-write-origin-1`，来源提交 `7f45b4d`、`aab5223`。

## 范围与依据

- 发布内容：手机端工艺层（触摸基础、`100dvh` 与四向安全区、阻断下拉整页刷新、列表字号、
  底部抽屉形态与 token 化过渡、数值更新微光）与抽屉返回栈修复。
- 同时带上第一批 UI 改版：它此前已提交但从未发布，生产 `frontend/dist` 一直是 10-22 的旧构建。
- `docs`、`README.md`、`AGENTS.md`、`deploy`、`src`、`tests` 保持线上版本，仓库内较新的文档提交未混入；
  `runtime-constraints.txt` 为线上实际存在，随基线保留。
- [候选与清单](reports/releases/20261029-mobile-ui-1/RELEASE.json)：87 个文件，1 个修改
  （`frontend/dist/index.html`）、17 个新增、16 个删除、69 个不变；无 schema 变化。

## 发布前只读核对（2026-10-29 20:01:53 +0800）

命令见 [采集脚本](../../reports/releases/20261029-mobile-ui-1/qa/collect-fingerprint.sh)，
原始输出见 [指纹记录](../../reports/releases/20261029-mobile-ui-1/pre-deploy-fingerprint.json)。

| 项目 | 值 |
| --- | --- |
| `current` / `previous` | `20261028-write-origin-1` / `20261028-au9999-close-1` |
| 四个 unit | `fund-analysis.service`、两个 timer、`cloudflared.service` 均 active/enabled |
| `/api/funds` | 200 |
| 主库行数 | 持仓 22、transactions 11、data_source_runs 5464、估值档案 21 |
| 模拟库行数 | accounts 1、orders 2、runs 2 |
| 磁盘 | `/dev/sda1` 96G 总 / 24G 已用 / 73G 可用 / 25% |

发布前只读采集漏掉 dist 清点，`frontend_dist_sha256` 由发布前从线上 `current`
只读 tar 取得的 baseline 计算；发布后复核 `20261028-write-origin-1` 的 dist 逐文件哈希与上表一致。

## 发布

`deploy/install-release.sh ~/fund-analysis-20261029-mobile-ui-1.tar.gz 20261029-mobile-ui-1`
（归档 974,486 字节，SHA-256 `227c5f1cd28b30f692b011a9511eeb706f9722ef0ef97592297132ff9e943d62`，
上传后服务器侧复算一致）。安装过程：新建 venv 并按线上 `requirements-installed.txt` 安装依赖、
`pip check` 无冲突；数据库一致性备份
`/var/lib/fund-analysis/backups/pre-20261029-mobile-ui-1.db`；停服务与 timer、切 `current`、
启动并 30 次健康检查后恢复 timer，日志末行 `Active release: /opt/fund-analysis/releases/20261029-mobile-ui-1`。

## 发布后核对

| 项目 | 结果 |
| --- | --- |
| `current` / `previous` | `20261029-mobile-ui-1` / `20261028-write-origin-1` |
| 四个 unit | 全部 active/enabled |
| `/api/funds`、`/valuation` | 200 / 200 |
| 部署产物与候选包 | dist 20 个文件逐文件 SHA-256 一致 |
| 生产库 | 持仓 22、transactions 11、估值档案 21、模拟库 1/2/2，与发布前相同 |
| 经 SSH 隧道浏览器检查（390×844） | 无横向溢出、底部导航可见、行名字号 15 px、详情为底部抽屉（顶部圆角 18 px、顶部留 12 px）、返回关闭抽屉且 URL 不变 |

桌面 1440×900 经同一隧道检查：无横向溢出、底部导航隐藏、左侧 rail 显示，无退化。

## 回滚

[rollback-code-only.sh](../../reports/releases/20261029-mobile-ui-1/rollback-code-only.sh)：
校验当前确为本次候选、目标 `20261028-write-origin-1` 可执行、且其 dist 与发布前记录的
19 个哈希逐一相符，再停服务与 timer、切回软链、启动并健康检查后恢复 timer。
本批不涉及数据库，回滚不需要恢复任何数据。

## 未覆盖与后续

- 真机（iPhone／Android）安全区、惯性滚动与软键盘遮挡仍待用户在生产站点验收。
- 收尾窗口的 `20261029-fund-profiles-1` 候选包含发布前的旧 `frontend/dist`；本次发布后它的
  基线已变化，若按原包发布会把 UI 退回旧构建。该批次发布前需要重切候选（改以
  `20261029-mobile-ui-1` 为基线，或仅保留 4 个后端文件差异）。
- 数值微光的实时触发与本批其他动效未在真机观察，浏览器检查只确认规则与类绑定生效。
