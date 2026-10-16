# 持仓录入 2.0 方案

状态：阶段 3 已完成（2026-09-04）。本文记录已实现契约和验收结果；阶段 4 已完成。

## 1. 文档用途

本文件约定手机端持仓录入的产品流程、隐私边界、数据契约、失败语义和验收标准。
阶段 3 只按本文实施截图识别 MVP，不进入 AI 分析或组合推荐。

完成结果：`/import` 已切换为截图录入；识别、核对、候选修正、合并或替换预览、原子写入和
提交后估值刷新均已实现。Web CSV 接口已移除，CLI 入口保留。后端 162 项测试及前端 route check、
typecheck、lint、build 已通过；390×844 和 1440×900 已完成实际流程，430×932 与
768×1024 已检查无横向溢出。未使用真实用户持仓截图；真机相机、软键盘和安全区仍是剩余风险。

参考仓库 `500tpig/FundFlowLite`、`500tpig/FundPilot-AI` 仅用于理解「识别 → 人工确认 →
写入」流程。两者当前没有 LICENSE，不复制实现；尤其不复制外部 VLM 默认静默发送、
失败返回空数组、`float` 金额运算、按盘中估值反推份额，以及逐条吞错写入。

## 2. 已确认决策

| 项 | 结论 |
| --- | --- |
| 外部看图模型 | 允许。用户配置后，后端把当次压缩截图发送到结构化识图服务。 |
| 未配置时 | 不发送任何图片。识别入口返回明确错误，页面不可进入核对。 |
| 本地 OCR | 阶段 3 不做。不引入 PaddleOCR / RapidOCR / DeepSeek-OCR 双路径。 |
| 金额主键 | `amount` 是持仓规模和收益计算的唯一权威字段。 |
| 份额 | 可选。只保存截图或人工确认得到的真实份额，不作为第二套计算口径，不反推补齐。 |
| 写入默认 | 合并更新。截图中未出现的持仓一律保留。 |
| 替换全部 | 必须列出待删除项，并单独确认后才允许删除。 |
| 图片存储 | 只用于当次识别。不写入 SQLite，不写日志原文，请求结束后删除临时文件。 |
| 识别结果 | 不得直接入库。必须经过候选修正和预览确认。 |
| 页面路径 | 仍使用 `/import`。导航文案改为「录入」。 |
| CSV | 主界面移除。CLI `fund-analysis import` 暂时保留，语义仍是整表替换。 |

## 3. 录入方式

### 3.1 推荐主路径

手机端批量录入的主路径是持仓列表截图，不是单只手工表单：

```text
相册多选 / 拍照 / 粘贴截图
        ↓
后端压缩后调用已配置的看图模型
        ↓
候选持仓（代码、名称、金额、可选份额、问题标记）
        ↓
按基金代码核验正式名称和 A/C 类别
        ↓
人工修正或删除候选
        ↓
预览新增、更新、保留；替换模式下另列删除
        ↓
单事务写入，再刷新估值
```

依据：当前持仓以金额为估值输入；支付宝等列表截图的主数字也是持仓金额；
现有样本大约十几到几十只，逐只填写不适合作为批量入口。

### 3.2 不采用「代码 + 份额」作为批量主键

他人产品的新增表单以持有份额为主，代码可填 `000001` / `510300` / `SPY`，并推断类型、
市场、币种。该形态适合单只手工建仓，不作为本产品的批量录入方案。

| 能力 | 本产品 | 他人单只表单 |
| --- | --- | --- |
| 批量来源 | 持仓列表截图 | 无 |
| 数量主键 | 持仓金额 | 持有份额 |
| 份额 | 可选保存，不参与默认估算 | 必填主键 |
| 代码范围 | 6 位公募基金代码 | 股票 / ETF / 海外代码 |
| 名称 | 检索正式名称后写入 | 可留空自动带出 |
| 成本 | 可选，截图有则候选 | 表单字段 |
| 买入日期、备注 | 不新增 | 有 |
| 估值计算 | `持仓金额 × 盘中估算涨跌幅` | 份额 × 估值净值 |

本产品已经具备单只手工入口：`/holdings` 输入 6 位代码后核对正式名称，再填金额和成本。
阶段 3 不把该表单改成份额主键，也不为截图流程增加买入日期、备注或币种字段。
截图确认页可增加非必填「持有份额」：识别清楚则确认，缺失或截断则留空。

可准确推断的字段仅限：

- 6 位代码对应的东财正式名称
- 正式名称末尾的 A / C 等类别
- 与现有持仓代码匹配后的新增 / 更新 / 保留 / 删除

不得推断的字段：

