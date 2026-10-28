# Fund Valuation

本地优先的基金估值工具，包含四个页面：

- `估值`：查看第三方盘中估值、官方净值、估算收益和数据新鲜度。
- `持仓`：新增、修改和删除持仓。
- `录入`：从持仓截图识别候选，人工核对后批量写入。
- `模拟`：查看独立模拟账户的资金、持仓、订单、固定策略运行记录与费用后指标，
  并可暂停或恢复新增买入（不提供手动下单）。

当前版本不包含 AI 分析、新闻、投资推荐、研究或报告功能，也不会连接券商或执行交易。
固定策略调度已部署服务器（2026-10-18）：模拟 timer 每 5 分钟运行 `simulation tick`，生产白名单
当前只有 016665（天弘全球高端制造混合(QDII)C），每周三 14:00 按 500.00 元模拟买入，首个周期
2026-10-21 已于 2026-10-28 复算通过（7.5 通过）；阶段 7 整体待生产手机真机查看与暂停及 2027 年日历。UI 视觉与布局改版（第一批）已于 2026-10-18 在本地完成并通过
四个视口的浏览器验收，尚未发布到服务器。后续产品阶段见 [`docs/project-roadmap.md`](docs/project-roadmap.md)。

## 环境

- Python 3.11+
- Node.js 20+
- pnpm 10+

## 安装

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .

pnpm --dir frontend install
pnpm --dir frontend build
```

## 截图录入

打开 <http://127.0.0.1:8765/import>，可从相册选择最多 5 张 JPG、PNG 或 WebP，
也可在手机端拍照或在桌面端粘贴图片。识别只生成页面内候选，修正代码、名称、金额和
可选份额后，需再次预览并确认才会写入 SQLite。

默认采用「合并更新」，未出现在截图中的持仓保持不变。切换为「替换全部」时，页面会列出
待删除持仓，并要求单独确认。批量写入使用一个 SQLite 事务，任一写入失败会整批回滚。

识图服务使用 OpenAI 兼容的 `/chat/completions` 接口，由启动进程提供以下环境变量：

```bash
export FUND_ANALYSIS_OCR_STRUCTURED_BASE_URL=https://example.invalid/v1
export FUND_ANALYSIS_OCR_STRUCTURED_API_KEY=replace-with-api-key
export FUND_ANALYSIS_OCR_STRUCTURED_MODEL=replace-with-vision-model
```

可选配置包括 `FUND_ANALYSIS_OCR_STRUCTURED_TIMEOUT`（默认 `180` 秒）和
`FUND_ANALYSIS_OCR_STRUCTURED_MAX_TOKENS`（默认 `4096`）。任一必需变量缺失时，
服务不会发送图片，并返回「未配置持仓识图服务」。截图只用于当次请求；服务端压缩为
最长边 1600 px 的 JPEG 后发送，不写入 SQLite，识别结束后关闭上传文件且不保留原图。

## 模拟账本

模拟账户使用独立 SQLite 文件，默认 `data/simulation.db`，可用 `--sim-db` 或
`FUND_ANALYSIS_SIMULATION_DB_PATH` 指定；路径指向真实库、其 WAL/SHM 文件、硬链接或符号链接时拒绝启动。
结算净值来自东财 lsjz 历史接口（单位净值 DWJZ，按基金代码与净值日精确查询，与是否持仓无关），
取不到时只读回退真实库官方净值；不读取持仓金额，也不修改真实持仓。

- 每个模拟库在创建时固定为 `fixture`（隔离样例）或 `production` 环境。生产库拒绝 fixture 规则、
  手工净值、指定时间和命令行下单；当前已导入一条已核验生产规则（016665），生产库只为它产生订单。
- 金额、份额、净值以十进制字符串存储和返回；余额由 `ledger_events` 重放得到，账本、规则和快照只追加。
- 规则按基金代码、渠道与份额类别建立不可变版本，有效区间为左闭右开。同一代码的版本必须共用渠道与份额类别，
  且不能同日生效或穿入已声明的结束日；修订须新增更晚的 `effective_from`，不修改已导入版本。
- 订单只由服务层受理（阶段 6 为 fixture 验收命令，阶段 7 为固定策略），没有 HTTP 下单接口。
  `/api/simulation` 提供账户、持仓、订单、运行记录、指标查询，以及撤单、暂停和恢复；写请求需要 `Idempotency-Key`。
- 固定策略首版只有一种：每周三北京时间 14:00、按基金代码升序、每只固定金额买入，执行窗口至 14:10（不含结束）。
  策略版本不可覆盖，修订须新增更晚的 `effective_from`。运行记录（`runs`/`run_items`）为账户 + 周期唯一，重启不重复下单。

```bash
# 隔离验收：在新的空目录里跑 C01–C16 与日期算例，逐项打印预期与实际
fund-analysis simulation acceptance --output-dir /tmp/sim-acceptance

