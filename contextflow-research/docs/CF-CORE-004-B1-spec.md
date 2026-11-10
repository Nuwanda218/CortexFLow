# CF-CORE-004-B1：Parser 作业基础设施开发规格（已批准实现基线）

日期：2026-11-10。源码基线：`76afa1c`。关联勘察：`../reports/CF-CORE-004-B-investigation.md`。

**状态：已作为 B1 实现基线获授权。** 本文给出 B1 的具体规格；不表示本文中的数据库表、Worker 或测试已经实现。B1 通过内部测试后可按用户授权连续进入 B2，无需中间人工验收；若实现中需要改变本规格的可靠性语义、迁移用户数据库或扩大到阶段 C，必须停止并升级说明。

## Supervisor Summary

B1 定位为：不接入真实 LLM，使用真实 SQLite Repository 与可替换 Fake Parser，建立可恢复的消息解析作业闭环。

定稿建议：

- 同一 SQLite 文件升级到 v2，新增单表 `message_parse_jobs`，每个 Message 最多一条作业及一个正式结果。
- 内部状态统一为 `ready / processing / retry_wait / completed`；completed 表示作业结束，不等于解析成功或任务完成。
- 保留阶段 0 的 Message、ParseResult、InstructionParser 和 AgentDirectory 形状；不新增平行目标目录接口。
- 使用 owner、claim_request_id、generation、数据库时间和未过期 Lease 保护所有领取权相关写入；B1 不做心跳。
- `attempt_count` 只计已提交的调用意图；每个 generation 最多授权一次 Parser 调用，达到预算后仍可领取并提交最终 failed。
- parsed 正式结果提交后 Message 保持 pending；unresolved/最终 failed 与 Message blocked 在同库同事务提交。
- 实际 v1 数据库迁移、真实 API、Git commit/push 都需要后续单独授权；Fake 运行使用明确指定的隔离数据库。

主要限制：B1 不证明目标真实存在或自然语言解析正确；Fake 结果同样会被冻结，不可把 Fake 演示数据直接交给未来生产 C。

## Technical Details

### 1. 范围与不变项

#### 1.1 B1 必须交付

| 工作包 | 内容 | 主要验收 |
|---|---|---|
| B1-01 | 共用 Schema 生命周期、显式 v1→v2、空库 v2 初始化 | 原消息及索引保留、原子性、重复迁移 |
| B1-02 | ParseJob、状态约束、惰性发现和正式结果编解码 | 每 Message 一行、完整结果、损坏拒绝 |
| B1-03 | 短事务领取、generation 和全部 owner 写入的 Fencing | 真实双连接竞争、迟到写入拒绝 |
| B1-04 | 固定 Lease、持久预算、有界重试及恢复 | 不续租、崩溃恢复、预算耗尽收敛 |
| B1-05 | 结果/作业终态/必要 Message 状态原子提交与读取 | parsed 保持 pending、失败原子 blocked |
| B1-06 | 真实 ParserWorker + FakeInstructionParser + 隔离 CLI/测试 | 同一生命周期可在 B2 替换 Parser |

#### 1.2 留给 B2/C/E 的内容

- B2：真实 LLM Provider/SDK、模型输出结构解析、目标目录加载与别名匹配、目标语义验证。
- C：AgentId 解析、整批 Task/Delivery 准入、派发和消费判断。
- E：完整 Core 组装、Bridge 单连接多路复用及跨 C/D 的恢复推进。
- B1 不实现 Project/Role、管理 UI、人工 reparse、结果版本、尝试审计历史表、心跳或 Agent 发送。
- B1 不安装依赖，不访问外部 API，不改 Adapter/Bridge/Registry/Envelope v1。

#### 1.3 保持现有行为

- Receiver 只持久接收并立即确认，不等待 Parser。
- Message 的 `pending / processed / blocked` 枚举不变，messages 的现有 DDL及三个索引定义不变。
- ParseResult 不增加 MessageId、parse_id、consumed、AgentId 或 TaskId。
- InstructionParser 仍为 `parse(text, known_agents=None) -> ParseResult`，没有数据库职责。
- B1 的 WorkerOutcome 是内部执行结果，不能冒用只允许 processed/blocked 的阶段 0 ProcessResult。

### 2. 草案核对与名称统一