- 份额（禁止 `amount / estimated_nav`；也禁止把 `amount / official_nav` 写成已确认份额）
- 成本（禁止 `金额 - 持有收益`）
- 组合占比（局部截图会得到错误占比）
- 截图未出现的持仓应被删除

### 3.3 金额为主、份额可选

改成份额主字段不会天然提高盘中涨跌估算精度。只有份额真实可靠、而金额已经过期时，
`shares × estimated_nav` 才可能让盘中组合市值更接近当前估值。

设 `A` 为上一官方净值时点的持仓金额，`q` 为真实份额，`N0` 为上一官方净值，
`Ne` 为盘中估值，`r = (Ne - N0) / N0`。若 `A = q × N0`，则：

```text
A × r = q × (Ne - N0)
A × (1 + r) = q × Ne
```

因此：

- 盘中估算涨跌：两种公式在基准一致时等价。是否准确仍取决于第三方 `estimated_nav` 或 `estimated_return_pct`。
- 盘中组合市值：可靠份额可算 `q × Ne`；金额口径用 `A + A × r` 得到相同结果。只有金额过期且份额可靠时，份额口径才有实际优势。
- 若界面只显示固定 `A`、没有加上估算收益，显示的是持仓金额快照，不是盘中组合市值。

更多小数位不等于更准确。估值源本身有偏差时，用份额只会更精确地算出这个有偏差的值。

份额来源：

| 来源 | 判断 | 边界 |
| --- | --- | --- |
| 从持仓页抄录后手工输入 | 可接受 | 须经确认；不得凭记忆或自行反推 |
| OCR 读到完整「持有份额」并人工确认 | 可接受 | 截断、单位不明或不确定时留空 |
| `amount / official_nav` | 不可作为已确认份额 | 日期一致时只是近似；金额按分取整和净值精度会造成不可逆损失 |
| `amount / estimated_nav` | 否决 | 分母不是官方净值或成交价；会把盘中偏差固化进份额并放大 |

`cost` 只用于持有收益 `amount - cost`。它不是盘中估值输入，也不能替代金额或份额作为规模主字段。

固定 `amount` 的误差：

- 日内不修改 `amount` 是正确的。它是当日计算基准，不随每次估值刷新写回。
- 以 `amount = 10000`、`r = 2%` 为例：估算收益 `200` 元。若把 `10000` 直接标成盘中市值，误差 `200` 元，相对误差约 `1.96%`，与当日涨跌幅同一数量级。
- 用 `amount + estimated_daily_profit` 显示盘中市值，这一阶误差已消除，不需要份额。
- 若先把金额改成 `10200` 再乘完整日涨幅 `2%`，会多算 `4` 元，约合 `r² = 0.04%`。
- 金额跨日过期、相对真实基准偏差为 `ε` 时，估算收益和估算市值的相对误差约为 `ε`，可能远大于当日涨跌幅。根因是基准过期，不是日内没有随估值改写 `amount`。

权威关系：

1. `amount` 仍是必填且唯一的持仓规模主字段。
2. 盘中估算收益继续使用 `amount × estimated_return_pct / 100`。
3. 若展示盘中组合估算市值，使用 `amount + estimated_daily_profit`，且 `amount` 须对应当日涨跌幅的基准时点。
4. 官方当日收益继续锁定第一次写入时的 `holding_amount`，不因后续金额或可选份额变化而重算。
5. `cost` 只用于持有收益。
6. `shares` 仅在页面明确提供并经人工确认时保存；阶段 3 不用它覆盖金额口径，也不按有无份额切换同一指标的公式。

阶段 3 不修改估值计算公式。估值页若把固定 `amount` 标成「盘中市值」，属于展示口径，不在截图录入 MVP 内改公式。

## 4. 隐私与部署

### 4.1 发送边界

- 浏览器只把图片交给本机后端，不直连外部模型。
- 仅当结构化识图环境变量完整时，后端才发送当次压缩 JPEG。
- 发送内容限于压缩后的截图和固定解析提示。不发送持仓数据库、历史净值或 API 密钥到前端。
- 页面在选择图片前展示固定说明：「识别会把当次截图发送到已配置的识图服务，图片不会保存。」

### 4.2 配置

沿用当前约定：应用不自动读取 `.env`，由进程环境变量提供配置。