# 在页面查看某个样例库（真实库可指向一个临时空库）
fund-analysis web --db-path /tmp/empty-main.db --simulation-db-path /tmp/sim-acceptance/C16.db

# 常用管理命令（均需 --sim-db；默认 --main-db 为 data/fund_analysis.db，仅用于防误配和只读净值）
fund-analysis simulation --sim-db data/simulation.db init            # 新库默认 production，幂等初始化 200,000 元
fund-analysis simulation --sim-db data/simulation.db show --account <id>
fund-analysis simulation --sim-db data/simulation.db settle --account <id>
fund-analysis simulation --sim-db data/simulation.db verify          # 账本重放核对
fund-analysis simulation --sim-db data/simulation.db backup --target data/backups/sim-<时间>.db
fund-analysis simulation --sim-db data/simulation.db restore --backup <备份> --target <新路径>
fund-analysis simulation --sim-db data/simulation.db archive --account <id> --confirm <id> --operator <名字> --reason <原因>
```

固定策略与调度命令（阶段 7 已实现并部署，模拟 timer 已启用）：

```bash
# 导入固定策略版本（fixture 库用 --fixture；生产库只接受已核验规则对应的 --file）
fund-analysis simulation --sim-db data/simulation.db strategy-import --account <id> --effective-from 2026-10-21 --fixture

# 手动处理到期周期：窗口内受理，窗口已过补记 missed；显式 --at 仅 fixture 库可用
fund-analysis simulation --sim-db data/simulation.db run-cycle --account <id> --at 2026-10-21T14:05:00+08:00

# 每日调度入口：结算检查与 23:30 日终快照；生产库不传 --at，取服务端时钟
fund-analysis simulation --sim-db data/simulation.db tick

fund-analysis simulation --sim-db data/simulation.db runs --account <id>       # 运行记录
fund-analysis simulation --sim-db data/simulation.db run --run <run_id>        # 单次运行详情
fund-analysis simulation --sim-db data/simulation.db metrics --account <id>    # 费用后指标
```

`deploy/systemd/fund-analysis-simulation.{service,timer}` 已安装并启用（2026-10-18），
渠道规则核验与 P0-B 采集与备份恢复证据已就绪；未导入规则的基金仍会记录 `rule_incomplete` 跳过。

备份通过 SQLite backup 生成并做完整性检查与账本重放，目标已存在时拒绝。恢复只写入尚不存在的新路径，
恢复后所有未归档账户保持暂停；替换正在使用的模拟库须先停服务并人工移走原文件。
「重置」即归档旧实验并新建账户：账户须先暂停，且没有待处理订单、冻结资金或应收款，旧记录全部保留。
规则、费用与到账日期的假设见 [`docs/simulation-plan.md`](docs/simulation-plan.md) 第 2.5 节；
fixture 结果不代表支付宝实际交易规则。

## CSV 迁移

```bash
fund-analysis import \
  --db-path data/fund_analysis.db \
  --portfolio examples/portfolio.csv
