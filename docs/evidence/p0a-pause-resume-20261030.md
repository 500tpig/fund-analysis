# P0-A 生产暂停／恢复验收与 10-28 周期结算核对（2026-10-30）

按 [P0-A 方案](../p0a-mobile-remote-access-plan.md) 第 7 节 D 执行：17:34:55 手机暂停，19:02:06 手机恢复，
期间观察 18:30 结算槽。结论：**D1–D7 全部通过**；10-28 周期订单按规则确认，真实库持仓与交易指纹与
10-29 结算前基线一致。生产版本在验收全程为 `20261030-mobile-ui-3`。

## 方法

- 手机：三星 Galaxy S23 Ultra、Via 浏览器、移动网络，经 `fund.55770517.xyz`（Cloudflare Access 会话）操作。
- 检查点：[checkpoint.py](p0a-pause-20261030/checkpoint.py) 在服务器以 `fund-analysis` 用户、Python 3.12.3 运行，
  经 sqlite3 backup API 复制主库与模拟库到临时一致性副本，按
  [10-29 基线](p0a-settlement-baseline-20261029.json) 的 `fingerprint_spec` 逐行规范化并 sha256 聚合，
  再在副本上运行项目自带 `simulation show / metrics / verify`，结束删除副本。全程不写生产库。
- 对比：[compare.py](p0a-pause-20261030/compare.py) 先核对每个区块的 `columns` 与 `order_by`（全部与基线一致，无漂移），
  再按主键列出变化字段、新增与删除行。
- 暂停期间监视：[tick-probe.py](p0a-pause-20261030/tick-probe.py) 每 2 分钟只读采样一次，
  共 42 个样本（17:37:52–19:01:53），原始记录 [d4-monitor.log](p0a-pause-20261030/d4-monitor.log)。

## 结果

| 步骤 | 结果 | 依据 |
| --- | --- | --- |
| D1 暂停前基线 | 通过（以 16:37:48 试跑检查点代替，见「偏差」） | 账户 `active`、版本 1；`ord_3560…` settled、`ord_6f34…` awaiting_confirmation；最近运行 10-28；估值 `complete`、无缺失、回撤 0.0023%；`verify` ok；全部区块与 10-29 基线聚合相同 |
| D2 手机暂停 | 通过 | 17:34:55 写入：`status` active→paused、`pause_reason` manual、`version` 1→2；新增 1 条 `pause` 幂等记录（key `web-582120c0…`，网页发出并通过同源校验）；其他区块不变，`verify` ok（[对比](p0a-pause-20261030/d2-vs-baseline.json)） |
| D3 状态来自库 | 通过 | 手机完全关闭标签后重开显示「恢复买入」（用户确认）；17:35:45 桌面经 SSH 隧道打开为「已暂停买入」+「恢复买入」，无「暂停买入」（[截图](p0a-pause-20261030/d3-desktop-tunnel-1440-paused.png)） |
| D4 暂停期间调度 | 通过 | 暂停区间内模拟 service 完成 18 次、`failed_count` 全为 0、无失败结果；42 个样本中账户均为 paused，订单数恒为 2、运行记录恒为 2 |
| D5 18:30 结算槽 | 通过 | 18:30:00.746 `ord_6f34…` awaiting_confirmation→settled（版本 2→3）：份额 222.13、费用 0.00、净额 500.00、`rounding_delta` −0.007583，与按 500.00 / 2.2509、0.01 份 ROUND_HALF_UP 预先算出的值一致；新增批次 `lot_c3323ca8…`、账本事件 `buy_confirm`（冻结 −500.00、份额 +222.13）、订单事件 8；暂停期间观察估值 257→261 持续写入 |
| D6 手机恢复（19:02:06） | 通过 | `status` paused→active、`pause_reason` 清空、`version` 2→3；新增 1 条 `resume` 幂等记录（key `web-f97326a8…`）；同一时刻新增 1 条 `complete` 估值快照（总资产 199,957.85，按 10-29 净值 2.1662）；订单数仍为 2 |
| D7 账本复核 | 通过 | `verify` `{"ok": true, "problems": []}`；可用 199,000.00、冻结 0.00、份额 220.05 + 222.13 = 442.18，与预期一致；真实库持仓聚合 `65659182…`、交易聚合 `96424c77…` 与 10-29 基线相同 |

## 10-30 结算检查点（对照 10-29 结算前基线）

完整采集见 [p0a-settlement-post-20261030.json](p0a-settlement-post-20261030.json)（19:02:34），
逐区块对比见 [settlement-vs-baseline.json](p0a-pause-20261030/settlement-vs-baseline.json)。

| 区块 | 基线 → 结算后 | 说明 |
| --- | --- | --- |
| 主库 holdings | 22 → 22，聚合相同 | 真实持仓未被模拟写入或反推 |
| 主库 transactions | 11 → 11，聚合相同 | 同上 |
| 主库 data_source_runs | 5503 → 5773 | 采集 timer 与估值页刷新的追加记录，属预期 |
| accounts | 仅 `version` 1→3 | 暂停与恢复各加 1，状态回到 active |
| strategy_versions、rule_versions、runs、run_items | 不变 | 无新周期运行 |
| orders | `ord_6f34…` 转为 settled | 字段变化见 D5；`ord_3560…` 不变 |
| order_events / lots / ledger_events | 7→8 / 1→2 / 4→5 | 各新增一条确认记录 |
| snapshots | 11 → 12 | 新增 10-29 净值 2.1662（eastmoney lsjz，19:00 观察） |
| idempotency_keys | 1 → 3 | pause 与 resume 各一条 |
| account_operations、dividends | 0 → 0 | 无 |

## 偏差与说明

- D1 原定 17:20 采集，用户 17:34 已先完成暂停；以 16:37:48 的同口径检查点作 D1。16:37 至暂停前
  生产库只有采集记录追加，D2 检查点中暂停之外的所有区块与基线聚合相同，结论不受影响。
- 方案 D5 的「日终估值快照」在 23:30 生成，晚于本次恢复时刻；暂停期间改为观察 5 分钟一次的观察快照持续写入
  （257→261）。隔离测试已覆盖暂停账户的日终快照。
- 设备记录仍缺 Via 浏览器版本号。

## 验收后发布

D7 通过后于 19:05:26 发布 `20261029-fund-profiles-1`（后端档案批次），记录见
[发布预备与发布记录](fund-profiles-release-20261029.md)；发布后账户 active/版本 3、两笔订单 settled、
`verify` ok，主库持仓与交易聚合仍与基线相同。

## P0-A 剩余项

撤销会话实测（第 7 节 C3）与 Access cookie SameSite 实测值、24 小时会话到期观察（C4，非阻塞）。