| 变量 | 用途 |
| --- | --- |
| `FUND_ANALYSIS_OCR_STRUCTURED_BASE_URL` | OpenAI 兼容 `/v1` 根路径，或完整 `/chat/completions` |
| `FUND_ANALYSIS_OCR_STRUCTURED_API_KEY` | 识图服务密钥 |
| `FUND_ANALYSIS_OCR_STRUCTURED_MODEL` | 必须支持图片输入的看图模型 |
| `FUND_ANALYSIS_OCR_STRUCTURED_TIMEOUT` | 单张超时秒数，默认 `180` |
| `FUND_ANALYSIS_OCR_STRUCTURED_MAX_TOKENS` | 默认 `4096` |

禁止用 `FUND_ANALYSIS_OCR_MODEL=deepseek-ai/DeepSeek-OCR` 做结构化持仓解析。
该变量若存在，只表示历史文本 OCR 配置，阶段 3 不读取。
`FUND_ANALYSIS_LLM_*` 留给 roadmap 中的 AI 阶段，本阶段不接入。

未配置判定：`BASE_URL`、`API_KEY`、`MODEL` 任一为空即视为未配置。

### 4.3 日志与密钥

- 禁止把图片 base64、data URL 或压缩后的字节写入日志。
- 允许记录图片张数、MIME、压缩前后字节数、模型名、耗时、候选条数和错误码。
- 密钥只从环境变量读取，不写入仓库、SQLite 或前端包。

## 5. 图片输入范围

### 5.1 支持

- 相册多选，最多 5 张。
- 手机拍照，单张；与多选相册分成两个入口，避免部分浏览器 `capture` 与 `multiple` 冲突。
- 桌面端剪贴板粘贴 `image/*`。iOS Safari 粘贴图片不稳定，不作为手机主入口。
- 格式：`image/jpeg`、`image/png`、`image/webp`。
- 扩展名 `.jpg` / `.jpeg` / `.png` / `.webp` 与 MIME 不一致时，以文件头嗅探为准。

### 5.2 不支持

- PDF、CSV、HEIC / HEIF、GIF、BMP、视频。
- 浏览器直接把图片发到外部模型。
- 服务端会话缓存识别结果。候选只存在于当前页面内存，刷新后需重新识别。

HEIC 返回 `415`，文案：「当前不支持 HEIC，请使用 JPG 或 PNG。」

### 5.3 大小与压缩

| 限制 | 值 |
| --- | --- |
| 单张原始大小 | 不超过 8 MB |
| 单次张数 | 1 到 5 |
| 压缩长边 | 1600 px |
| 压缩格式 | JPEG quality 80 |
| 发给模型的单张 | 压缩后不超过 2 MB，否则该张失败 |

压缩在后端进行。前端不强制预压缩；可选缩小以加快上传，但以后端校验为准。

### 5.4 生命周期

1. 接收 multipart 后写入系统临时文件。
2. 嗅探类型、校验大小、压缩。
3. 调用识图服务。
4. `finally` 删除全部临时文件，包括压缩产物。
5. 不写入 `data/`、SQLite 或 `frontend/dist`。

## 6. 候选字段与标记

### 6.1 候选对象

金额类字段在 JSON 中使用十进制字符串，保留到分。服务端用 `Decimal` 解析，
量化到 `0.01` 后再写入现有 `holdings.amount`。不在业务层用二进制浮点做金额加减。
现有 `amount` 列仍是 SQLite `REAL`，阶段 3 不迁移列类型。

可选 `shares` 用十进制字符串，最多 4 位小数，禁止 `REAL` 和 IEEE float。
新增可空列 `holdings.shares TEXT`，不回填历史行。未识别到份额时写入 `NULL`，不根据金额反推。

| 字段 | 必填（确认写入时） | 来源 |
| --- | --- | --- |
| `code` | 是，6 位数字 | 截图或名称检索补全 |
| `name` | 是 | 优先正式名称 |
| `amount` | 是，`>= 0` | 截图持仓金额 |
| `shares` | 否 | 截图完整「持有份额」或人工抄录 |
| `cost` | 否 | 截图有则保留 |
| `holding_profit_rate` | 否 | 截图有则保留 |
| `daily_profit` | 否 | 截图有则保留；无日期，不作为今日收益 |
| `cumulative_profit` | 否 | 截图有则保留 |
| `amount_nav_date` | 否 | 不从截图推断。持仓页可手工填写金额对应的净值日期；改金额未同时确认则旧日期失效 |
| `ocr_name` | 否 | 模型原始名称，供对照 |
| `share_class` | 否 | 从正式名称解析 |
| `issues` | 是，可为 `[]` | 见下表 |
| `verification` | 是 | 见核验状态 |
| `confidence` | 是 | `high` / `low` |
| `source_image_index` | 是，从 0 起 | 来源图片 |

阶段 3 不从截图写入 `weight_pct`、`theme`、`asset_type`、`asset_class` 或备注。