```

CSV 至少需要 `code`、`name`，以及完整的 `amount` 或 `weight_pct`。支持中文表头。常用可选字段包括 `cost`、`holding_profit_rate`、`daily_profit`、`cumulative_profit`、`year_profit`、`theme` 和 `amount_nav_date`。`amount_nav_date` 为空时保持未知，不按导入当天回填。

CSV 不再出现在 Web 主界面，只保留 CLI 迁移入口。导入会在完整解析成功后一次性替换持仓；
解析失败时不会修改现有持仓。

## 启动

```bash
fund-analysis web --db-path data/fund_analysis.db
```

打开 <http://127.0.0.1:8765/valuation>。

默认数据库是 `data/fund_analysis.db`。应用只会增量创建估值 MVP 所需的 SQLite 表，不会删除旧数据库里的其他表或历史数据。

## 数据口径

- 官方净值来自 Eastmoney 公共基金接口。
- 盘中估值优先使用 Eastmoney `FundCoreDiyNew` 批量接口；主源整体失败时使用
  `FundValuationLast`，缺失代码再由新浪基金估值逐只补充。第三方估值仅供参考，
  不是官方净值或成交价格。
- 估值页主指标按「展示日」取值（`summary.today.display_session`）。当日有有效估值或官方净值时为今天：
  按当前金额 × 北京时间当日有效基金估值涨跌幅计算，标「今日预估」；同日官方净值披露后该基金改按官方涨跌幅，
  未披露基金继续预估，每只只计一次，全部为官方时标「今日收益」。金额基准日期缺失不阻止预估展示。
  国内当日完整源估值可用于预估，即使前值未能核验，仍不升级为可核对收益；
  已知基准冲突、前值日期异常、字段缺失、失败或跨日估值不参与。
- 当日没有任何可用数据（周末、节假日、盘前）时，展示日回退到最近官方净值日，标「MM-DD 当日收益」，
  只合计净值日等于展示日的基金；单只优先取 `fund_official_daily_profit` 记录，否则按当前金额 × 官方涨跌幅。
  覆盖不全时标「已更新 n/总数」；净值日期较早的基金（如 QDII）照常显示数值并带「MM-DD」标记，不计入合计。
- 基金行只在异常时显示标记：净值日期早于展示日显示「MM-DD」；展示日为今天时，估值过期显示「延迟」、
  交易时段无估值显示「无估值」，官方与预估混合时预估行显示「估」。完整来源与时间在行提示和详情中。
- 海外归属不明的最近估值直接显示在基金行，不计入今日预估；同日官方数据可单独参与官方测算。
- 主指标不是实际结算收益。详情中的可核对收益继续按北京时间日期 `D` 选择，并区分三种口径：
  - `official_measurement`：官方净值日恰为 `D`，且金额基准日恰为 `D` 前一自然日时，按
    `amount × official_return_pct / 100` 纳入可核对小计，标「已披露净值测算」。
    当前官方快照没有涨跌前基准日；跨周末、节假日或更早的金额保留为情景，不猜交易日。
  - `estimate`：当日尚无官方净值、盘中估值完整且归属 `D`、来源前值日期早于 `D`、
    净值比与涨跌幅校验通过，且金额日期与来源前值日期一致时，
    按 `amount × estimated_return_pct / 100` 纳入预估小计，标「未披露预估」。
  - `unknown`：日期、金额基准或来源基准无法确认时，可核对收益为空。
- 已披露 `D` 的官方净值但金额基准未知或不匹配时，不把旧估值当成已确认收益；
  仍可查看「按当前金额情景测算」，且官方情景与预估情景分栏，不把混合净额标成全账户实际收益。
- 汇总的官方情景仅含未纳入可核对的官方测算；未披露预估情景仅含未纳入可核对、且当日尚未披露
  官方净值的基金。详情仍保留官方和预估对照值，因此详情情景字段不能不加筛选地相加作为首页小计。
- 来源前值缺失、前值日期等于或晚于估值日、估值净值缺失或基准冲突时，预估不进入可核对合计。
- 无日期的导入 `daily_profit` 只作为「历史导入收益，日期未记录」，跨日也不进入今日值或今日合计。
- 部分覆盖只显示已覆盖部分的小计和覆盖范围；全部缺失显示「无法确认全账户当日收益」，不显示为 `0`。
  真正可用且基准确认的 `0%` 才显示 `0.00`。
- 可选 `amount_nav_date` 表示持仓金额对应的净值日期。旧数据保持为空，不猜测、不自动回填今天；
  修改金额时若未同时确认该日期，旧确认失效。日期存在也不等于已满足实际收益计算的全部条件。
- 海外估值不把 `pre_date` 当作收益归属日；净值比与来源涨跌幅不一致时保留源率并标记原因，不覆盖源字段。
- 新浪估值固定使用 `pre_nav` 与 `nav_pct` 同一组字段，按来源交易时段（含跨午夜）取最后一个分钟点。
  海外最近估值直接显示在基金行的「当日涨跌」列，带来源日期时间和涨跌基准日，
  不计入今日合计；基准冲突单独标出。
- 官方净值状态区分披露与市场交易：境内基金按国内交易日；QDII 净值日为国内与海外同时开市的日期，
  于下一个国内交易日晚间披露，国内休市顺延不判滞后。行内始终显示官方净值日期，详情显示下次披露日。
- 估值页的浏览器轮询只在页面可见时自动刷新（服务器部署另有独立采集 timer，见下文）：初次加载时补拉一次最近交易日数据（带 3 分钟冷却，跨标签页与重新进入页面不重复请求；后台加载时保留首次补拉待处理状态，首次切换为可见时先同步服务端最近运行时间，数据就绪后执行一次判断）；页面可见期间交易时段按 5 分钟、国内披露窗口 10 分钟轮询，其余时段仅对逾期或源失败做一次性补刷，不把首次补拉条件带入常规 30 秒轮询；非今日收盘估值保留来源真实日期并标为过期，绝不计入今日预估收益；连续失败退避，3 次后暂停直到手动刷新或重新可见。多个标签页同时刷新时后端合并为一次。状态行区分「已刷新」与来源是否真的有新数据，刷新失败保留原数据和阅读位置。
- 历史官方日收益保留首次观察金额，标「按首次观察金额测算」；累计是已记录日期合计，不补齐缺失日期。
- 持有收益优先采用已确认的截图原值，并保存 `profit_snapshot_date` 与 `profit_source`；
  缺少截图原值时保留原成本测算。修改金额、份额或基金代码且未重新确认收益时，截图持有收益失效。
- 录入支持 `imported_holding_profit`、`profit_snapshot_date`、`profit_source` 与 `daily_profit_date`。
  截图日期不是净值日期，不反推成本、份额或金额基准日。日期明确的日收益按日期和来源分组展示，
  不与官方测算、盘中预估或无日期导入值相加；只保存当前持仓的最近一次导入记录，不代替交易账本。
- 市场行情使用东财 `ulist.np/get`，纳斯达克 100 对应 `100.NDX100`；`100.NDX` 不用于该指标。
  单行情接口 502 与是否开盘无关，来源失败保留旧快照及失败状态。
- 市场概览使用 Eastmoney 延时行情，固定展示中证全指、黄金 9999 和 Nasdaq-100；
  只保存最近成功快照，不参与基金估值或收益计算。黄金夜盘按上金所 20:00-02:30 计，
  周五夜盘持续到周六 02:30。板块或指数涨跌不替代基金涨跌幅。
- `amount` 是持仓规模和估值计算的唯一主字段；金额解析使用十进制口径，不根据净值反推份额。
- `shares` 只保存截图识别或人工确认的真实份额，使用 SQLite `TEXT`；缺失时保持为空，
  不参与默认估值公式。
- 可在持仓页手工维护成本和金额基准日期；截图候选可以保留当日收益、累计收益等可选字段，
  截图流程不推断金额基准日期。

## 写请求来源校验

`POST`、`PUT`、`PATCH`、`DELETE` 在读取请求体和进入业务处理前统一校验来源，失败返回 `403`。
`Host` 的主机名必须在可信列表内：默认只有 `127.0.0.1`、`localhost` 和 `::1`；隧道子域与局域网主机名
用环境变量 `FUND_ANALYSIS_WRITE_HOSTS`（逗号分隔，可写 `主机名`、`主机名:端口` 或 `[IPv6]`；
按主机名匹配，端口只做校验不参与匹配；不支持通配符）显式加入，写法非法时启动即失败。
这一层挡住 Host 与 Origin 同为攻击者域名的 DNS rebinding 形态。

携带 `Origin` 时，只接受与请求协议及原始 `Host`（含端口）完全一致的来源；拒绝 `null` 与任何异源值，
重复出现完全相同的合法值不再误判。`Sec-Fetch-Site` 一旦存在，必须为 `same-origin`，`same-site` 也不放行。

无 `Origin` 时，存在 `Referer` 则校验其完整来源；无 `Referer` 则接受 `Sec-Fetch-Site: same-origin`。
三种来源头均缺失时，不携带 Cookie 的请求继续放行，保留现有 `collect` CLI；携带 Cookie 则拒绝。
这些检查不提供账户认证，也不限制能自行构造请求头的非浏览器客户端。

`fund-analysis web` 固定只信任来自 `127.0.0.1` 的 Uvicorn 转发头，不受 `FORWARDED_ALLOW_IPS` 扩大影响；
不采用 `X-Forwarded-Host` 或 `Forwarded`。Vite 保留原始 `Host`。Cloudflare Tunnel 接入时也须保留原始 `Host`，
外部 HTTPS 由可信本机代理传入的 `X-Forwarded-Proto` 还原；Cloudflare 会覆盖客户端伪造的该头，
见 [Cloudflare HTTP headers](https://developers.cloudflare.com/fundamentals/reference/http-request-headers/#x-forwarded-proto)。
隧道子域还要写进 `/etc/fund-analysis.env` 的 `FUND_ANALYSIS_WRITE_HOSTS`，否则经隧道的写请求会被拒绝。
本次没有新增 CORS 放行。

## 检查

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m compileall -q src tests
pnpm --dir frontend check:routes
node frontend/scripts/check-today-profit.mjs
node frontend/scripts/check-refresh-policy.mjs
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend build
```