| 草案事项 | 核对结论与规格处理 |
|---|---|
| 单表或双表 | 采用单表。当前 ParseResult 无持久化接口/外键依赖，C 的只读访问也不要求独立结果表；无需双表 |
| 四种作业状态未完整列出 | 补全 `ready / processing / retry_wait / completed`；与勘察中的 running/finalized 仅为内部名称变化 |
| Claim 只返回 MessageId/generation/Lease | 补 owner 与 claim_request_id；调用前已知的 request_id 用于取消后的结果确认 |
| 只检查 MessageId/generation/Lease | 增加 owner、request_id、Supervisor kind、Message 仍 pending；不能用无条件 mark 更新 |
| attempt_count | 补 `last_attempt_generation` 与持久 `max_attempts`，防止重复登记及重启后配置改变隐式增加预算 |
| 无心跳 | Worker 截止时间 + 提交余量；超时不保证底层操作停止，数据库 Fence 独立生效 |
| B1 定义目标目录接口 | 复用已有 AgentDirectory.known_agents；真实目录延后，不新增重复接口 |
| 需要 C 持久读取 | B1 内部定义窄 Reader；不在本轮改变冻结公共导出，也不让 C 依赖表布局 |
| 预算耗尽恢复 | 允许只领取并终结，禁止再次登记 Parser 调用，而不是禁止一切领取 |

### 3. 数据模型：单表规格

表名使用 `message_parse_jobs`，与勘察保持一致。正式结果拆成同一行中的几个明确列，便于状态 CHECK；这不是双表，也不改变 ParseResult 的公共形状。

| 字段 | SQLite 类型/空值 | 语义 |
|---|---|---|
| message_id | TEXT PRIMARY KEY NOT NULL，FK | 指向 messages.message_id；删除使用默认 RESTRICT/NO ACTION，不级联丢弃结果 |
| job_state | TEXT NOT NULL | ready / processing / retry_wait / completed |
| claim_generation | INTEGER NOT NULL | 初始 0；每次新领取 +1，永不重置，不作为调用次数 |
| lease_owner | TEXT nullable | 每次 Worker 进程启动生成的唯一实例身份，不是可复用的主机名 |
| claim_request_id | TEXT nullable，非空时唯一 | 调用前生成的一次领取身份；只关联当前领取，不是永久历史键 |
| lease_expires_at_ms | INTEGER nullable | 数据库时钟下的 UTC epoch 毫秒 |
| attempt_count | INTEGER NOT NULL | 初始 0；已 COMMIT 的解析调用意图次数 |
| last_attempt_generation | INTEGER nullable | 最后获得调用授权的 generation；同 generation 不二次加预算或授权 |
| max_attempts | INTEGER NOT NULL | 创建作业时固定的总尝试上限，包含第一次；不由新 Worker 的配置覆盖 |
| next_attempt_at_ms | INTEGER nullable | retry_wait 到期时间，跨重启保留 |
| last_error_json | TEXT nullable | 最后一次临时故障的安全 ErrorInfo，不是正式 ParseResult |
| result_status | TEXT nullable | completed 时 parsed / unresolved / failed |
| instructions_json | TEXT nullable | completed 时有序 JSON 数组；失败时为 `[]`，非 completed 时为 NULL |
| result_error_json | TEXT nullable | completed 的 unresolved/failed 时 ErrorInfo；parsed 时 NULL |
| intent_fingerprint | TEXT nullable | parsed 时阶段 0 算法的 SHA-256，其他状态 NULL |
| created_at_ms | INTEGER NOT NULL | 创建时间，使用统一数据库时钟 |
| updated_at_ms | INTEGER NOT NULL | 最后写入时间；不以时间排序证明状态先后 |
| completed_at_ms | INTEGER nullable | 正式结果提交事务的时间戳，不是物理磁盘写完时刻 |

#### 3.1 单行约束

- 对整数列使用整数类型和范围检查，拒绝负数及浮点数；Python 参数拒绝 bool 冒充 int。
- `max_attempts >= 1`，`0 <= attempt_count <= max_attempts`，`attempt_count <= claim_generation`。
- attempt_count=0 时 last_attempt_generation 为 NULL；否则 `1 <= last_attempt_generation <= claim_generation`。
- owner/request 非空时须为有效非空文本；epoch 时间为非负整数。generation 不以时间戳替代。
- 初始 ready 必须 generation=0、attempt_count=0；其余非初始状态 generation>0。
- fingerprint 为 64 位小写十六进制；JSON 文本合法性与字段组合由内部 codec 再验证，不依赖额外 SQLite JSON 扩展。
- 不以 `updated_at >= created_at` 或 `completed_at >= updated_at` 保证安全：本机时钟回拨可能打破这些关系，Fencing 依赖 generation。