金额为 `0` 允许人工确认后写入（清仓仍列示的情况）。金额缺失则该行不能进入确认。
份额缺失、截断或不确定时留空，不阻止该行确认。

### 6.2 `issues`

| 值 | 含义 | 确认前是否必须处理 |
| --- | --- | --- |
| `code_missing` | 无 6 位代码 | 必须补代码或删除该行 |
| `name_missing` | 无名称 | 必须补名称或删除该行 |
| `amount_missing` | 无金额 | 必须补金额或删除该行 |
| `amount_conflict` | 多张图同一代码金额不一致 | 必须留下一行 |
| `shares_unclear` | 份额截断、单位不明或无法解析 | 份额留空，可继续 |
| `shares_conflict` | 多张图同一代码份额不一致 | 份额留空，可继续 |
| `name_mismatch` | 截图名称与正式名称明显不同 | 可继续，默认采用正式名称 |
| `share_class_mismatch` | 截图类别与该代码正式名称不一致 | 必须改代码或删除 |
| `share_class_ambiguous` | 名称对应多只 A/C | 必须选定代码 |
| `unverified` | 基金检索失败 | 可继续，但需已有名称 |
| `not_found` | 代码未检索到基金 | 可继续，但需已有名称 |
| `skipped_non_fund` | 非 6 位公募基金 | 该行不能确认，应删除 |

`confidence` 为 `low` 的条件：`issues` 非空，或代码 / 金额曾被补全。页面用「需核对」标记，
不阻止修正后确认。

### 6.3 多图合并

按图片顺序依次识别，再按代码合并：

- 同一代码、金额相差小于 `0.01`：保留一条，`source_image_index` 取首次。
- 同一代码、金额相差不小于 `0.01`：只保留一条展示行，金额留空，加入 `amount_conflict`，
  并在警告中列出各图金额。
- 同一代码、金额可合并但份额不一致：金额按上条规则保留，份额留空，加入 `shares_conflict`。
- 无代码但名称相同：不自动合并，留待核验。
- 某张失败、其余成功：HTTP 200，`warnings` 指出失败图片序号；不是空数组。
- 全部失败：`502`。
- 全部成功但 0 条候选：`422`。

## 7. 代码、名称与 A/C 核验

核验发生在识别完成后、返回候选之前，复用现有 `search_public_funds`。
不在确认写入时再次依赖东财检索，避免检索失败阻断已确认写入。

### 7.1 代码规则

- 只接受 6 位数字。去掉空格和其它字符后长度不是 6，则视为无代码。
- `000000` 视为无效。
- 现金、余额宝、股票代码、海外 ticker 不进入可确认集合。

### 7.2 有代码

1. 用该代码检索。
2. 命中则 `official_name` 覆盖待写入 `name`，`ocr_name` 保留原文，`verification=matched`。
3. 未命中则 `verification=not_found`，保留 OCR 名称，加入 `not_found`。
4. 检索失败则 `verification=unverified`，加入 `unverified`，不丢弃候选。

### 7.3 无代码但有名称

1. 用 OCR 名称检索，最多 5 条。
2. 仅 1 条 6 位命中：补代码，`verification=inferred`，`confidence=low`。
3. 多条命中：不自动挑选，加入 `share_class_ambiguous`，附 `name_matches`。
4. 0 条：保持无代码，加入 `code_missing`。

禁止用模糊相似度自动挑一只基金。参考仓库中的名称打分补全不采用。

### 7.4 A / C 类别

从正式名称末尾识别 `A` / `C` / `B` / `E` / `H`（允许半角或全角括号包裹）。

- 截图名称类别与正式名称一致：不标问题。
- 截图为 C、该代码正式名称为 A：`share_class_mismatch`。不得改成「同名另一份额」代码。
- 名称检索同时出现 A 和 C：`share_class_ambiguous`，页面列出候选代码供点选。

`/holdings` 现有单只名称核验保持不变。截图流程不得在检索失败时清空用户已填名称。

## 8. 合并更新与替换全部

### 8.1 合并更新（默认）

以基金代码大小写不敏感匹配现有持仓：

| 情况 | `action` | 写入 |
| --- | --- | --- |
| 现有无此代码 | `add` | 插入 |
| 代码存在且金额、名称、成本等确认字段相同 | `keep` | 不改 |
| 代码存在且字段不同 | `update` | 更新见下表 |
| 现有有、截图无 | 不出现在差异里 | 保留 |

更新字段：