## 服务器部署与后台采集

服务器 `ocvps` 使用 Python venv + systemd，监听 `127.0.0.1:8765`。不使用 Docker，
不修改现有 OpenResty、DNS 或其他服务。整个站点通过 SSH 隧道访问；没有认证时不得开放公网接口。
本地 Web 默认端口也为 `8765`，Vite 开发代理使用该端口。

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18765:127.0.0.1:8765 ocvps
```

隧道运行期间打开 <http://127.0.0.1:18765/valuation>。关闭隧道、浏览器或本机休眠只会停止本机访问，
服务器采集继续运行。服务器数据库从部署日起独立维护；本地原库保留，后续更新代码不再覆盖服务器库。
没有支付宝账户同步，采集不会更新持仓金额、成本或金额基准日，也不代表真实账户结算收益。

| 项目 | 位置或名称 |
| --- | --- |
| 代码与各版本独立 venv | `/opt/fund-analysis/releases/<release-id>` |
| 当前 / 上一个版本 | `/opt/fund-analysis/current`、`/opt/fund-analysis/previous` |
| 持久数据库 | `/var/lib/fund-analysis/fund_analysis.db` |
| 一致性备份 | `/var/lib/fund-analysis/backups/` |
| 常驻服务 | `fund-analysis.service` |
| 定时任务 | `fund-analysis-collect.timer` → `fund-analysis-collect.service` |
| 服务账户 | `fund-analysis`，无登录 shell，数据目录 `0700`、数据库 `0600` |
| 可选敏感配置 | `/etc/fund-analysis.env`，由 root 管理，权限 `0600`，不写 Git |
| 可信写来源主机名 | 同文件中的 `FUND_ANALYSIS_WRITE_HOSTS`（隧道子域用；loopback 已内置） |

Web 和 timer 进程设置 `TZ=Asia/Shanghai`，系统时区仍为 UTC。Web 保持单进程；采集命令向
localhost 的既有刷新 API 发请求，网页和后台共用一把锁。不要另起直接写库的采集进程或多个 Web worker。
`POST /api/funds/refresh?scheduled=true` 在锁内判断交易日和时间槽；已运行的槽依据数据库记录跳过，
重启仍能去重。默认 POST 与不带 `--scheduled` 的 CLI 都保留手动立即刷新行为。

### 采集安排（北京时间）

Timer 每 5 分钟检查一次，窗口外不访问行情上游。

| 窗口 | 采集频率与用途 |
| --- | --- |
| 国内交易日 09:30–11:30、13:00–15:30 | 每 5 分钟；含午间收盘点与 15:00、15:05…15:30 收盘重试 |
| 国内交易日 16:00、17:00、18:00 | 每小时一次补刷，兼顾早披露与收盘源延迟 |
| 国内交易日 19:00–23:50 | 每 10 分钟刷新官方净值并对账 |
| 国内交易日 08:00 | 昨晚迟披露补刷 |
| 前一天为国内交易日的次日 00:10 | 跨日披露补刷，含周六及长假第一天 |
| 香港交易时段 / 美股常规交易时段 | 上述窗口外每 30 分钟；美股复用现有日历与纽约时区，自动处理夏令时 |

定时采集参考包括网页刷新在内的运行记录去重；手动刷新仍立即执行。盘中最后一次刷新不会跳过新的收盘槽。失败保留原快照，
写入来源失败记录，CLI 以非零退出码报告部分失败；下一采集槽继续尝试。Timer 的 `Persistent=true`
在重启后补做当前窗口判断，不伪造停机期间漏掉的分钟行情或历史收盘样本。
本地代码已将估值改为按来源追加候选，保留原报价、基准及首次／最近采集时间，重复采集去重；
仅通过境内收盘与可比基准校验的样本进入新误差统计，QDII 未确认的归属继续为空。
2026-10-28 [发布前验收](docs/qdii-estimate-validation.md#7-发布前验收与具体发布方案2026-10-28)完成，
原 3 项 P2 已修复且独立复审通过（据用户确认），`20261028-qdii-evidence-1` 经确认已发布：
448 条历史样本原值保留，189 条旧误差因缺少历史证据退出新统计；8 只 QDII 的 221 条记录未回填。
生产迁移逐值保留历史原字段，18 条新候选留证与连续刷新去重通过；Web 和两个 timer 已恢复，
真实持仓与模拟库未变，生产 UI 保留原版。主库备份为 `backups/pre-20261028-qdii-evidence-1.db`；
迁移后回滚必须连同数据库恢复。黄金行情来源失败在发布前已存在，未纳入本次修复；16:00 采集因此部分失败并返回退出码 1，timer 继续运行。
详见[线上验收记录](docs/qdii-estimate-validation.md#8-生产发布与真实运行验收2026-10-28)。
交易日历只覆盖 2025–2026 年，未覆盖年份报错停止自动采集，不猜工作日；年底前需依据官方公告更新日历。

### 检查与手动采集

以下命令在服务器执行（先 `ssh ocvps`）：

```bash
systemctl is-enabled fund-analysis.service fund-analysis-collect.timer
systemctl is-active fund-analysis.service fund-analysis-collect.timer
TZ=Asia/Shanghai systemctl list-timers --all fund-analysis-collect.timer
sudo journalctl -u fund-analysis.service -u fund-analysis-collect.service --since today --no-pager
curl --fail --silent --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/funds > /dev/null