| job_state | owner/request/lease | next_attempt | 正式结果字段 | completed_at |
|---|---|---|---|---|
| ready | 全 NULL | NULL | 全 NULL | NULL |
| processing | 全非空、generation>0 | NULL | 全 NULL | NULL |
| retry_wait | 全 NULL | 非空 | 全 NULL | NULL |
| completed | 全 NULL | NULL | 必须满足下述 ParseResult 组合 | 非空 |

正式结果组合：

- parsed：instructions_json 解码为非空完整指令数组；error=NULL；fingerprint 非空且重算相同。
- unresolved/failed：instructions_json=`[]`；error 非空；fingerprint=NULL。
- last_error 与 result_error 分开。正式失败是否可再尝试不由 ErrorInfo.retryable 决定，completed 永远不自动重试。

索引：

- claim_request_id 的唯一索引，NULL 可以多行。
- `(job_state, next_attempt_at_ms, message_id)` 支持 ready/retry_wait。
- `(job_state, lease_expires_at_ms, message_id)` 支持到期接管。
- `(job_state, result_status, completed_at_ms, message_id)` 支持正式结果枚举。

#### 3.2 跨表不变量

lazy-create、claim、begin-attempt、retry 和 finalize 均在事务内核对：来源 Message 存在、kind=supervisor_instruction、processing_state=pending。

FK 只能证明存在，不能证明 kind 或处理前态。不能只依赖 Worker 先读到的 Message 快照。

现有 DTO/SQL 允许 pending 携带 processing_error，虽然阶段 A 接收不会产生它。B1 不偷偷收紧该契约；parsed 保持原 Message 状态及错误字段，不清除既有诊断。失败提交时原子改为 blocked，并设置本次正式错误。

若外部调用已有 mark API 使未完成作业的 Message 变为 processed/blocked，B1 不覆盖、不重新领取，报告前态不一致。B1 不修改阶段 A 被动 mark API 的既有转移语义，也不声称能阻止其他模块或直接 SQL 任意改库。

### 4. Schema v2 与迁移

#### 4.1 单一 Schema 所有者

新增窄内部 Schema 模块；SQLiteMessageStore 和 ParseJobRepository 共用它。将现有 v1 messages DDL/索引原样作为验证基线，禁止通过修改旧常量假装已有数据库是 v2。

| 打开路径 | 行为 |
|---|---|
| 真正空 v0 | 一次事务创建 messages、parse 表和索引，设置并验证 v2 |
| 已知 v1，普通 Store/Worker 启动 | 明确拒绝并提示需要显式迁移；不隐式升级 |
| 已知 v1，显式维护迁移入口 | 验证后原子 v1→v2 |
| 已知 v2 | 严格验证，不重复迁移；重复维护入口为已完成/no-op |
| 非空 v0、未知版本、owned Schema 损坏或保留表名冲突 | 拒绝，不能接管或覆盖 |

严格验证本模块拥有的表、CHECK、索引、谓词和触发器；保留阶段 A 对无关已有对象的边界，不擅自删除或要求重建其他表。

#### 4.2 迁移事务

1. 操作者停旧 Receiver/Worker，确认没有混跑进程，完成一致性备份。
2. 在持久 PRAGMA 或 DDL 变化前只读核对版本和 owned Schema。
3. BEGIN IMMEDIATE 后再查版本与定义；并发迁移者若已完成 v2则验证并复用。
4. 创建 parse 表/索引，不重写 messages 的任何行或既有幂等索引。
5. 设置 user_version=2、完整验证并 COMMIT。
6. 失败 rollback；失败前已有 v1须保留，空库不得留下半成品 Schema/version。

Schema DDL/version 的原子回滚不承诺恢复此前已成功设置的 journal_mode。若要求失败后连 journal 设置也完全不变，应把该 PRAGMA 放到独立已说明的步骤，不把“Schema 原子性”泛化为一切文件属性回滚。

迁移不批量生成 ParseJob；旧 pending 消息由正常惰性发现路径处理。

旧阶段 A 代码重开会拒绝 v2，但已经打开的连接不会自动再查 user_version。版本号不能替代停服务措施。WAL 数据库必须用 SQLite 一致性备份，不只复制主文件。

**授权边界：** 后续实现可在临时库测试维护入口；不等于允许迁移 `_artifacts/cfcore003-manual.sqlite3` 或环境默认数据库。正式迁移、备份覆盖和服务重启须分别说明目标与影响后获准。B1 不提供降级重建/删表回滚工具。

### 5. Repository 操作与事务语义