- 必写：`name`、`amount`
- 确认体提供且非 `null` 才写：`shares`、`cost`、`holding_profit_rate`、`daily_profit`、`cumulative_profit`、`amount_nav_date`
- 未提供的可选字段保持原值，与当前持仓编辑不覆盖导入记账字段的行为一致；但金额变化且未同时提供 `amount_nav_date` 时，清空已有金额基准日期
- 截图识别不推断 `amount_nav_date`，也不把今天当作默认净值日
- 该行已存储的 `weight_pct` 置空，展示层按最新金额重算占比
- 不改 `theme`、`asset_type`、`asset_class`
- 有 `shares` 时不得用它改写 `amount`，也不得按 `shares ×` 净值重算金额

### 8.2 替换全部

替换会删除截图确认集合中未出现的现有持仓。规则：

1. 默认选项仍是「合并更新」。
2. 选择「替换全部」后，预览必须单独列出待删除行，包含代码、名称、金额。
3. 待删除数为 0 时，确认体 `confirm_remove` 可为 `false`。
4. 待删除数大于 0 时，确认体必须同时满足：`mode=replace`、`confirm_remove=true`、
   `remove_codes` 与服务端计算集合完全一致（忽略顺序，代码规范化后比较）。
5. 集合不一致返回 `409`，不写入。
6. 页面确认按钮文案为「确认删除 N 只并替换持仓」，不能与合并确认共用「确认导入」。

CLI CSV 仍是整表替换，不经过本页的删除确认 UI。

## 9. API 契约

阶段 3 新增三个接口，下线 Web CSV 接口。OpenAPI 测试需同步更新。

| 方法 | 路径 | 作用 | 是否写库 |
| --- | --- | --- | --- |
| `POST` | `/api/holdings/entry/recognize` | 识图并核验候选 | 否 |
| `POST` | `/api/holdings/entry/preview` | 按确认稿计算差异 | 否 |
| `POST` | `/api/holdings/entry/confirm` | 事务写入 | 是 |
| 下线 | `/api/import`、`/api/import/preview` | 原 CSV Web 入口 | — |

错误体继续使用 FastAPI `{ "detail": "中文说明" }`，便于现有 `requestJson` 直接展示。
`detail` 必须是稳定、可断言的中文字符串，禁止把模型原始输出或堆栈返回给页面。

### 9.1 识别

```http
POST /api/holdings/entry/recognize
Content-Type: multipart/form-data
```

字段名 `images`，重复提交 1 到 5 个文件。不要用 JSON base64 作为主契约。

成功示例：

```json
{
  "schema_version": "holdings_entry_recognize.v1",
  "provider": "structured_vlm",
  "model": "example-vlm",
  "image_count": 2,
  "candidates": [
    {
      "source_image_index": 0,
      "code": "021740",
      "ocr_name": "前海开源黄金ETF联接C",
      "name": "前海开源黄金ETF联接C",
      "share_class": "C",
      "amount": "15402.47",
      "shares": "12345.67",
      "cost": "15994.67",
      "holding_profit_rate": "-3.70",
      "daily_profit": null,
      "cumulative_profit": "-592.20",
      "issues": [],
      "verification": "matched",
      "confidence": "high",
      "name_matches": []
    }
  ],
  "warnings": []
}
```

识别失败语义：

| 条件 | HTTP | `detail` |
| --- | --- | --- |
| 未配置识图服务 | 503 | `未配置持仓识图服务` |
| 未上传图片 | 400 | `请选择 1 到 5 张持仓截图` |
| 超过 5 张 | 400 | `一次最多识别 5 张截图` |
| 单张超过 8 MB | 413 | `单张图片不能超过 8 MB` |
| MIME / 文件头不支持 | 415 | `仅支持 JPG、PNG 或 WebP 截图` |
| HEIC | 415 | `当前不支持 HEIC，请使用 JPG 或 PNG` |
| 压缩后仍超过 2 MB | 413 | `截图压缩后仍然过大，请裁剪后重试` |
| 全部图片调用失败 | 502 | `持仓识图服务失败` |
| 全部超时 | 504 | `持仓识图超时` |
| 模型返回无法解析 | 502 | `持仓识图结果无法解析` |
| 成功但 0 条候选 | 422 | `未识别到基金持仓` |

禁止：配置缺失或调用失败时返回 `200` 且 `candidates: []`。

模型输出不是事实。服务端必须把模型 JSON 映射到候选对象，丢掉未知键，补 `issues`，
再做代码核验。模型直接给出的代码若不是 6 位数字，按无代码处理。

### 9.2 预览

```http
POST /api/holdings/entry/preview
Content-Type: application/json
```