# 与 timer 完全相同的任务；窗口外或本槽已采集时跳过。
sudo systemctl start fund-analysis-collect.service
# 无浏览器立即采集；部分上游失败时退出码为 1，成功部分仍入库。
sudo -u fund-analysis env TZ=Asia/Shanghai \
  /opt/fund-analysis/current/.venv/bin/fund-analysis collect --port 8765
```

`journalctl` 中的来源错误与 API / SQLite 中的 `data_source_runs` 相对应。
确认时间更新可只读取运行记录，不需打开页面或修改持仓：

```bash
sudo -u fund-analysis python3 - <<'PY'
import sqlite3
with sqlite3.connect('file:/var/lib/fund-analysis/fund_analysis.db?mode=ro', uri=True) as db:
    for row in db.execute('SELECT job_type,status,started_at,completed_at,row_count,error_message FROM data_source_runs ORDER BY id DESC LIMIT 3'):
        print(row)
PY
```

### 更新

在已审阅的本地 checkout / worktree 中构建并上传代码。包内不包含数据库、`.env`、本机 venv 或 Node 依赖。
每次使用唯一版本号，安装脚本拒绝覆盖已有版本；首次安装联网解析依赖，后续更新默认沿用上一版
`requirements-installed.txt` 中的依赖版本。需要升级依赖时先单独核验并调整约束。

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
release_id=$(date +%Y%m%d-%H%M%S)
COPYFILE_DISABLE=1 tar --no-xattrs --exclude=__pycache__ \
  -czf "/tmp/fund-analysis-$release_id.tar.gz" \
  src frontend/dist pyproject.toml README.md AGENTS.md deploy tests
scp "/tmp/fund-analysis-$release_id.tar.gz" deploy/install-release.sh ocvps:/tmp/
ssh ocvps "bash /tmp/install-release.sh /tmp/fund-analysis-$release_id.tar.gz $release_id"
```

