# 手机体验第二批 2.4–2.5 发布（20261030-mobile-ui-3）

2026-10-30 发布，**已上线**。纯前端发布：只替换 `frontend/dist`，不含后端、数据库与依赖变更。
基线为线上实际运行的 `20261030-mobile-ui-2`，来源提交 `de711d7`（2.4 暗色深化）与 `7ee8cd4`（2.5 浅色主题）。

## 范围与依据

- 发布内容：2.4 暗色主题深化（表面色阶、减描边、圆角／时长／图表笔触 token 统一）与 2.5 跟随系统的
  浅色主题（`prefers-color-scheme`，两条 `theme-color`，无手动开关）。规范与本地验收见
  [手机体验第二批方案](../mobile-ux-batch2-plan.md) 2.4、2.5 节与 [浅色主题验收记录](light-theme-20261030/README.md)。
- `0d6965f..7ee8cd4` 的非文档改动只有 `frontend/` 与 `AGENTS.md`、`README.md`；候选中 `docs`、`AGENTS.md`、
  `README.md`、`src`、`tests`、`deploy` 保持线上版本，仓库内较新的文档提交未混入。
- [候选与清单](../../reports/releases/20261030-mobile-ui-3/RELEASE.json)：88 个文件，1 个修改
  （`frontend/dist/index.html`）、19 个新增、19 个删除、68 个不变；`dist` 以外无差异，无 schema 变化。
- 构建：`7ee8cd4` 干净工作区 `pnpm build`（含 `vue-tsc`）通过；`node scripts/check-contrast.mjs` 全部达标。
- 基线核对：线上 `current` 只读 tar（排除 `.venv`、egg-info、`__pycache__`）与 `20261030-mobile-ui-2`
  候选 88 个文件逐一 SHA-256 与权限一致。

## 发布前只读核对（2026-10-30 16:08:57 +0800 / 08:08:57 UTC）

原始输出见 `reports/releases/20261030-mobile-ui-3/qa/pre-deploy-fingerprint.txt`。

| 项目 | 值 |
| --- | --- |
| `current` / `previous` | `20261030-mobile-ui-2` / `20261029-mobile-ui-1` |
| 四个 unit | `fund-analysis.service`、两个 timer、`cloudflared.service` 均 active/enabled |
| `/api/funds` | 200 |
| 主库行数 | 持仓 22（最大 rowid 62）、transactions 11、data_source_runs 5758、估值档案 21 |
| 模拟库行数 | accounts 1、orders 2、runs 2 |
| 磁盘 | `/dev/sda1` 96G 总 / 24G 已用 / 73G 可用 / 25% |
| 线上 dist | 21 个文件逐文件 SHA-256 已记录，与 `20261030-mobile-ui-2` 候选一致 |

## 发布

`bash ~/install-release.sh ~/fund-analysis-20261030-mobile-ui-3.tar.gz 20261030-mobile-ui-3`
（归档 977,291 字节，SHA-256 `3b32d63f723545487a0eaaec144277d23d72df9ed5b32d9432a67c96e757210e`，
上传后服务器侧 `sha256sum -c` 一致；`install-release.sh` 与候选内 `deploy/install-release.sh` 哈希一致）。
打包使用 `COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata`，本次安装日志不再出现 xattr 警告。
为避开采集 tick，在 08:10 UTC 的 tick 完成（两个 service 均 inactive）后启动安装。

安装过程：数据库一致性备份 `/var/lib/fund-analysis/backups/pre-20261030-mobile-ui-3.db`（27,488,256 字节，
脚本输出 `SQLite backup verified`）；停服务与 timer、切 `current`、启动并健康检查后恢复 timer，
日志末行 `Active release: /opt/fund-analysis/releases/20261030-mobile-ui-3`。

## 发布后核对（2026-10-30 16:10:45 +0800 起）

| 项目 | 结果 |
| --- | --- |
| `current` / `previous` | `20261030-mobile-ui-3` / `20261030-mobile-ui-2` |
| 四个 unit | 全部 active/enabled；两个 timer 下一次 08:15 UTC，随后 tick 结果 `success`，日志无错误 |
| `/api/funds` | 200 |
| 部署产物与候选包 | dist 21 个文件逐文件 SHA-256 一致 |
| 上一个构建 | `20261030-mobile-ui-2` 的 dist 21 个文件 `sha256sum --check` 全部 OK，venv 可执行，可回滚 |
| 生产库（发布后即刻） | 持仓 22、transactions 11、data_source_runs 5758、估值档案 21、模拟库 1/2/2，与发布前相同 |
| 生产库（浏览器检查后 16:15） | 持仓、transactions、估值档案与模拟库不变；data_source_runs 5758 → 5761，为 16:15 采集 tick 与估值页既有刷新所写 |

经 SSH 隧道（`127.0.0.1:18765`）浏览器检查，四页 × 390×844／1440×900 × 深／浅两主题共 16 个状态
（[逐项结果](../../reports/releases/20261030-mobile-ui-3/qa/probe-results.jsonl)）：

- 全部无横向溢出、无页面级错误文案；手机底部导航可见、左侧 rail 隐藏；桌面底部导航隐藏、rail 216 px。
- 深色：`--bg-canvas` `#0a0c0b`、body 背景 `rgb(10, 12, 11)`、生效的 `theme-color` 为 `#0A0C0B`。
- 浅色：`--bg-canvas` `#f3f4f6`、body 背景 `rgb(243, 244, 246)`、生效的 `theme-color` 为 `#F3F4F6`。
- 模拟页「暂停买入」宽 90 px，在 10%／50%／90% 横向位置点中的都是它自身，与「刷新」不重叠
  （10-29 真机发现的旧构建重叠缺陷在线上已不复现）。
- 手机详情抽屉：两主题下均为底部抽屉、顶部留 12 px、顶部圆角 18 px，抽屉底色深色 `rgb(16, 19, 16)`、
  浅色 `rgb(255, 255, 255)`；返回关闭抽屉且 URL 保持 `/valuation`。
- 截图：`reports/releases/20261030-mobile-ui-3/qa/tunnel-{390,1440}-{valuation,holdings,import,simulation}-{dark,light}.png`
  与 `tunnel-390-drawer-{dark,light}.png`。检查过程中未点击暂停、刷新或识图。

## 回滚

[rollback-code-only.sh](../../reports/releases/20261030-mobile-ui-3/rollback-code-only.sh)（`bash -n` 通过，未执行）：
校验当前确为本次候选、目标 `20261030-mobile-ui-2` 可执行、且其 dist 与发布前记录的 21 个哈希逐一相符，
再停服务与 timer、切回软链、启动并健康检查后恢复 timer。本批不涉及数据库，回滚不需要恢复任何数据。

## 未覆盖与后续

- 真机（iPhone／Android）的浅色主题、Safari 状态栏与 `theme-color` 行为、安全区、返回手势、软键盘仍待用户在生产站点验收。
- 系统外观在页面打开期间切换的实时跟随只在本地验收中覆盖，线上只按两种外观分别打开检查。
- `20261029-fund-profiles-1` 候选需以 `20261030-mobile-ui-3` 为基线重切后再发布。