```json
{
  "mode": "merge",
  "items": [
    {
      "code": "021740",
      "name": "前海开源黄金ETF联接C",
      "amount": "15402.47",
      "shares": "12345.67",
      "cost": "15994.67",
      "holding_profit_rate": "-3.70",
      "daily_profit": null,
      "cumulative_profit": "-592.20"
    }
  ]
}
```

`mode` 只接受 `merge` 或 `replace`。预览不得写库。
`shares` 可省略或为 `null`；有值时必须是可解析的非负十进制，不得为服务端反推结果。

成功示例：

```json
{
  "schema_version": "holdings_entry_preview.v1",
  "mode": "merge",
  "rows": [
    {
      "code": "021740",
      "name": "前海开源黄金ETF联接C",
      "amount": "15402.47",
      "shares": "12345.67",
      "cost": "15994.67",
      "action": "update",
      "error": null
    }
  ],
  "warnings": [],
  "valid_count": 1,
  "error_count": 0,
  "add_count": 0,
  "update_count": 1,
  "keep_count": 0,
  "remove_count": 0
}
```

`replace` 时，待删除行 `action` 为 `remove`，`remove_count` 为这些行的数量。
`merge` 时不得出现 `remove` 行，`remove_count` 恒为 0。

预览失败语义：

| 条件 | HTTP | `detail` |
| --- | --- | --- |
| `mode` 非法 | 400 | `mode 只接受 merge 或 replace` |
| `items` 为空 | 400 | `请至少确认 1 条持仓` |
| 代码重复 | 400 | `基金代码 021740 在确认列表中重复` |
| 代码不是 6 位 | 400 | `基金代码 02174 不是 6 位数字` |
| 名称为空 | 400 | `基金代码 021740 缺少名称` |
| 金额缺失或无法解析 | 400 | `基金代码 021740 缺少有效金额` |
| 金额为负 | 400 | `基金代码 021740 的金额不能为负` |
| `shares` 无法解析或为负 | 400 | `基金代码 021740 的份额无效` |

预览只接收确认稿字段，不接收 `issues`。预览 / 确认不再自动查东财，但代码格式、
名称和金额必须有效。`share_class_mismatch` 与 `share_class_ambiguous` 在识别后的页面拦截：
未改代码或未选定代码时，前端禁止进入预览。后端以代码、名称、金额校验为准，
不重复实现全部 issue 枚举。

### 9.3 确认写入

```http
POST /api/holdings/entry/confirm
Content-Type: application/json
```

```json
{
  "mode": "merge",
  "confirm_remove": false,
  "remove_codes": [],
  "items": [
    {
      "code": "021740",
      "name": "前海开源黄金ETF联接C",
      "amount": "15402.47",
      "shares": "12345.67",
      "cost": "15994.67",
      "holding_profit_rate": "-3.70",
      "daily_profit": null,
      "cumulative_profit": "-592.20"
    }
  ]
}
```

替换且待删除大于 0：

```json
{
  "mode": "replace",
  "confirm_remove": true,
  "remove_codes": ["018125"],
  "items": [
    {
      "code": "021740",
      "name": "前海开源黄金ETF联接C",
      "amount": "15402.47",
      "shares": null,
      "cost": null,
      "holding_profit_rate": null,
      "daily_profit": null,
      "cumulative_profit": null
    }
  ]
}
```

成功示例：

```json
{
  "schema_version": "holdings_entry_confirm.v1",
  "mode": "merge",
  "holdings_written": 1,
  "add_count": 0,
  "update_count": 1,
  "keep_count": 0,
  "remove_count": 0,
  "message": "已合并更新 1 条持仓",
  "refresh": {
    "attempted": true,
    "has_failure": false,
    "message": "官方净值成功 1 只、失败 0 只；盘中估算成功 1 只、失败 0 只；市场行情成功 3 项、失败 0 项。"
  }
}
```

写入失败语义：

| 条件 | HTTP | `detail` | 数据库 |
| --- | --- | --- | --- |
| 校验失败 | 与预览相同 | 与预览相同 | 不变 |
| `replace` 且待删除 `> 0` 但 `confirm_remove` 不为 `true` | 400 | `替换全部需要确认待删除持仓` | 不变 |
| `remove_codes` 与服务端集合不一致 | 409 | `待删除持仓与当前数据不一致，请重新预览` | 不变 |
| 写入中异常 | 500 | `持仓写入失败，已回滚` | 不变 |

`refresh` 在事务提交之后执行。刷新失败不回滚持仓，`refresh.has_failure=true`，
`refresh.message` 使用现有 `/api/funds/refresh` 口径。

## 10. 事务边界

新增 `LocalStorage.apply_holdings_entry(items, mode)`，在同一个 `with conn:` 中完成：