脚本先安装新版本 venv、检查依赖与 SPA 文件、预检 systemd 配置，再检查当前版本健康状态并做 SQLite backup。
然后只停止本项目任务和服务、切换版本链接、安装本项目 unit、启动 Web；HTTP 检查通过后才启用 timer。
更新期间有短暂停机。HTTP 检查的连接超时为 2 秒、单次总超时为 5 秒，启动检查最多尝试 30 次。
启动检查失败时 timer 保持停止，查日志后按下节回滚；当前版本不健康时再次发布会被拒绝，`previous` 保持不变。
备份文件为 `backups/pre-<release-id>.db`。初次真实库迁移已完成；后续更新不得重复上传本地旧库。

### 备份与回滚

运行中可执行一致性备份，不直接复制活跃的 `.db` 或 `.db-wal`：

```bash
ssh ocvps
backup_id=$(date +%Y%m%d-%H%M%S)
sudo -u fund-analysis python3 /opt/fund-analysis/current/deploy/backup.py \
  /var/lib/fund-analysis/fund_analysis.db \
  "/var/lib/fund-analysis/backups/manual-$backup_id.db"
```

备份脚本独占创建目标、通过 SQLite backup 包含 WAL 已提交数据，再做 `integrity_check`；
目标已存在时拒绝覆盖。当前提供手动和更新前备份，未配置自动备份、异机副本或自动清理。