以下为 B1 内部接口语义，不增加阶段 0 公共 API。正常 SQLite 操作复用标准库、连接锁、asyncio.to_thread、WAL/FULL/foreign_keys 和安全错误转换；不引入 ORM、通用事务回调或新队列。

#### 5.1 惰性发现

`discover_pending(limit, max_attempts) -> 创建数量`

- 条件：Supervisor + pending + 不存在对应 ParseJob。
- 有界批量、确定顺序 `messages.received_at, message_id`；插入依赖 message_id PK。
- 已 completed 的 parsed Message 虽仍 pending，也因已有 job 而不重建。
- 发现和 claim 可在同一短事务实现，但不得绕过上述条件或重置预算。

#### 5.2 领取

`claim_next(owner, claim_request_id, lease_duration_ms) -> ParseClaim | None`

ParseClaim 至少包括 message_id、owner、claim_request_id、claim_generation、lease_expires_at_ms、attempt_count、max_attempts。

事务：BEGIN IMMEDIATE → 查相同当前 owner/request → 选择到期作业 → generation+1 → 写 owner/request/Lease、清 next_attempt → rowcount/读回验证 → COMMIT。

可选项：ready、已到 next_attempt 的 retry_wait、已到 lease_expires_at 的 processing；选择顺序确定，不保证公平调度或每个作业永久不饿死。

- Lease 未过期时，其他 Worker 不领取。
- 到期定义为 `lease_expires_at_ms <= database_now_ms`；有效条件为严格 `>`。
- **预算耗尽的到期作业仍可领取**，但只能终结，不能新调用 Parser。
- 同一当前 request 的明确重复返回同一 claim；不存在永久 claim 历史去重。

`find_claim(owner, claim_request_id) -> ParseClaim | None` 只查询，不新建领取。

#### 5.3 统一 Fence

owner 专属写入统一条件：

`message_id + processing + owner + claim_request_id + generation + 未过期 Lease + Message 仍为 pending Supervisor`

begin-attempt、retry、finalize 全部使用该条件。B1 不提供 renew；未来心跳若增加也必须使用相同 Fence。

时间在获得写锁后从 SQLite 取值；可使用本环境已支持的 `CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER)`。同一操作用一个数据库时间基准，不接受 Worker 传来的 now。SQLite 时间仍来自本机系统时钟，不是独立时间服务器；B1不支持跨主机协调。

条件写并读回为事务内的授权检查点；正式提交前再核对 Lease，事务中无 await/网络调用。COMMIT 的磁盘等待仍可能跨过墙钟到期：不能声称 COMMIT 返回时 Lease 必然有效。短写事务序列化使接管者只能在该事务完成后行动，检查点合法且成功提交的 completed 结果不得因调用方晚收到响应而撤销。已过期后才开始的条件写必须拒绝，即使无人接管。

#### 5.4 登记尝试

`begin_attempt(claim) -> 新授权 / 已登记 / 预算耗尽`

- 新授权：Fence 成立、attempt_count<max_attempts、last_attempt_generation 不等于当前 generation；同事务 +1 并记 generation，COMMIT 后才允许调用。
- 已登记：不再次加计数，**不再授予调用权**。
- 预算耗尽：不改计数，不调用 Parser，转最终 failed。
- 每次重试需释放到 retry_wait，下一次领取使用新 generation。

begin_attempt 的 COMMIT 与真正调用之间崩溃会消耗一次预算，即便 Parser 未执行。这是保守调用意图预算，不是 Provider 计费计数。

#### 5.5 重试与终结

`schedule_retry(claim, error, delay_ms)`：仅 Fence 成立时进入 retry_wait、持久 next_attempt/last_error，清 owner/request/Lease；generation 与计数保留。

`finalize(claim, result)`：严格 codec、Fence、唯一正式结果检查和必要 Message 更新处于同一事务；清租约字段、设置 completed_at、读回完整结果及 Message 后 COMMIT。

- parsed/unresolved 来自一次已登记的当前 generation 的 Parser 调用。
- 正常 failed 来自当前调用的终态错误；恢复时 `attempt_count=max_attempts` 可无新登记直接生成 `PARSE_ATTEMPTS_EXHAUSTED` 的最终 failed。
- parsed：只完成 ParseJob，Message 保持原 pending。
- unresolved/failed：同事务写 ParseJob 与 Message blocked，Message.error 与正式 result.error 一致。
- rowcount=0：领取权/前态丢失；丢弃该未提交候选，不调用无条件 mark 补写。
- completed 结果不可替换、追加或重新解析。明确重复确认相同结果只返回已有记录；不同结果报告 `PARSE_RESULT_CONFLICT`，不修改。
- 数据库 busy 可在有效 Lease 内重试同一候选的持久提交，不增加 attempt_count、不再次调用 Parser。
- 若等待数据库使 Lease 到期，旧候选不得提交；后续接管可能再用一次预算调用，故不能承诺 Parser exactly-once。