1. 规范化代码，拒绝批次内重复。
2. `merge`：按代码更新或插入；不删除其它行。
3. `replace`：写入确认集合后，删除不在集合中的行。
4. 任一步异常则整笔回滚。
5. 成功后返回计数。调用方再刷新估值。

不要逐条 `try/except` 后继续写入。不要先删后插却分多个连接提交。
`replace_holdings` 仍供 CLI CSV 使用，Web 截图路径不调用它，以免未确认删除。

不新增表，不存识别会话，不存图片哈希。允许给 `holdings` 增加可空 `shares TEXT` 列，
与现有 `_ensure_holding_columns` 的加列方式一致。`GET /api/holdings` 可原样返回该字段；
估值汇总和估算收益不读取它。

## 11. 页面状态

路径保持 `/import`。`routes.ts` 的 `label` / `title` / `breadcrumb` 改为「录入」 /
「持仓录入」 / 「录入」。`check-routes.mjs` 的导航文案断言同步修改。
`section` 与 `name` 仍为 `import`，避免扩大路由重构。

底部导航第三个入口改为「录入」。桌面 rail 同步。

### 11.1 状态机

| 状态 | 页面 | 主操作 |
| --- | --- | --- |
| `idle` | 选择截图 | 相册、拍照、粘贴 |
| `recognizing` | 上传中 | 不可重复提交 |
| `recognize_failed` | 错误 + 原入口 | 重试 |
| `candidates` | 候选列表 | 改代码 / 名称 / 金额 / 可选份额，删除行，点选 A/C |
| `previewing` | 计算差异 | 不可点确认 |
| `preview` | 新增 / 更新 / 保留 | 默认合并；可选替换 |
| `replace_confirm` | 待删除清单 | 明确确认删除 |
| `writing` | 写入中 | 按钮 loading |
| `write_failed` | 预览仍在 | 显示错误，数据未改 |
| `done` | 完成摘要 | 去估值页；刷新失败单独显示 |

### 11.2 移动端约束

延续 `docs/mobile-responsive-plan.md` 的断点、安全区和 44×44 px 触控要求。

- 步骤纵向排列：选择 → 核对 → 预览 → 完成。
- 相册 / 拍照按钮在窄屏占满可用宽度。
- 候选按卡片纵向编辑，代码、名称、金额、可选份额、成本依次排列。
- 份额输入为非必填；空值不阻止预览。
- 「需核对」行使用警告色标记，不依赖 hover。
- 预览在手机上优先显示代码、名称、金额、动作；完整对照可局部横滑。
- 替换确认是独立一步，待删除项默认展开。
- 识别中、写入中禁用返回上一步之外的破坏性按钮；允许取消后回到 `idle` 并丢弃候选。
- 软键盘打开时，当前编辑字段和主按钮仍需可见。真机键盘遮挡与阶段 1 相同，记为剩余风险。

### 11.3 界面文案

| 位置 | 文案 |
| --- | --- |
| 导航 | 录入 |
| 页题 | 持仓录入 |
| 相册 | 从相册选择 |
| 拍照 | 拍照 |
| 空态说明 | 识别会把当次截图发送到已配置的识图服务，图片不会保存。 |
| 候选主按钮 | 预览变更 |
| 合并确认 | 确认合并 |
| 替换确认 | 确认删除 N 只并替换持仓 |
| 完成后 | 查看估值 |

## 12. CSV 与 CLI

阶段 3 从 Vue 主界面移除 CSV 选择、模板下载和 `/api/import*` 调用。
`ImportView.vue` 改为截图流程，不保留 CSV 双入口。

CLI 保持：

```bash
fund-analysis import --db-path data/fund_analysis.db --portfolio examples/portfolio.csv
```

行为不变：解析成功后整表替换；解析失败不改现有持仓。
截图流程稳定前不删除该命令。README 在阶段 3 落地后改为：主界面用截图录入，
CSV 仅 CLI 迁移。

Web 下线 `/api/import` 与 `/api/import/preview` 后，原测试改为断言 404，
或从 OpenAPI 白名单移除。不要留无页面的 CSV 接口。

## 13. 阶段 3 实施范围

按以下顺序，仍在同一阶段对话内完成：

1. 后端配置读取、图片校验 / 压缩、识图客户端（可注入 HTTP，测试不打真实模型）。
2. 加列 `holdings.shares`，以及 `recognize` / `preview` / `confirm` 与 `apply_holdings_entry`。
3. 下线 Web CSV 接口，保留 CLI。
4. 改造 `/import` 页面与导航文案。
5. 自动化测试和浏览器验收。
6. 更新 README 与 roadmap 阶段 3 状态。完成后本文件可按文档维护规则删除，
   稳定事实迁入 README 和测试。