以下代码回滚命令仅适用于已验证 schema 兼容的历史版本。QDII 候选
`20261028-qdii-evidence-1` **不适用**：旧代码会误计旧误差并批量覆盖新候选，迁移后必须恢复
迁移前主库，顺序和数据损失边界见[本次回滚方案](docs/qdii-estimate-validation.md#77-回滚与数据影响)。
不得直接使用本节命令回退 QDII 修复。兼容版本才可在服务器执行：

```bash
set -e
rollback_release=$(readlink -f /opt/fund-analysis/previous)
test -x "$rollback_release/.venv/bin/fund-analysis"
sudo systemctl stop fund-analysis-collect.timer fund-analysis-collect.service fund-analysis.service
ln -s "$rollback_release" /opt/fund-analysis/current.rollback
mv -Tf /opt/fund-analysis/current.rollback /opt/fund-analysis/current
sudo install -m 644 "$rollback_release"/deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start fund-analysis.service
for attempt in $(seq 1 30); do
  if curl --fail --silent --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/funds > /dev/null; then break; fi
  sleep 1
done
curl --fail --silent --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/funds > /dev/null
sudo systemctl start fund-analysis-collect.timer
```

上述早期发布无 schema 变化；这不是后续版本只回滚代码的依据。恢复备份前必须先停服务、另做当前库的一致性快照，
核验选定备份后再替换；这会丢失备份之后的数据，属于需单独确认的操作，本次未执行。
若需离机备份，可通过 `ssh ocvps 'sudo cat <已核验快照路径>'` 保存到本机，权限设为 `0600`；
不将原库、快照或敏感配置写入 Git。

### 本次真实验证（2026-09-30）

当前版本为 `20260930-collection-6`，上一个可回滚版本为 `20260930-collection-5`。
本机与服务器 ARM Python 3.12 的后端 230 项测试均通过；前端构建、类型、lint、路由和两项策略回归通过。
服务与 timer 为 active / enabled；11:40、11:45、11:50 自动任务日志证明时间槽去重实际执行。
验收最后检查为北京时间 11:50:37：下一次 timer 检查是 11:55，下一新采集槽为 12:00（香港午间收盘点），
境内下午从 13:00 恢复，收盘 15:00–15:30 重试、官方披露 19:00–23:50 采集。
关闭浏览器后 11:44:51–11:45:01 手动采集官方净值和估值各 18 只成功，数据库写入真实运行时间；
当前持仓的官方日期为 09-28–09-29，估值来源时间为 09-30 04:00–11:43。
来源日期不会因今天采集而改成今天；当天 18 只样本已积累，收盘归属和后续对账尚未发生。

SQLite 完整性与持仓指纹核对通过，本地原库未变。更新前备份、手动在线备份、回滚到版本 5
并恢复版本 6 均实际执行且 HTTP 检查通过；没有恢复数据库或修改持仓。
SSH 隧道中的三个 SPA 页面均能加载，估值页渲染 18 行且无浏览器错误。

市场概览域名 DNS / TLS 可通，接口间歇出现 `502` 或关闭连接；11:29 曾成功 2 项，11:45 为 3 项失败，
失败状态与错误写入运行记录，最近成功行情仍保留。未换源、填默认价或伪装成全量成功。
截图识别凭据未迁移，服务器识图功能暂未配置；可在 `/etc/fund-analysis.env` 中配置既有 OCR 环境变量。
未开放公网或配置自动 / 异机备份。真实收盘自动采样、晚间披露和跨日对账尚未等到对应时段，
不能由模拟测试替代；QDII 收益归属仍未知，日历仍需补 2027 年。

### 阶段 7 模拟部署（2026-10-18）

版本 `20261018-025612` 已部署（上一版 `20260930-collection-6`）。本次新建
`/etc/fund-analysis.env`（root `0600`，`FUND_ANALYSIS_SIMULATION_DB_PATH=/var/lib/fund-analysis/simulation.db`），
补齐 web 单元读取模拟库的路径；真实库无 schema 变化，更新前备份 `backups/pre-20261018-025612.db`。

服务器已初始化生产模拟库并启用 timer（`fund-analysis-simulation.timer`，每 5 分钟判断一次）：
账户 `acct_57e3deb6871f420c9e98`（200,000.00 元）、规则 `prod_016665_alipay_20261018`
（支付宝交易规则截图与东财费率页交叉核对，截图存档于 `deploy/rules/evidence/`）、
策略 `strat_42f234003fbb83ba`（每周三 14:00、016665 每期 500.00 元，2026-10-21 生效）。
备份 `backups/sim-20261018-pre-timer.db` 与恢复演练 `backups/sim-restore-drill-20261018.db`
通过账本重放核对（P0-B 完成）；手动 settle 实测经 lsjz 取得 016665 官方净值 2.2806（10-15）
写入模拟库，timer 5 分钟触发与窗口跳过已在 journal 验证。

7.5 预计验收窗口为 2026-10-21 至 10-23：10-21 受理买入、10-22 晚间取得官方净值、10-23 确认后逐笔复算。
**结果（2026-10-28）：首期按此时序完成并复算通过，详见 `docs/simulation-plan.md` 第 13 节。**
完成时间以实际受理、对应官方净值取得和结算结果为准；净值延迟则继续待确认，跳过不算成交验收通过。
剩余风险：2027 年日历需在 12 月前补；手机远程为 P0-A，以 Cloudflare 隧道接入时须同时启用
Zero Trust 认证——当前 web 无认证，不得直接公开。P0-A 方案见
[`docs/p0a-mobile-remote-access-plan.md`](docs/p0a-mobile-remote-access-plan.md)，尚未执行。

2026-10-23 线上复验补充：东财列表接口亦出现持续 502。三项行情在主源失败时分别使用腾讯（中证全指、纳指 100）和新浪（上金所 Au99.99），校验标的、价格、涨跌幅与报价时间；运行记录保留主源错误和实际来源。备用源也失败时继续保留旧快照并标失败。