### 6. 取消、不确定结果和恢复

#### 6.1 不能把 await 取消当作 SQLite 已回滚

为每次数据库操作保留被 shield 的在途 task/完成句柄。取消后先确认该操作结束，再查询；不能假设仅凭 threading.Lock 就保证新查询排在尚未开始的旧线程调用之后。

| 不确定窗口 | 处理 |
|---|---|
| claim await 取消、尚不知 MessageId | 用调用前已知 owner/request 查询；不重新提交该 request 的 claim 操作、不假设返回 token 存在 |
| claim 已提交但 Worker 正在关闭 | 不调用 Parser；若可安全确认并释放则 retry_wait，否则等 Lease 到期 |
| begin_attempt await 取消/授权确认丢失 | 查询计数可确认登记，但不据此再次授权；该 generation 不调用/再调用 Parser，释放或等 Lease |
| Parser 超时/取消但可能仍执行 | 禁止迟到 callback 自行提交；按照 Fence 重试/终结，过期候选丢弃 |
| finalize COMMIT 成功但响应丢失 | get_final_result 读回并比较，确认原结果；不重新解析 |
| rollback 失败 | 关闭/禁用连接，安全报错，停止使用该 Repository；不能把 unknown 记成成功 |

bounded shutdown 只停止新领取并等待/收尾已有操作；不能保证不可取消的后台线程/Parser立即停止。不能在结果未确认时盲目重放写操作，也不能在未结束的 SQLite 操作上并发关连接。

#### 6.2 恢复收敛

- Lease 未到期：不接管。
- Lease 到期且预算未耗尽：新 generation 领取，重新登记并执行下一次尝试。
- Lease 到期且预算已耗尽：新 generation 领取，直接原子提交最终 failed + blocked。
- retry_wait 尚未到期：不领取；到期后同样先检查预算。
- 已 completed：永不重新调用。解析成功与结果读取不依赖内存事件。
- 重复崩溃发生在调用登记之前可以持续增加 generation，而不消耗调用预算；B1保证的是调用次数上界和预算耗尽终结，不承诺在无限基础设施故障下按固定时间收敛。空轮询/数据库故障使用有界等待，避免忙循环。

### 7. Worker、Fake 与错误分类

#### 7.1 真实 Worker 生命周期

ParserWorker 依赖 Repository、MessageReader 和 InstructionParser。提供 `run_once()` 与持续轮询/停止能力；生命周期不是 Fake 专属代码。

路径：发现 → claim → MessageReader 严格读原正文 → 可选候选快照 → begin_attempt → 事务外 await parser.parse → 验证返回对象 → retry/finalize。

B2 只替换 InstructionParser 与可信候选来源，不替换领取、预算、Fence、结果持久化或恢复机制。

AgentDirectory 已存在；B1可省略目录并传 None，测试可注入仅内存 Fake。目录为空 tuple 与 None 不混同：前者明确无候选，后者没有提供目录。B1只检查候选/返回值的数据形状，不验证名称是否真实或别名是否歧义。

#### 7.2 Error taxonomy

| 结果/异常 | Worker 行为 |
|---|---|
| 合法 parsed | 正式提交；不验证目标实际存在 |
| 合法 unresolved | 正式提交 blocked，无部分指令 |
| 合法 failed | 作为最终结果提交 blocked，即使 error.retryable=true 也不自行改成重试 |
| Parser 抛出安全 ContextFlowError 且 retryable=true | 有预算则 retry_wait；无预算则最终 failed |
| Parser 超过执行截止时间 | `PARSER_TIMEOUT`；有预算重试，无预算终结；不证明执行已停止 |
| 外部取消/Parser CancelledError | 不吞掉停止信号；有可用授权时释放到 retry_wait，否则等待 Lease；已登记预算不退回 |
| 返回非 ParseResult 或违反持久 codec | `PARSER_CONTRACT_VIOLATION`，最终 failed；不能保存原始不可信对象或部分结果 |
| 其他未知异常 | `PARSER_EXECUTION_ERROR`，最终 failed；只保留安全异常类别，不盲目循环重试 |
| Message/持久 ParseResult 损坏 | fail closed，报告存储错误；不重新解析以掩盖损坏，也不覆盖已有正式结果 |
| SQLite busy/写入失败 | 不增加调用预算；busy 有界等待，其他写入失败确认回滚/持久结果后停止或待恢复 |

