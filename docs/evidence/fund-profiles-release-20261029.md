# 新基金档案批次后端发布预备（20261029-fund-profiles-1）

2026-10-29 制作本地候选，2026-10-30 以线上新版本重切，**尚未上传、未部署**。基线为线上实际运行的
`20261030-mobile-ui-3`，候选为 `20261029-fund-profiles-1`。保留生产 UI：候选不含前端改动，
`frontend/dist` 与线上逐字节一致；README、AGENTS 保持线上版本，仓库内较新的文档提交未混入。

## 重切记录（2026-10-30）

旧候选以 `20261028-write-origin-1` 为基线，此后生产先后发布 `20261029-mobile-ui-1`、
`20261030-mobile-ui-2`、`20261030-mobile-ui-3`，旧包含旧 `frontend/dist`，部署会回退线上 UI，因此作废重切。
旧产物整体移至 `reports/releases/20261029-fund-profiles-1/superseded-base-20261028-write-origin-1/`
（旧归档 SHA-256 `1a28ee25…10d6c0`，从未上传）。版本号沿用 `20261029-fund-profiles-1`，服务器上不存在同名目录。

- 新基线：`20261030-mobile-ui-3` 候选；2026-10-30 与线上 `current` 只读 tar（排除 `.venv`、egg-info、
  `__pycache__`）88 个文件逐一 SHA-256 与权限一致（`qa/recut-20261030/baseline-vs-production.json`）。
- 4 个差异文件在新基线中的内容与旧基线逐字节相同，补丁可原样套用；候选中这 4 个文件与旧候选及
  `main` 上的同名文件逐字节一致。

## 范围与依据

本批档案提交在 `main` 上为 `20e2b66`（旧记录中的 `8fee791` 是变基前的同内容提交，`src`、`tests` 无差异），
证据归档提交 `9bda7f3`。候选等于线上基线加本批对运行树的差异，只修改 4 个文件：

- `src/fund_analysis/fund_metadata.py`：新增 018147、008254、007195、008401 档案
- `src/fund_analysis/web/storage.py`：种子 docstring 去数字
- `tests/test_nav_status.py`、`tests/test_web_storage.py`：回归用例

无 schema 变化，依赖不变（`requirements-installed.txt` 与线上一致）。部署后首次启动由
`seed_initial_metadata_if_needed` 补入缺失档案行：生产现 21 行，预计变为 25 行（追加 4 只新代码）。

## 候选与范围

- 候选目录 `reports/releases/20261029-fund-profiles-1/candidate/`，88 个文件；84 个与线上基线
  内容与权限一致，4 个修改，无新增与删除。
- 完整发布包 `fund-analysis-20261029-fund-profiles-1.tar.gz`，977,942 字节；SHA-256
  `05712edeb909097366397c1b85e48870b1c733f0e3c351a109907a516e2ce72c`。打包排除 macOS 扩展属性，
  包内无 `__pycache__`、数据库或 `._*` 文件，解包后与候选 88 个文件逐一一致。
- 同目录 `runtime.patch`、`RELEASE.json`、`SHA256SUMS`、`rollback-code-only.sh`。

## 隔离验证（2026-10-30 重切后复跑）

独立 QA 环境 Python 3.12.13（uv 管理），依赖沿用生产 `requirements-installed.txt`，
仅额外安装 `httpx==0.28.1`；未模拟 Linux 与真实 systemd 切换。测试在候选的临时副本中运行，候选目录不被写入。

- 候选完整后端测试 **376 项通过**（`qa/recut-20261030/candidate-tests.log`），与旧候选结果相同。
- 流程烟测（`flow-results.json`）：真实 Uvicorn + 临时库，`/api/funds` 200；首启种子后
  `fund_valuation_profiles` 25 行，4 只新代码的字段与预期一致（018147 GLOBAL/unknown_rule、
  008254 GLOBAL/unknown_rule、007195 CN/cn_same_day_night、008401 US/unknown_rule）。
- 基线回归与 ruff：后端运行树与旧基线相同，沿用 2026-10-29 结果（候选测试跑基线源码时
  `test_nav_status.py` 2 错误、`test_web_storage.py` 1 失败；ruff 仅一处既有 F841），见旧产物目录。

## 发布安排与拟定步骤

用户已确认一并发布，时段放在 2026-10-30 P0-A 暂停／恢复验收（17:30–18:50）与 D7 账本复核完成之后，
并避开 21:30 结算槽，避免服务重启干扰暂停观察与当日结算。

1. 重新只读核对线上 `current` 仍为 `20261030-mobile-ui-3`、基线文件哈希与 timer 状态；有变化则重判。
2. 核验发布包 SHA-256 后上传 `fund-analysis-20261029-fund-profiles-1.tar.gz` 与包内
   `deploy/install-release.sh`；不上传工作区、数据库或本机 venv。
3. 在采集 tick 刚结束时以 `20261029-fund-profiles-1` 为版本号运行安装脚本：建立独立 venv、沿用生产依赖约束、
   自动生成 `backups/pre-20261029-fund-profiles-1.db`、短暂停服后原子切换 `current`，
   HTTP 检查通过后恢复原 timer。不构建前端。
4. 部署后核验：`/api/funds` 200；`fund_valuation_profiles` 21 变为 25 行且 4 只新档案字段一致；
   持仓、transactions、模拟库计数与订单状态与发布前一致；dist 与 `20261030-mobile-ui-3` 一致；timer 恢复并实际触发。

## 回滚（脚本仅做语法检查，未执行）

`rollback-code-only.sh` 可回退到 `20261030-mobile-ui-3`，保留主库与模拟库：校验基线 88 个文件哈希，
停两个 timer 与三个服务，留证备份主库，原子切回基线，健康检查后恢复 timer（模拟 timer 仅在 enabled 时启动）。
本批只有追加型档案行、无 schema 变化，回滚不恢复数据库；旧代码按代码查询读取新增档案行，无破坏性影响。

## 未覆盖

未上传、未部署；未改动 Cloudflare、DNS、生产库；未构建前端。本批无路由与前端行为变化，
浏览器验收不适用。
