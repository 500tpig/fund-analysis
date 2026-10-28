# 写请求来源校验最小候选发布包

2026-10-28 制作本地候选，**尚未发布**。基线为线上实际运行的 `20261028-au9999-close-1`，
候选为 `20261028-write-origin-1`。本轮只做只读基线与本机隔离验证，
未上传、未部署、未改动 Cloudflare／DNS、未触碰生产库。

## 范围与依据

安全修复来源为本地提交 `9bed5e7`（可信 Host 白名单与收紧配置解析），未推送、未发布。
本轮以线上基线为起点重新制作候选，不把工作区重新打包，未混入尚未发布的 UI 改版
（`frontend/dist` 与线上逐文件一致）。启动参数与代理依赖保留：`cli.py` 设置
`forwarded_allow_ips="127.0.0.1"`。

已核对：全部受影响文件的线上基线与 `9bed5e7` 的父提交 `9653a1a` 逐字节一致，
因此候选等于"线上基线 + 该提交的差异"，未夹带其他本地改动。

### 候选与范围

- [隔离候选目录](../../reports/releases/20261028-write-origin-1/candidate/)，85 个文件；
  75 个与线上基线内容与权限一致，9 个修改、1 个新增。
- [完整可安装发布包](../../reports/releases/20261028-write-origin-1/fund-analysis-20261028-write-origin-1.tar.gz)，
  970,958 字节；SHA-256 为
  `a393e9160f62080d7961026df7e88a231b92a380989d5e45fa02a3fa8e40a38e`。
- [最终 diff](../../reports/releases/20261028-write-origin-1/runtime.patch)、
  [逐文件清单](../../reports/releases/20261028-write-origin-1/RELEASE.json)、
  [线上基线清单](../../reports/releases/20261028-write-origin-1/production-inventory.json)、
  [包校验文件](../../reports/releases/20261028-write-origin-1/SHA256SUMS)。

修改文件：`src/fund_analysis/web/app.py`、`src/fund_analysis/web/defaults.py`、
`src/fund_analysis/cli.py`、`README.md`，以及 `tests/test_cli.py`、
`tests/test_estimate_accuracy.py`、`tests/test_holdings_entry.py`、
`tests/test_simulation_api.py`、`tests/test_web_api.py`；新增 `tests/test_write_origin.py`。
`README.md` 仅补充写来源校验章节与一行配置表，无运行时影响。无 schema 变化，依赖不变
（`requirements-installed.txt` 与基线一致）。发布包不含数据库、`.env`、venv、Node 依赖。

## 隔离验证

使用本机独立 Python 3.12.13 环境（`qa-venv`），全部运行时依赖沿用生产版本，仅额外安装
既有测试工具 `httpx==0.28.1`。生产为 Linux / Python 3.12.3；本地未模拟 Linux 或真实
systemd 切换。测试全部使用临时库。

- 候选完整后端测试 **375 项通过**，日志见
  [candidate-tests.log](../../reports/releases/20261028-write-origin-1/candidate-tests.log)。
  线上基线同环境为 366 项，本地完整工作区为 382 项；差额来自尚未发布提交新增的用例，
  候选只含本次安全修复。
- 用候选用例跑线上基线源码，**12 项失败、2 项错误**（跨站写返回 201／422 而非 403），
  见 [baseline-regression.log](../../reports/releases/20261028-write-origin-1/baseline-regression.log)，
  证明差异确实来自本次修复。
- 真实 Uvicorn（`python -m fund_analysis web`，loopback）下经原始 HTTP 请求核对，
  见[流程结果](../../reports/releases/20261028-write-origin-1/flow-results.json)与
  [流程日志](../../reports/releases/20261028-write-origin-1/flow-verification.log)：
  - 正常写入：`Host: 127.0.0.1` 带 JSON → 201；无来源头（CLI 形态）→ 201。
  - 攻击拒绝：跨站简单请求（`Origin: https://evil.example`）→ 403；
    rebinding（`Host` 与 `Origin` 同为 `evil.example`）→ 403；未知 `Host` → 403；
    伪造 `X-Forwarded-Host` 仍按原始 `Host` 判定 → 403；库内仅写入 2 条正常持仓。
  - `collect` CLI 实跑 `POST /api/funds/refresh`，退出码 0，未被来源校验拦截。
  - 模拟调度：`test_simulation_strategy` 通过。
  - 回滚对比：基线源码同样请求返回 201（跨站可写），证明回滚会撤回防护。
- [流程脚本](../../reports/releases/20261028-write-origin-1/qa/verify_release_flow.py)单独保存，不进入发布包。
- 包完整性：`baseline/` 加 `runtime.patch` 逐字节等于 `candidate/`；归档成员集合、
  内容与权限与候选一致；清单与实测一致。

## 发布前拟定步骤（待用户授权后执行）

1. 重新只读核对线上 `current` 仍为 `20261028-au9999-close-1`、基线逐文件哈希与两个 timer 状态；
   若有变化则停用本候选重新判断。
2. 核验发布包 SHA-256 后上传本包与包内 `deploy/install-release.sh`；不上传工作区代码或本机数据库。
3. 以 `20261028-write-origin-1` 为版本号运行安装脚本。脚本会建立独立 venv、沿用生产依赖约束、
   自动生成 `/var/lib/fund-analysis/backups/pre-20261028-write-origin-1.db` 一致性备份，
   随后短暂停止 Web 与采集／模拟任务并切换 `current`，HTTP 检查通过后恢复原 timer。
4. 部署后核验 `/api/funds`、来源运行记录、持仓与模拟库指纹、timer 状态；不构建前端。

## 回滚（脚本仅做语法检查，未执行）

[rollback-code-only.sh](../../reports/releases/20261028-write-origin-1/rollback-code-only.sh)
可回退到 `20261028-au9999-close-1`，保留主库与模拟库：

1. 确认当前为 `20261028-write-origin-1`，核对基线版本文件哈希与既有 venv。
2. 停止两个 timer、两个任务 service 与 Web；先备份当前主库用于留证，不恢复旧库。
3. 原子切换 `current` 到基线并启动 Web；两版本 unit 相同，不重装 unit，不迁移数据库。
4. `/api/funds` 健康检查通过后恢复发布前的 timer。

**回滚会撤回安全防护**：基线允许跨站简单请求写入持仓、录入确认、刷新与识图。
P0-A 远程入口（Cloudflare Tunnel + Access）开放后，不得回滚到无防护版本；
如确需回滚代码，必须同时关闭远程路由后再执行。