B2 的原始模型输出结构失败如需重试，由实际 InstructionParser 映射为明确 retryable 异常；不需要重写 Worker。

#### 7.3 Fake 能力与安全隔离

FakeInstructionParser 同样实现现有 parse 签名，支持：合法 parsed/unresolved/failed、可重试异常、非可重试异常、阻塞/超时、主动取消、延迟响应及错误类型返回。可使用脚本序列与 Event 控制，不在 Fake 中模拟 Repository。

CLI 只提供 Fake 模式，必须显式指定演示数据库，不默认打开环境 Message Store；日志明确标识 FAKE，不需要 token 或网络。测试/演示须使用隔离数据，不能让未来 C 消费 Fake 正式结果。

Fake completed 不会因为 B2换了真实 Parser 而重解析；演示数据库与正式数据库分开，不添加 reparse开关来绕过冻结规则。

#### 7.4 参数建议与截止时间

以下是 B1 Fake 演示默认建议，不是未来 Provider 的 SLA。均可由显式配置/CLI覆盖并校验：

| 参数 | 建议值/规则 |
|---|---|
| lease_duration_ms | 30,000 |
| execution_timeout_ms | 10,000 |
| submit_margin_ms | 5,000 |
| busy_timeout | 现有 5 秒；计算余量时纳入 |
| max_attempts | 3，总尝试数，不是第一次以外再重试3次 |
| retry_base_ms / retry_cap_ms | 1,000 / 30,000；指数退避取上限，不增加复杂随机策略 |
| poll_interval_ms | 500；空轮询/短时 DB busy 均不紧循环 |
| discover_limit | 100，每轮有界发现 |

max_attempts 写入新 job；修改启动配置只影响后续新作业。next_attempt 按已持久 attempt_count 计算并保存，不在重启后从零退避。

启动校验 `execution_timeout + submit_margin < lease_duration`，提交余量覆盖至少一次 SQLite busy wait 及本地验证余量。claim/begin_attempt 自身耗时后，Worker再次计算剩余时间并以 monotonic 作进程内截止：剩余不足不发起新调用。墙钟 Fence 始终由数据库决定，不接受 monotonic 值入库。

ParserWorker为每次 Parser 调用创建独立asyncio Task。正常返回只由Worker主协程持有候选；Parser没有Repository引用或完成回调，因此迟到任务本身没有提交能力。超时或停止时先cancel并在有界时间内等待：

- 若任务确认结束，按timeout/取消策略调度重试或终结；
- 若实现拒绝取消且在宽限期后仍运行，Worker保留该Task引用并观察到结束、丢弃其结果，同时不复用同一Parser实例并停止该Worker继续领取，避免遗留调用与后续尝试共享不安全状态；Lease到期后可由另一个Worker实例接管；
- 进程退出可终止本进程遗留调用，但B1不声称能够撤销已经到达未来远程Provider的请求。

这保证迟到候选不会自动进入Repository；最终写入仍必须再次通过Lease/Fence。

### 8. ParseResult codec 与后续读取

内部 codec 显式映射阶段 0 字段：status、每条 target_agent/content、ErrorInfo 的 code/message/retryable/details；JSON 使用 canonical 编码、UTF-8、禁止非有限数和重复对象键。

- 写入前要求真实 ParseResult，重构/校验全批指令；不使用 `dataclasses.asdict()` 处理 MappingProxyType。
- 读取要求 exact fields、枚举合法、有序列表、ErrorInfo 合法，并重新构造阶段 0对象。
- parsed 重算 fingerprint；任何不匹配或残缺结果报告 `PARSE_JOB_CORRUPT`，不返回有效子集。
- ErrorInfo 只能持久化经过安全映射的上下文；不保存凭据、Provider 原始响应或异常堆栈中的敏感数据。

B1内部 Reader 只定义：

- `get_final_result(message_id) -> StoredParseResult | None`：不存在 job或尚未 completed 返回 None；完成时返回 MessageId、完整 ParseResult、完成时间；损坏抛安全错误。

B1 不提前提供无游标的全局结果枚举接口。未来 E/C 若需要扫描 completed+parsed，必须结合 Task批次权威状态设计有确定游标的恢复查询，不能把 `limit` 列表误当成消费队列。

