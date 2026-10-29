# 新基金档案批次后端发布预备（20261029-fund-profiles-1）

2026-10-29 制作本地候选，**尚未上传、未部署**。基线为线上实际运行的 `20261028-write-origin-1`，
候选为 `20261029-fund-profiles-1`。保留生产 UI：候选不含前端改动，`frontend/dist` 与线上逐字节一致；
README、AGENTS 保持线上版本，仓库内较新的文档提交未混入。

## 范围与依据

提交 `8fee791`（4 只新基金档案）与 `9bda7f3`（证据归档）已推送 `origin/main`。候选等于线上基线
加本批对运行树的差异，只修改 4 个文件：

- `src/fund_analysis/fund_metadata.py`：新增 018147、008254、007195、008401 档案
- `src/fund_analysis/web/storage.py`：种子 docstring 去数字
- `tests/test_nav_status.py`、`tests/test_web_storage.py`：回归用例

无 schema 变化，依赖不变（`requirements-installed.txt` 与基线一致）。部署后首次启动由
`seed_initial_metadata_if_needed` 补入缺失档案行：生产现 21 行，预计变为 25 行（追加 4 只新代码）。

## 候选与范围

- [候选目录](../../reports/releases/20261029-fund-profiles-1/candidate/)，85 个文件；81 个与线上基线
  内容与权限一致，4 个修改，无新增与删除。
- [完整发布包](../../reports/releases/20261029-fund-profiles-1/fund-analysis-20261029-fund-profiles-1.tar.gz)，
  969,623 字节；SHA-256 `1a28ee254db865b233a10cf46a9786aff3b2519a6a8f8268d4cb9c430910d6c0`。
- [runtime.patch](../../reports/releases/20261029-fund-profiles-1/runtime.patch)、
  [RELEASE.json](../../reports/releases/20261029-fund-profiles-1/RELEASE.json)、
  [SHA256SUMS](../../reports/releases/20261029-fund-profiles-1/SHA256SUMS)、
  [回滚脚本](../../reports/releases/20261029-fund-profiles-1/rollback-code-only.sh)。
- 发布前只读核对（2026-10-29 15:20 至 15:23 北京时间）：线上 `current` 仍为 `20261028-write-origin-1`、
  `previous` 为 `20261028-au9999-close-1`；三服务 active、双 timer active/enabled、`/api/funds` 200；
  部署树与本地基线 85 个文件逐一 SHA-256 一致，requirements-installed.txt 单独复测一致，
  egg-info 与 .venv 为部署时生成、不在清点范围。发布前只读指纹见
  [pre-deploy-fingerprint.json](../../reports/releases/20261029-fund-profiles-1/pre-deploy-fingerprint.json)：
  持仓 22 行、transactions 11 行、模拟库计数与两笔订单（awaiting_nav、settled）状态。

## 隔离验证

独立 QA 环境 Python 3.12.13（uv 管理），依赖沿用生产 `requirements-installed.txt`，
仅额外安装 `httpx==0.28.1`；未模拟 Linux 与真实 systemd 切换。

- 候选完整后端测试 **376 项通过**
  （[candidate-tests.log](../../reports/releases/20261029-fund-profiles-1/candidate-tests.log)）。
  与工作区 383 项的差额是线上包未包含的未发布用例，候选只含本批差异。
- 基线回归：用候选测试跑线上基线源码，`test_nav_status.py` 20 项中 2 错误（008401 KeyError）、
  `test_web_storage.py` 33 项中 1 失败（018147 缺失断言），证明差异来自本批
  （[baseline-regression.log](../../reports/releases/20261029-fund-profiles-1/baseline-regression.log)）。
- 流程烟测（[flow-results.json](../../reports/releases/20261029-fund-profiles-1/flow-results.json)）：
  真实 Uvicorn + 临时库，`/api/funds` 200；首启种子后 `fund_valuation_profiles` 25 行，
  4 只新代码的字段与预期一致（018147 GLOBAL/unknown_rule、008254 GLOBAL/unknown_rule、
  007195 CN/cn_same_day_night、008401 US/unknown_rule）。
- ruff 与工作区结果相同，仅 `tests/test_web_storage.py` 一处既有 F841（qa/ruff.log）。

## 发布前拟定步骤（待用户授权后执行）

1. 重新只读核对线上 `current` 仍为 `20261028-write-origin-1`、基线文件哈希与 timer 状态；有变化则重判。
2. 核验发布包 SHA-256 后上传 `fund-analysis-20261029-fund-profiles-1.tar.gz` 与包内
   `deploy/install-release.sh`；不上传工作区、数据库或本机 venv。
3. 以 `20261029-fund-profiles-1` 为版本号运行安装脚本：建立独立 venv、沿用生产依赖约束、
   自动生成 `backups/pre-20261029-fund-profiles-1.db`、短暂停服后原子切换 `current`，
   HTTP 检查通过后恢复原 timer。不构建前端。
4. 部署后核验：`/api/funds` 200；`fund_valuation_profiles` 21 变为 25 行且 4 只新档案字段一致；
   持仓、transactions、模拟库计数与两笔订单状态与发布前指纹一致；timer 恢复并实际触发。

## 回滚（脚本仅做语法检查，未执行）

[rollback-code-only.sh](../../reports/releases/20261029-fund-profiles-1/rollback-code-only.sh)
可回退到 `20261028-write-origin-1`，保留主库与模拟库：校验基线 85 个文件哈希，停两个 timer 与
三个服务，留证备份主库，原子切回基线，健康检查后恢复 timer。本批只有追加型档案行、无 schema
变化，回滚不恢复数据库；旧代码按代码查询读取新增档案行，无破坏性影响。回滚不会撤回写来源校验，
基线版本本身包含该修复。

## 未覆盖

未上传、未部署；未改动 Cloudflare、DNS、生产库；未构建前端。本批无路由与前端行为变化，
浏览器验收不适用；逐行聚合指纹由 2026-10-29 21:45 结算前检查点另行留证。