允许新增依赖：`Pillow`（压缩）。识图 HTTP 优先用标准库 `urllib`，与现有基金检索一致。
不新增 OpenAI / Gemini SDK，不把参考仓库代码拷进本仓库。

依赖注入：`create_app(..., holdings_recognizer=...)`，测试传入固定候选或受控错误。

## 14. 自动化测试

最少覆盖：

- 未配置识图服务：`503`，无出站 HTTP，数据库不变。
- 0 张、6 张、8 MB 以上、HEIC、PDF：对应 `400` / `413` / `415`。
- 识图 HTTP 失败：`502`，不是空候选。
- 识图超时：`504`。
- 模型返回非 JSON：`502`。
- 模型返回空 funds：`422`。
- 两张图同一代码不同金额：`amount_conflict`，金额为空。
- 一张失败一张成功：`200` + `warnings`，含成功候选。
- 有代码命中正式名称：`verification=matched`，写入名称为正式名称。
- 名称对应 A 和 C：`share_class_ambiguous`，不自动选代码。
- 截图 C、代码实为 A：`share_class_mismatch`。
- 预览不写库。
- 合并：新增、更新、未出现的持仓仍在。
- 更新金额时 `weight_pct` 被置空。
- 可选字段未提供时不覆盖已有 `daily_profit` 或已有 `shares`。
- 确认体无 `shares` 时写入成功，且不得出现 `amount / estimated_nav` 或 `amount / official_nav` 的补齐值。
- 确认体提供份额时原样保存，估值估算收益仍只使用 `amount`。
- 替换未确认：`400`，持仓不变。
- 替换 `remove_codes` 过期：`409`，持仓不变。
- 确认写入中途失败：回滚。
- 写入成功、刷新失败：持仓已改，`refresh.has_failure=true`。
- 批次重复代码：`400`。
- OpenAPI 不再包含 `/api/import`，包含三个 `entry` 路径。
- CLI import 仍整表替换。
- 前端 route check：三个一级入口，文案为「估值」「持仓」「录入」。

禁止测试依赖真实 VLM 或真实密钥。`.env` 中的密钥不得出现在测试、文档或日志断言里。

## 15. 浏览器验收

阶段 3 至少在 390×844 和 1440×900 走通。430×932 与 768×1024 检查布局不回退。

必须实际操作，不是只截静图：

1. 未配置识图时，选择图片后看到「未配置持仓识图服务」，持仓页数据不变。
2. 相册多选 2 张测试图，进入候选，能改金额、留空或填写份额、删除一行。
3. 桌面端把 PNG 粘进页面，可以识别（可用固定 mock）。
4. 低可信与 A/C 冲突行可见「需核对」；未处理前不能预览成功。
5. 默认合并：现有一只不在截图中的基金，确认后仍在。
6. 替换：先列出待删除，未确认不能写入；确认后该基金消失。
7. 识别失败、部分图片失败、写入失败都能看到错误，且失败路径不改持仓。
8. 成功后估值页金额与新持仓一致；刷新失败时持仓仍已保存并有失败说明。
9. 底部导航显示「录入」，CSV 模板和文件选择不再出现。
10. 主要按钮不小于 44×44 px，无非预期横滑，正文不被底栏挡住。

测试图放 `tests/fixtures/holdings_entry/`，用无真实账户信息的构造图或脱敏图。
不要提交真实支付宝截图到 git。

## 16. 阶段 3 明确不做

- 本地 OCR 引擎、双路径 `auto` 模式。
- 把 `shares` 改为必填，或按有无份额切换估算公式。
- 用 `amount / estimated_nav` 或 `amount / official_nav` 生成份额。
- 每次盘中刷新后把估算市值写回 `amount`。
- 识别结果直接入库。
- 默认替换全部。
- 图片或识别会话落库。
- 恢复 CSV 主界面。
- 新增一级导航或把录入并入持仓页。
- 改造 `/holdings` 单只表单为份额主键。
- 接入 `FUND_ANALYSIS_LLM_*`、AI 分析页、投资推荐。
- 复制参考仓库源码。

## 17. 交给阶段 3 的完成标准

- 手机上可完成：选图 → 候选修正 → 差异确认 → 原子写入 → 估值刷新。
- 识别失败、部分缺失、A/C 冲突、写入失败均有可断言结果。
- 未配置识图服务时不会外发图片。
- CSV 只留 CLI。
- README、roadmap、测试与页面口径一致。