C未来通过 Reader按 MessageId读取后，重新解析全部目标引用为 AgentId（不是再次调用 LLM），原子创建整批 Task/Delivery/fingerprint。Task批次存在是消费权威；B1不添加 consumed，不创建 Task表或消费通知。B/C正式公开 Reader形状在后续契约审查冻结。

### 9. 验收矩阵：设计门槛，不是测试成绩

B1 的 16 项实现门槛均已由自动化测试覆盖；实际命令与最终数量见 `../reports/CF-CORE-004-B2.md`。下表保留故障制造方式与必须观察的结果，不能只凭测试名称判断通过。

| ID | 门槛 | 竞争/故障制造方式与必须观察的结果 |
|---|---|---|
| B1-T01 | v1旧数据与索引保留 | 临时库按原始 v1 DDL装入 Supervisor/Agent、pending/blocked/processed；对比全部行/Envelope字节、指纹、时间、ID、原表/索引SQL |
| B1-T02 | 空库直接 v2 | 全新临时文件初始化；验证v2、两个表/索引、FK/CHECK及完整读写 |
| B1-T03 | 重复/并发迁移幂等 | 独立连接/进程同屏障开始维护入口；最终一份v2、原数据不变，无重复对象 |
| B1-T04 | 迁移中断原子性 | 临时子进程在已有DDL写入但未COMMIT时被终止；另以authorizer拒绝中途DDL；重开必须完整v1/空v0，无半v2 |
| B1-T05 | 双Worker单一有效领取 | 两个真实Repository连接同时释放屏障竞争同一Message；只有一个当前有效token，PK一行；不只用同实例Lock |
| B1-T06 | Lease未到期不可重复领 | 第二连接持续尝试/查询；generation、owner和attempt_count不变 |
| B1-T07 | Lease过期新generation接管 | 受控数据库时钟或条件等待到期；第二连接取得严格更大generation与新owner |
| B1-T08 | 旧Worker迟到写入拒绝 | 旧Fake阻塞于Event；新Worker接管后释放旧结果；旧begin_attempt/retry/finalize均拒绝，且未接管但已过期也拒绝 |
| B1-T09 | parsed原子完成且pending | 经真实MessageStore接收→Worker→独立连接Reader重读；完整结果与fingerprint存在，Message仍pending |
| B1-T10 | unresolved/failed与blocked原子 | 对两状态分别提交；检查空指令、相同ErrorInfo；其他连接只能见提交前或完整提交后组合 |
| B1-T11 | 结果写/commit失败不伪完成 | Repository初始化后注入临时RAISE(ABORT/IGNORE)及延迟FK提交失败；检查rowcount/读回/rollback，job与Message均未半提交 |
| B1-T12 | COMMIT后崩溃不重复结果 | 子进程确认真实COMMIT后、业务响应前退出；重开读取原结果与ID，Fake调用计数不再增长 |
| B1-T13 | 到期作业重启恢复 | 进程登记尝试后退出，不调用内存恢复函数；新进程到期接管并完成/终结 |
| B1-T14 | 预算持久有上限 | max=3，失败/重启到第三次；新配置不能重置或扩大旧job上限；第三次崩溃到期后只领取并failed，无第四次调用 |
| B1-T15 | Fake超时/取消/异常可解释 | Event制造阻塞/迟到；分别取消claim、登记、Parser、finalize await；逐窗检查预算、Lease、无越权写入、恢复或终结 |
| B1-T16 | 阶段A/0回归 | 保留原有接收、duplicate/conflict、来源、Receiver、Bridge与契约行为测试，不删断言来适配新实现 |

额外必须覆盖的边界断言：

- 惰性发现双连接幂等；只发现Supervisor pending；completed+parsed pending不重建。
- 所有NULL/非法状态组合、整数类型、预算和FK约束；SQL中NULL使CHECK得到unknown时也不能误放行非法组合。
- begin_attempt重复只返回已登记、不二次授权；SQLite busy不消耗预算；未确认授权不盲目调用。
- Message前态变更后所有owner写入拒绝；结果Reader不覆盖/自动修复损坏。
- 非ParseResult、部分指令、JSON重复字段、Unicode编码问题、错误/fingerprint组合及fingerprint篡改。
- Parser等待时，另一Message可以被Receiver持久接受：证明网络/等待不在写事务内；持续Worker不阻塞事件循环。
- rollback失败禁用连接；敏感异常不泄露；Fake CLI不自动打开默认/用户数据库。
- 相同最终结果确认幂等、不同结果冲突；B1内部Reader可跨Repository重开读取，不依赖内存事件。

