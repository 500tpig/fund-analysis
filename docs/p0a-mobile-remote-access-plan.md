# P0-A 手机安全远程访问方案

状态：方案已准备（2026-10-28），尚未执行。方案选择已有倾向（复用现有隧道、邮箱验证码、不做跨窗口生产暂停），
但外部配置变更尚未授权；本轮及此前只做调查、隔离测试与风险检查：未登录或修改 Cloudflare，
未改动 DNS、服务器配置、生产代码或生产数据。账户信息由用户补充；生产暂停／恢复的时段待下述隔离测试
通过后再定。

## 1. 目标与边界

目标：手机经移动网络访问生产站点，查看模拟账户并暂停、恢复新增买入；不依赖桌面电脑持续开启 SSH 隧道。
这是阶段 7 剩余的「手机真机可查看并暂停」验收所需的前置条件。

边界：

- 沿用项目既定方向 Cloudflare Tunnel + Zero Trust Access，不开放公网端口，不修改 OpenResty。
- 不新增代码、不发布 UI 改版、不处理 tick 重复查询优化、不启动阶段 8。
- 现有 SSH 隧道访问方式保持不变，作为回退路径。

## 2. 现状核查（2026-10-28，只读）

| 项目 | 核查结果 |
| --- | --- |
| 服务器 | `ocvps`，Ubuntu 24.04、aarch64、4 核、约 24 GB 内存，根分区使用 24% |
| 应用监听 | `fund-analysis.service` 仅监听 `127.0.0.1:8765`；应用本身无登录、无 Host 校验，FastAPI 默认的 `/docs`、`/openapi.json` 未关闭 |
| 公网入站 | 主机 `iptables` 的 INPUT 链仅放行 TCP 22、3030 与已建立连接，其余 REJECT；OpenResty 虽监听 80/443，但主机层不放行。云侧安全列表未核查 |
| cloudflared | 已安装并运行（2026.3.0，systemd `cloudflared.service`，自 2026-10-20 12:29 UTC 随服务器重启启动）。以令牌方式运行，路由由 Cloudflare 控制台远程托管，只有出站连接，本地 `127.0.0.1:20241` 提供就绪与配置只读接口 |
| 既有隧道内容 | 配置版本 54，6 条既有主机名路由加 1 条末尾默认规则（404）。这些路由属于服务器上的其他应用，与本项目无关，不得停止、重启或修改 |
| 域名 | 既有路由使用的域名在同一 Cloudflare 账户中，可复用其下的新子域；是否已启用 Zero Trust 组织未知，需用户确认 |
| 接口形态 | 无 SSE、无 WebSocket；前端统一用 `fetch`。写接口：持仓增删改、`/api/funds/refresh`、截图识别与确认、模拟账户创建／撤单／暂停／恢复 |
| 请求耗时 | 近 14 天刷新任务平均 11.9 秒、最长 69.3 秒，低于 Cloudflare 代理约 100 秒的响应等待上限（实施时以官方文档为准）。服务器未配置识图变量，识图当前不可用；若日后启用，默认 180 秒超时会超过该上限 |
| HTTPS | 经 Cloudflare 访问为 HTTPS，`crypto.randomUUID` 可用；2026-10-19 记录的「局域网 http 下转圈」问题不会出现 |