测试时钟位于Repository/SQLite取时边界，不由Worker直接提供now。至少保留一个使用真实SQLite时钟的到期测试。竞争用真实独立连接/进程与屏障；不能仅凭asyncio.gather两个受同一Lock串行的调用宣称验证双Worker。进程终止仅针对测试启动的子进程和临时库。

### 10. 预期文件范围与开发顺序

以下文件范围已经按连续 B1→B2 授权实施；实际状态和额外 B2 文件见交付报告：

| 文件 | 后续职责 |
|---|---|
| contextflow_core/database_schema.py（新） | 原v1定义、v2定义、严格校验、显式维护迁移；单一Schema所有者 |
| contextflow_core/message_store.py（最小修改） | 改用共用Schema入口；接收/来源/幂等/mark行为保持 |
| contextflow_core/parse_jobs.py（新） | B1内部状态/Claim/Reader/Repository与短事务 |
| contextflow_core/parse_result_codec.py（新） | 持久ParseResult严格编解码与指纹验证 |
| contextflow_core/parser_worker.py（新） | 真Worker、时限/重试/取消/恢复及Fake模式CLI |
| contextflow_core/fake_parser.py（新） | 可脚本控制的InstructionParser Fake；明确非语义实现 |
| tests/core/test_database_schema.py（新） | 空库/迁移/中断/保留/严格验证 |
| tests/core/test_parse_jobs.py（新） | 状态/并发/Fencing/预算/原子提交/Reader |
| tests/core/test_parse_result_codec.py（新） | 完整字段/损坏/Unicode/指纹 |
| tests/core/test_parser_worker.py（新） | 生命周期、故障窗口、迟到、恢复与隔离CLI |
| tests/core/test_message_store.py（必要增补） | v2使用及v1迁移要求；保留原行为测试 |
| docs/CF-CORE-004-B1-usage.md、reports/CF-CORE-004-B1.md（后续） | 使用/迁移说明与实际验收证据 |

开发按 Schema/状态不变量 → Repository与codec → 恢复/预算 → Worker/Fake → B2 Directory/Provider/Parser → 竞争/故障与全回归 → 使用及交接推进，并保存了 RED→GREEN 证据。

后续验证命令（从contextflow-research执行）：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/contracts tests/core -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests -q -p no:cacheprovider
```

测试使用tempfile创建真实文件库，关闭所有连接后清理；不使用用户数据库。报告逐项说明T05/T08/T11/T12/T14/T15如何制造竞争/故障以及实际观察，不能只列测试名字。

### 11. 源码依据与本轮验证边界

| 依据 | 支持的结论 |
|---|---|
| contextflow_core/database_schema.py:23-168 | v2的原messages DDL、ParseJob DDL、状态CHECK及索引 |
| contextflow_core/message_store.py:75-115 | 长连接、连接锁及共用Schema入口 |
| contextflow_core/message_store.py:181-266 | accepted须COMMIT、完整Envelope/身份/指纹读回 |
| contextflow_core/message_store.py:280-336 | list_pending不领取，mark无前态/owner CAS，不可用于B1原子finalize |
| contextflow_core/contracts/models.py:77-118 | ErrorInfo与pending错误合法性 |
| contextflow_core/contracts/models.py:167-210 | ParseResult完整组合、指纹算法；当前无持久codec |
| contextflow_core/contracts/models.py:369-379 | ProcessResult不能表示parsed后仍pending的Worker结束 |
| contextflow_core/contracts/ports.py:55-89 | 现有Extraction/Parser/AgentDirectory接口，可直接复用 |
| contextflow_core/contracts/ports.py:104-115 | Task批次为C消费/恢复权威 |
| contextflow_core/contracts/README.md:102-138 | B1持久结果明确改变阶段0的仅内存候选边界，不能声称原本已实现 |
| contextflow_core/receiver.py:61-102 | Receiver只接收并确认，不调用Parser |
| tests/core/test_message_store.py:219-298 | 真实写入/COMMIT失败、SQLite取消后事务仍可能完成的测试先例 |
| tests/core/test_message_store.py:318-360 | 双连接竞争与被动mark前态语义 |

规格核对时 SQLite 3.40.1 的 epoch 取时表达式只在独立内存库验证。随后按连续 B1→B2 授权实施；所有 Schema/迁移测试和演示均使用隔离临时/新建数据库，没有迁移现有手动验收库或默认用户库，也未 commit/push。

**当前边界：** B1 已实施并作为 B2 可靠性底座；完整结果与限制见 `../reports/CF-CORE-004-B2.md`。不自动迁移现有手动验收库/默认用户数据库，不自动 commit/push，不进入阶段 C。