官方资料：[Private web application](https://developers.cloudflare.com/cloudflare-one/setup/secure-private-apps/private-web-app/)、
[Create an Access application](https://developers.cloudflare.com/learning-paths/zero-trust-web-access/access-application/create-access-app/)、
[Session management](https://developers.cloudflare.com/cloudflare-one/identity/users/session-management/)、
[Create a tunnel (dashboard)](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)。
已确认的要点：Access 应用默认拒绝，用户必须匹配 Allow 策略；添加已发布应用路由后主机名对互联网可达，
保护只来自 Access 应用；Zero Trust Free 开通时仍需登记付款方式，但不扣费（官方 Get started 文档）；
会话时长默认 24 小时，可设为立即过期到一个月；`/cdn-cgi/access/logout` 与「Revoke existing tokens」
可使会话失效。

## 3. 最小方案

### 3.1 架构

```text
手机浏览器 ──HTTPS──▶ Cloudflare 边缘（Access 校验身份）──▶ 既有 cloudflared（出站隧道）
                                                         └─▶ http://127.0.0.1:8765 （fund-analysis.service）
```

复用服务器上已运行的隧道，只在 Cloudflare 控制台新增一条已发布应用路由；服务器无需任何改动。
路由的服务地址写 `http://127.0.0.1:8765`，不写 `localhost`，避免解析到未监听的 IPv6 地址。

备选：新建专用隧道，在服务器新增一个 systemd 服务和令牌文件。隔离性更好，但多出服务器变更与一份新凭据，
不属于最小方案，仅在用户不愿与其他应用共用隧道时采用。

### 3.2 保护范围

一个 Access 应用，覆盖整个子域，不设路径例外、不设 Bypass、不设 Service Auth：

- 页面：`/`、`/valuation`、`/holdings`、`/import`、`/simulation`、静态资源与 legacy 308 跳转。
- 全部 `/api/*`：读取、持仓写入、刷新、截图识别、模拟账户读取与写入。
- `/docs`、`/redoc`、`/openapi.json`。

应用本身不提供认证，因此 Access 是唯一的认证层；任何未经 Access 的路由都等于公开。

### 3.3 身份与访问策略

- 登录方式：一次性 PIN（邮箱验证码，免配置）。如用户希望使用 Google 或 GitHub 账号并依赖其二步验证，
  可改用对应登录方式，需额外配置 OAuth 应用。
- 策略：仅一条 Allow，Include 为用户本人的单个邮箱；不加通配域名、不加其他邮箱。
- 会话：首轮验收使用默认 24 小时，便于验证过期与重新登录；验收通过后由用户决定是否延长。
- Access 应用的 Cookie 设置中启用 HttpOnly 等选项，以控制台实际提供的选项为准，并在验收记录中写明最终值。

## 4. 实施顺序（用户在控制台操作）

顺序固定：先建 Access 应用，再添加路由。反过来会出现主机名已公开而尚无保护的空窗。

| 步骤 | 操作者 | 内容 | 对外影响 |
| --- | --- | --- | --- |
| S0 | 用户 | 回答第 8 节的待确认事项 | 无 |
| S1 | 用户 | Zero Trust 中创建自托管 Access 应用（子域、单邮箱 Allow 策略、24 小时会话） | 主机名尚无 DNS，不可达 |
| S2 | 本方 | 只读基线：隧道配置版本与路由数、模拟账户状态与版本、两个服务与 timer 状态、持仓表指纹 | 无 |
| S3 | 用户 | 现有隧道上添加已发布应用路由（子域 → `http://127.0.0.1:8765`），控制台会同时创建代理 DNS 记录 | 主机名开始可达，已受 Access 保护 |
| S4 | 本方 | 立即做未登录外部探测（第 6 节）；任一探测拿到应用内容则立即回滚 | 无 |
| S5 | 用户 | 手机验收（第 7 节 A–C） | 只读 |
| S6 | 用户确认 | 暂停／恢复验收（第 7 节 D），仅在约定的非受理窗口 | 改变生产策略状态 |
| S7 | 本方 | 复查隧道配置、服务状态与账本，写入验收记录 | 无 |

## 5. 部署影响与回滚

### 5.1 影响

| 范围 | 影响 |
| --- | --- |
| 服务器文件、服务、防火墙、代码 | 无变更，不重启任何服务，采集与模拟 timer 不受影响 |
| 既有隧道 | 路由配置由控制台下发并热加载，版本由 54 变为 55，路由数 6 变为 7；其他 6 条路由不应变化，S2、S7 对比确认 |
| Cloudflare 账户 | 新增 1 个 Access 应用、1 条策略、1 条路由、1 条 DNS 记录；Zero Trust Free 需登记付款方式，不扣费 |
| 数据面 | 页面与 API 流量在 Cloudflare 边缘终止 TLS，持仓金额、模拟账户数据与上传截图在技术上可被 Cloudflare 读取。这是 Cloudflare Tunnel 方案固有的特性，需用户接受 |
| 现有访问方式 | SSH 隧道照常可用 |

### 5.2 回滚

回滚不需要登录服务器，也没有文件需要还原。顺序固定：

1. 删除该子域的已发布应用路由。此后子域不再到达服务器。
2. 若控制台未同步删除，手动删除对应 DNS 记录。
3. 删除或停用 Access 应用。必须晚于第 1 步，否则路由仍在而保护已消失。
4. 需要立即中断已登录会话时，在 Access 应用中执行「Revoke existing tokens」。

无法立即删除路由时的应急收口：先把 Access 策略改为不匹配任何人（移除邮箱）并撤销令牌，再删除路由。

回滚验收：外部探测不再返回应用内容；服务器上读取 `127.0.0.1:20241/config`，路由数回到 6 条，
其余路由与基线一致；SSH 隧道仍可访问。

禁止的回滚方式：停止或重启服务器上的 `cloudflared.service`。它同时承载其他应用的路由。

## 6. 外部探测（未登录，S4 与回滚后使用）

从本机对新子域发起以下请求，不携带任何登录凭据。通过标准：全部返回 Access 的登录跳转（302 指向
`*.cloudflareaccess.com`）或 403，响应体不含应用页面或 JSON。

- `GET /`、`GET /simulation`、`GET /api/funds`、`GET /api/holdings`、`GET /api/simulation/accounts`
- `GET /docs`、`GET /openapi.json`、一个已知的静态资源路径
- 带伪造头 `Cf-Access-Jwt-Assertion: invalid` 的 `GET /api/simulation/accounts`
- `POST /api/simulation/accounts/acct_probe/pause`，带 `Idempotency-Key` 与 JSON 体。使用不存在的账户 ID：
  未被拦截时得到 404，不会改变任何状态；被拦截时应为 302 或 403

任一请求返回应用内容（200 或应用自己的 404／422），视为保护失效，立即按 5.2 回滚。

## 7. 验收计划

### A. 保护有效性

第 6 节全部通过；Zero Trust 的 Access 登录日志出现用户本人的登录事件。

### B. 手机查看（只读）

使用真机，关闭 Wi-Fi，走移动网络。记录机型、系统版本、浏览器、网络类型和时间。

1. 访问新子域，完成邮箱验证码登录。
2. 依次打开估值、持仓、录入、模拟四个页面，确认加载、无横向溢出、无控制台可见错误。
   估值页首次打开可能触发现有的刷新逻辑，会写入行情与估值记录，不修改持仓；这是既有行为。
3. 模拟页核对：账户总览（总资产、可用现金、冻结）、持仓、订单列表与详情（含已完成订单
   `ord_3560958e…` 与待净值订单 `ord_6f34daea…` 的当前状态）、运行记录与详情、费用后指标。
4. 页面数值与同一时刻服务器只读查询的库内数值一致。验收时不点击暂停、刷新、识图。

### C. 会话与访问控制

1. 用未在策略中的邮箱请求验证码：应被拒绝，且不收到验证码。
2. 访问 `/cdn-cgi/access/logout` 后再打开站点：必须重新登录。
3. 页面保持打开时在控制台执行「Revoke existing tokens」，再在页面内触发一次接口请求：记录页面表现。
   预期为请求失败并提示通用错误，重新载入页面后进入登录流程；Access 对此类子请求默认不返回 401。
   官方给出的做法是请求带 `X-Requested-With: XMLHttpRequest` 以获得 401，需要前端小改动，
   不在最小方案内，视这次观察结果另行决定。
4. 默认 24 小时会话到期：次日首次访问确认需重新登录，作为非阻塞项记录。

### D. 暂停状态持久化与调度行为（生产状态变更，时段由用户在隔离测试通过后确定）

只规划，未执行。暂停与恢复会改变生产策略状态，并在模拟库永久追加记录。隔离测试已于 2026-10-28 通过
（见下方「调度行为的覆盖方式」），生产暂停时段待用户指定。

窗口选择：

- 下一个受理窗口是 2026-11-04（周三）14:00–14:10。暂停期间不得覆盖该窗口。
- 建议窗口：2026-10-30（周五）17:30 暂停，18:50 之后恢复。待净值订单 `ord_6f34daea…` 的确认到期时点是
  当日 18:00，结算槽在 18:30–18:45，因此可同时观察暂停期间结算是否照常。
- 若 10-28 净值尚未取得，持久化部分照常验收，结算部分顺延，不提前判定。

恢复前置：恢复会重新校验数据完整性、净值更正与回撤。暂停前先只读确认当前估值完整、无净值冲突、
回撤远低于 15%。若恢复被拒绝，保持暂停并报告原因，不绕过；11-04 13:50 前仍未恢复，当周买入会被跳过。

步骤与通过标准：

| 步骤 | 操作 | 通过标准 |
| --- | --- | --- |
| D1 | 暂停前只读基线 | 账户 `active`、记录版本号、两笔订单状态、最近一次运行为 10-28 |
| D2 | 手机点击「暂停买入」 | 页面显示已暂停；库内 `status=paused`、`pause_reason=manual`、版本号加 1、新增一条 pause 幂等记录 |
| D3 | 完全关闭手机标签后重新打开，另用桌面 SSH 隧道页面查看 | 两处均显示已暂停（状态来自库，不是页面缓存） |
| D4 | 暂停期间观察 timer | 每 5 分钟的 tick 持续运行且无失败；无新订单、无新运行记录 |
| D5 | 18:30 结算槽 | 订单 `ord_6f34daea…` 在净值已取得时按规则转为已完成，说明暂停不阻断结算；日终估值快照仍按时写入 |
| D6 | 手机点击「恢复买入」（18:50 之后） | 页面显示 `active`；库内 `pause_reason` 为空、版本号再加 1、新增一条估值快照、新增一条 resume 幂等记录、无新订单 |
| D7 | 账本复核 | 项目自带账本重放通过；现金、冻结、份额与预期一致；真实库持仓指纹不变 |

调度行为的覆盖方式（隔离 fixture 库，2026-10-28 通过）：

- 窗口内暂停（运行记录为已完成、该基金项跳过且原因为 `account_paused`、不产生订单）：
  `CycleLimitAndSkipTest.test_paused_account_skips_with_account_paused`（直接调用周期处理）与
  `PausedSettlementTickTest.test_paused_account_keeps_closing_and_skips_next_cycle`（经 `scheduled_tick`，
  下一个周三 14:05 的运行为 `completed`、该项为 `account_paused`、订单数不变）。
- 暂停后既有订单继续结算：已有 `CycleLimitAndSkipTest.test_settlement_continues_while_paused`
  （直接调用 `settle`，单步确认）。2026-10-28 新增 `PausedSettlementTickTest.test_pause_at_each_stage_does_not_block_settlement`，
  按生产形态经 `scheduled_tick` 与可控净值来源分步推进：受理 → 18:30 槽净值未披露 → 21:30 槽转待确认 →
  确认到期后的 18:30 槽已完成，分别在「受理后等净值」「净值未披露的结算槽后」「已待确认」三个阶段暂停；
  每种情形订单都按期完成（净值 1.2500、份额 400.00、费用 0.00），账户保持 `paused/manual`，
  期间仍只有 1 笔订单、1 条运行记录，账本重放无问题；暂停账户的日终快照照常生成。
  反向验证：在内存中让结算对非 `active` 账户直接返回，新增测试 4 项断言全部失败；补丁未写入源码。
- 上一版本方案曾写「暂停期间结算照常没有自动化测试」，该说法有误（`settle` 层已有用例），已更正；
  新增测试补的是经调度入口的分阶段形态。
- 隔离测试不能替代生产观察：D5 仍在生产中观察一次，作为真实数据与真实调度下的复核。
- 可选项 E（跨 11-04 14:00 的生产暂停，会损失一个真实样本）：用户已决定不做。

### F. 记录与收尾

保存手机信息、时间戳、Access 登录日志截图、探测结果、库内前后值与隧道配置前后对比，写入
`simulation-plan.md` 第 13 节并更新 roadmap。A–D 全部通过后 P0-A 完成，阶段 7 的「手机真机可查看并暂停」满足。
阶段 7 仍须完成：2027 年交易日历、10-28 周期订单确认、真实库指纹在下次结算前后留档，以及按第 12 节约定
迁移稳定规则后再移除 `simulation-plan.md`。

## 8. 方案选择与待补充信息

已倾向的方案选择（2026-10-28，用户表示）：复用服务器现有隧道；邮箱一次性验证码登录；不做可选项 E。
这些是方案选择，不构成外部配置变更的授权；Cloudflare、DNS 与生产配置在授权前均不改动。

待用户补充或决定：

1. Cloudflare 账户是否已启用 Zero Trust 组织？Zero Trust Free 需登记付款方式（官方说明不扣费），是否接受？
   不接受时的备选是手机 SSH 端口转发，体验较差，不建议日常使用。
2. 新子域名称，建议 `fund.<既有域名>`；确认该域名在同一账户且可新增 DNS 记录。
3. 允许登录的邮箱，以及该邮箱是否已开启二步验证。
4. 是否接受页面与 API 流量经 Cloudflare 边缘终止 TLS。
5. 手机机型、系统与浏览器，以及是否有第二个邮箱用于 C1 的拒绝测试。
6. 生产暂停／恢复的日期与时段（隔离测试已通过，可定；建议仍选非受理窗口，下个窗口为 2026-11-04 14:00）。
7. 第 10 节跨站请求检查的处理：是否在对外开放前补应用侧保护。

## 9. 风险与限制

- 共用隧道：拥有该 Cloudflare 账户权限者可修改任一路由。应用不校验 Access 令牌，路由配置错误即等于公开。
  可选加固是源站校验 `Cf-Access-Jwt-Assertion`，需改代码并发布，本次不做。
- 一次性 PIN 的安全性取决于邮箱本身，丢失邮箱即丢失访问控制。
- 应用自身没有认证与跨站请求防护。第 10 节实测：持仓创建与录入确认可被不带自定义头的跨站简单请求写入，
  风险详情与处理选项见该节。模拟写接口因要求自定义请求头 `Idempotency-Key` 而不受影响。
- 会话过期时已打开页面的接口请求会失败，需手动刷新页面（见 C3）。
- 刷新接口耗时接近但低于代理等待上限；若日后启用识图，需单独评估超时。
- Zero Trust Free 为 50 用户上限，无服务等级承诺；本项目仅 1 个用户。
- 隧道配置只在控制台可见，仓库内没有对应记录；本文是唯一的变更记录来源，实施时需补写最终子域与设置值。

## 10. 写接口跨站请求检查（2026-10-28）

检查对象：应用全部写路由。方法：在隔离的临时库上，用 `TestClient` 发送浏览器跨站「简单请求」——带
`Origin: https://evil.example`、不带任何自定义头、`Content-Type` 为 `text/plain`、表单或 multipart、
无请求体。简单请求无需预检，页面脚本可直接发出；带自定义头或使用 PUT／DELETE 的请求必须先通过预检。
未接触生产、未改任何源码。

| 写路由 | 简单请求结果 | 判断 |
| --- | --- | --- |
| `POST /api/holdings` | `text/plain` 携带 JSON，返回 201，持仓数 0 → 1 | 可被跨站写入 |
| `POST /api/holdings/entry/confirm` | `text/plain` 携带 JSON，`merge` 模式返回 200，持仓数 1 → 2 | 可被跨站写入 |
| `POST /api/holdings/entry/confirm`（`replace`） | 返回 409：待删除持仓须与当前数据一致 | 攻击方不知道当前持仓，难以成功，但不是机制性保护 |
| `POST /api/holdings/entry/preview` | 返回 200 | 只读，响应跨站读不到 |
| `POST /api/funds/refresh` | 无请求体，处理函数被调用 | 可被跨站触发，只引起上游行情请求与估值记录写入 |
| `POST /api/holdings/entry/recognize` | multipart 到达处理函数（识图未配置时 503） | 配置识图后可被跨站消耗识图额度 |
| `PUT`／`DELETE /api/holdings/{id}` | 预检返回 405，无 `Access-Control-Allow-Origin` | 需要预检且应用无 CORS，被浏览器拦截 |
| 模拟 `POST` 账户创建／撤单／暂停／恢复 | 全部 422 `missing_idempotency_key`，账户状态与版本不变 | 自定义头 `Idempotency-Key` 触发预检，被拦截 |

原因：持仓相关的 JSON 接口用 `request.json()` 直接解析，不检查 `Content-Type`，所以 `text/plain` 也被接受；
应用没有 Origin／Host 校验，也没有 CORS 中间件。已用回归测试固定模拟写接口的保护：
`tests.test_simulation_api.SimulationApiTest.test_write_endpoints_reject_simple_cross_site_requests`。

实际影响取决于浏览器是否会在跨站请求中携带 Access 的会话 cookie（`CF_Authorization`）。其 `SameSite`
属性默认值未能从官方文档确认。若为 `Lax` 或 `Strict`，跨站 POST 不会带上该 cookie，会在 Access 层被拦；
若为 `None`，上表「可被跨站写入」的接口在用户已登录时会生效。验收时需在已登录浏览器检查 `Set-Cookie` 的
实际 `SameSite` 值并写入验收记录。

可选处理（均未实施；模拟写接口已有保护，下列为持仓类接口的补充）：

1. 在 Access 应用的 cookie 设置中选择 `Lax` 或 `Strict`（若控制台提供该选项），不改代码。
2. 应用侧加一个对非 GET 请求的 Origin 校验：存在 `Origin` 且主机与 `Host` 不一致则拒绝。
   同源前端不受影响；已有 SSH 隧道访问同样同源。需要改代码并发布。
3. 对 JSON 接口要求 `Content-Type: application/json`。前端已按此发送；同样需要改代码并发布。

建议在对外开放前至少落实第 1 项并验证实际值；第 2 项改动小、覆盖面最广，是否实施由用户决定。
本检查只报告，未修复。
