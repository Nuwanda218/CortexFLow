# CF-CORE-004：阶段 B 前置技术勘察报告

日期：2026-11-07
基线分支：`feat/cf-core-003-message-store`
基线提交：`76afa1c`
任务性质：只读技术勘察；本报告是唯一预期仓库变更

## 证据口径

本报告使用以下标签区分证据强度：

- **[源码已实现]**：当前生产代码存在实际运行逻辑。
- **[类型契约] / [接口契约]**：生产源码定义了 DTO、枚举或 Protocol；不代表对应业务服务已实现。
- **[测试已覆盖]**：现有测试对该行为或契约组合有断言；不等于所有运行环境都已验证。
- **[测试 Fake]**：测试中的固定响应替身，只证明 Port 可被实现，不证明业务能力。
- **[文档中声明]**：当前架构文档表达的边界或意图；若与源码冲突，以源码为准。
- **[过时文档] / [历史文档]**：已被后续源码或用户验收推进超越，仅用于记录差异。
- **[会话声明]**：用户在本次/此前会话中确认的事实，尚未写回仓库文档。
- **[本轮命令实测]**：本轮实际执行的只读检查或隔离测试结果。
- **[设计建议]**：本报告提出的后续方案，当前尚未实现。

文件引用均相对 `contextflow-research/`，例如：

`contextflow_core/message_store.py:290-332 — 接收事务在 commit 后才返回 accepted`。

---

# 1. Executive Summary

## 1.1 是否具备进入阶段 B 的基础

**具备可靠的阶段 A 底座，但尚不具备直接开始 Parser Worker 实现所需的全部状态、身份和迁移机制。**

已有基础包括：

1. Supervisor Message 已可靠写入 SQLite，并保存完整 Envelope、Core MessageId、来源和处理标记。
   **[源码已实现]** `contextflow_core/message_store.py:27-76,242-332`
2. Store 已有 WAL、显式短写事务、busy timeout、连接内互斥和跨连接 SQLite 仲裁。
   **[源码已实现]** `contextflow_core/message_store.py:127-191,290-332`
3. `ParseResult` 能表达有序、多目标、全有或全无的解析结果，并提供稳定指纹。
   **[源码已实现]** `contextflow_core/contracts/models.py:167-210`
4. Parser、LLM Provider、Agent Directory 和 Dispatcher 已有 Protocol 边界。
   **[接口契约]** `contextflow_core/contracts/ports.py:55-101,136-160`
5. Receiver 只负责接收和持久化，不会调用 Parser，适合与独立 Worker 解耦。
   **[源码已实现]** `contextflow_core/receiver.py:61-102`

但当前仍缺少：

- 原子领取、Lease、Fencing、重试调度和解析尝试计数；
- ParseResult 持久化及 MessageId 关联；
- Schema v1 → v2 迁移机制；
- 真实 Agent 名称/别名目录；
- 实际 LLM Adapter、严格原始输出校验和 Parser Worker；
- 阶段 C 的 Task 批次实现。

## 1.2 最重要的架构发现

### 发现 A：Message 状态不能直接充当 Parser 状态机

`Message.processing_state` 只有：

- `pending`
- `processed`
- `blocked`

**[源码已实现]** `contextflow_core/contracts/models.py:32-35`

它表示整条 Message 的下游处理进度，而不是 Parser 的 `ready/running/retry_wait/finalized`。`processed` 只有在所有下游持久步骤完成后才应设置，并不表示“解析成功”。
**[接口契约]** `contextflow_core/contracts/ports.py:34-52`
**[文档中声明]** `contextflow_core/contracts/README.md:57-66`

直接加入 Parser 状态会同时改变冻结枚举、Message 构造校验、SQLite CHECK 和 Schema 版本，并把两个状态维度混在一起。

**推荐：保留 Message 状态语义，新增独立解析作业状态。**

### 发现 B：阶段 0 与本轮初步方案存在明确边界冲突

阶段 0 曾明确选择：

> 不新增 ParseResult 持久化实体、解析版本、检查点或调度组件。

**[文档中声明]** `contextflow_core/contracts/README.md:102-114,132-138`

本轮初步方案要求持久化 ParseResult、Lease/Fencing 和跨重启重试。这不是阶段 0 的自然延伸，而是需要显式批准的新恢复边界和 Schema v2。

技术上可以兼容现有 `ParseResult` 形状；但不能把该变化当作普通局部实现。

### 发现 C：Endpoint Registry 不是 Agent Directory

Endpoint Registry 保存的是：

```text
(provider, native_session_id) <-> EndpointId
```

**[源码已实现]** `contextflow_core/registry.py:37-85`

Agent 则是独立身份域：

```text
target_agent: str -> AgentDirectory.resolve() -> AgentDescriptor | None
                                             -> descriptor.agent_id
```

**[接口契约]** `contextflow_core/contracts/ports.py:76-89`
**[类型契约]** `contextflow_core/contracts/models.py:167-180,213-238`

仓库没有实际 AgentDirectory、别名索引或可信候选列表。测试中的 `FakeAgentDirectory` 只返回固定值，不能视为生产能力。
**[测试 Fake]** `tests/contracts/test_ports.py:60-68,265-272`

### 发现 D：当前 MessageStore 是持久收件箱，不是领取队列

`list_pending()` 是普通 SELECT，不会领取、加锁或返回 token：
**[源码已实现]** `contextflow_core/message_store.py:377-390`

两个 Worker 可以同时读到同一 Message。现有 `mark_processed/mark_blocked` 也没有 owner、generation 或 lease 条件：
**[源码已实现]** `contextflow_core/message_store.py:392-433`

因此不能采用：

```text
list_pending -> 调 LLM -> mark_processed
```

作为多 Worker 或崩溃恢复方案。

### 发现 E：SQLite 能保证正式结果唯一，但不能保证 LLM 恰好调用一次

Worker 可能在以下任一窗口崩溃：

- 记录调用意图之后、真正发请求之前；
- 请求已到 Provider、响应返回之前；
- 收到响应之后、SQLite 结果提交之前。

Lease/Fencing 可以阻止旧 Worker 覆盖持久结果，却无法撤销已经发出的远程请求。因此应分别承诺：

- **正式解析结果最多提交一次／首个合法 fenced 提交获胜；**
- **LLM 调用可能至少一次；不能承诺 exactly-once。**

## 1.3 推荐的最小方向

如果用户确认“阶段 B 必须可靠持久化 ParseResult 并支持自动接管”，推荐：

1. `InstructionParser` 保持纯解析边界，不直接访问数据库。
2. 新增独立 `ParserWorker` 作为编排层，Receiver 不变。
3. 在**同一个 SQLite 文件**中新建一张 `message_parse_jobs` 表，同时保存当前作业状态和唯一正式 ParseResult。
4. `messages.processing_state` 不增加 Parser 状态。
5. `parsed` 最终结果提交后，Message 仍保持 `pending`，等待 C 提交 Task 批次。
6. `unresolved` 或重试耗尽后的最终 `failed`，在同一事务中保存结果并将 Message 标为 `blocked`。
7. 使用 `claim_generation + lease_owner + lease_expires_at` 进行 fenced completion。
8. 第一版不做完成后的人工重新解析、不保存多版本、不新增 `consumed` 布尔值。
9. 阶段 C 未来以“是否已有完整 Task 批次”为消费权威，不依赖重复的 `consumed` 标记。

该方向不要求修改现有 `ParseResult`、`ParsedInstruction` 或 `TaskDispatcher.create_tasks(source_message_id, parsed)` 的形状。

## 1.4 必须由用户确认的事项

按优先级：

1. 是否批准在阶段 B 同步实现原属 E 的 Worker 领取／恢复编排，同时保持 `InstructionParser` 无数据库。
2. 是否批准推翻阶段 0 的“不持久化 ParseResult”边界，并授权 Schema v2 迁移。
3. 是否采用独立解析作业状态，而不扩展 Message.processing_state。
4. Agent 候选及别名的可信来源是什么；是否在 B 前补一个最小配置驱动 AgentDirectory。
5. Worker 是单实例还是必须支持多个并发实例／自动接管；这决定 Lease/Fencing 是否属于最低范围。
6. LLM Provider、SDK、模型、超时及重试成本政策；仓库当前没有实际 LLM 客户端或依赖声明。

---

# 2. Existing Implementation

## 2.1 SQLite Message Store

### 2.1.1 Message 的实际存储

当前数据库版本为 `PRAGMA user_version=1`，唯一业务表是 `messages`。
**[源码已实现]** `contextflow_core/message_store.py:27-76`

| 列 | 当前含义 |
|---|---|
| `message_id` | Core `MessageId`，主键 |
| `kind` | `supervisor_instruction` 或 `agent_report` |
| `submission_id` | Supervisor 提交幂等身份 |
| `source_agent_id` / `report_id` | 内部 Agent 报告身份；Supervisor 行为 NULL |
| `endpoint_id` | Supervisor Endpoint |
| `native_session_id` | 原生会话身份 |
| `native_message_id` | Envelope 中的原生 Assistant Message ID |
| `envelope_json` | 完整规范化 Envelope JSON，包括正文及 nullable 来源字段 |
| `fingerprint` | 完整 Envelope 的 SHA-256 |
| `received_at` | UTC 接收时间 |
| `processing_state` | `pending/processed/blocked` |
| `processing_error_json` | 被动处理错误快照 |

Store 在接收时重新构造 `SupervisorEnvelope`，使用既有 canonical/fingerprint，再保存冗余来源列。
**[源码已实现]** `contextflow_core/message_store.py:242-260`

读取时会重新构造阶段 0 对象，并核对来源冗余列和指纹；不只是返回裸数据库行。
**[源码已实现]** `contextflow_core/message_store.py:334-363`

### 2.1.2 当前状态及实际语义

`ProcessingState`：

| 状态 | 当前语义 |
|---|---|
| `pending` | 下游持久化处理尚未全部完成 |
| `processed` | 调用方承诺所有下游持久步骤已完成；不是 Task 完成或发送成功 |
| `blocked` | 下游处理被错误阻塞，并保留 `ErrorInfo` |

**[类型契约]** `contextflow_core/contracts/models.py:32-35,112-142`
**[接口契约]** `contextflow_core/contracts/ports.py:34-52`

构造和 SQL 只验证状态／错误组合：

- blocked 必须有错误；
- processed 不得有错误；
- pending 可以带或不带错误；当前 Store 新消息写入 NULL，公开 mark API 也不会产生 pending+error，但 DTO 与 SQL CHECK 本身未禁止该组合；
- pending 并未被定义为 Parser ready/running。

**[源码已实现]** `contextflow_core/contracts/models.py:112-129`
**[源码已实现]** `contextflow_core/message_store.py:49-60`

Store 是“被动标记器”，不是状态机所有者。它允许调用方重复标记，当前测试甚至固定了 `blocked -> processed` 可以被调用方覆盖。
**[测试已覆盖]** `tests/core/test_message_store.py:344-360`

因此，不能从字段名推断它已经具备 Parser 状态转换保护。

### 2.1.3 pending 由谁设置、读取和更新

- 新消息 INSERT 时，Store 显式写 `ProcessingState.PENDING`。
  **[源码已实现]** `contextflow_core/message_store.py:312-325`
- Receiver 只调用 `accept_message()`，不领取或推进 pending。
  **[源码已实现]** `contextflow_core/receiver.py:61-102`
- `list_pending(kind, limit)` 已存在，并按 `(received_at, message_id)` 返回普通查询结果。
  **[源码已实现]** `contextflow_core/message_store.py:377-390`
- 当前生产代码没有 Parser Worker 调用它；实际调用主要在测试和使用说明中。
- `mark_processed/mark_blocked` 可更新状态，但没有处理者身份、前态、lease 或 fence 条件。
  **[源码已实现]** `contextflow_core/message_store.py:392-433`

现有 pending 索引为：

```sql
CREATE INDEX messages_pending
ON messages(kind, processing_state, received_at, message_id)
```

**[源码已实现]** `contextflow_core/message_store.py:72-75`

### 2.1.4 事务与连接管理

每个 `SQLiteMessageStore` 实例：

- 持有一个长期 `sqlite3.Connection`；
- `isolation_level=None`，由代码显式管理事务；
- `check_same_thread=False`，通过 `asyncio.to_thread` 执行 SQL；
- 用一个 `threading.Lock` 串行化该连接上的读、写和关闭；
- 不接受 `:memory:` 或 `file:` URI 数据库；
- 没有连接池或通用 Unit of Work。

**[源码已实现]** `contextflow_core/message_store.py:127-164,233-240,278-290,384-390`

接收和标记各自直接执行 `BEGIN IMMEDIATE/COMMIT/ROLLBACK`，但没有公开或内部通用事务回调，阶段 B 不能通过组合两个现有公开方法获得跨表原子性。
**[源码已实现]** `contextflow_core/message_store.py:290-332,411-433`

### 2.1.5 并发设置

初始化执行：

```text
journal_mode = WAL
synchronous = FULL
foreign_keys = ON
BEGIN IMMEDIATE
```

**[源码已实现]** `contextflow_core/message_store.py:166-191`

`busy_timeout` 默认 5 秒，通过 `sqlite3.connect(timeout=...)` 设置；锁冲突映射为 retryable `MESSAGE_STORE_BUSY`，Store 不做内部指数退避。
**[源码已实现]** `contextflow_core/message_store.py:88-106,136-160`

现有测试覆盖真实锁等待、commit 回滚、异步取消后后台 SQLite 仍可能完成、双连接首次提交竞争。
**[测试已覆盖]** `tests/core/test_message_store.py:233-326`

重要影响：取消 `asyncio.to_thread` 的 await 不会取消正在运行的 SQLite 事务。未来 `claim()` 被取消时必须按“领取结果未知”处理，并使用调用前已知的唯一领取请求身份查询、读回 token；不能假定已得到 MessageId/generation，或假定没有领取。
**[测试已覆盖]** `tests/core/test_message_store.py:268-298`

### 2.1.6 唯一约束和索引

当前持久唯一性：

- `message_id` 主键；
- Supervisor：`submission_id` 部分唯一；
- Agent 报告：`(source_agent_id, report_id)` 部分唯一。

**[源码已实现]** `contextflow_core/message_store.py:34-75`

没有：

- 每个 Message 的解析作业唯一性；
- 解析结果唯一性；
- claim owner 或 active lease；
- retry/LLM call 计数；
- fingerprint 全局唯一性。

不同 submission_id 即使正文相同，也会保存为不同 Message；这是既有业务语义，而不是缺陷。

### 2.1.7 Schema 版本和迁移

当前策略：

- 空且无对象的 v0 数据库可初始化为 v1；
- 已有 v1 可打开；
- 未知版本和非空 v0 fail closed；
- v1 精确校验 messages 建表 SQL、列顺序、必要索引 SQL；
- messages 上的未知 trigger 被拒绝。

**[源码已实现]** `contextflow_core/message_store.py:166-213`

当前**没有 v1 -> v2 migration path**。`SCHEMA_VERSION != 0/1` 会在迁移发生前被拒绝。
**[源码已实现]** `contextflow_core/message_store.py:27,166-190`

测试覆盖未知版本不迁移、外来数据库不接管、约束／部分索引谓词／trigger 被篡改时拒绝。
**[测试已覆盖]** `tests/core/test_message_store.py:458-508,537-558`

阶段 B 若新增同库表，必须明确版本所有权。技术上在 user_version=1 下偷偷加入额外表可能暂时绕过当前 owned-object 校验，但那会产生未版本化 Schema 漂移，不能作为正式方案。

### 2.1.8 阶段 B 对阶段 A 的影响

阶段 B 扩展同一数据库会影响：

1. Store 初始化的版本判断和精确 Schema 校验；
2. 所有打开既有 v1 数据库的 Receiver/CLI；
3. 未知版本 fail-closed 测试；
4. 并发首次迁移、失败回滚和旧数据保留测试；
5. WAL 下多连接写锁等待；
6. 使用旧二进制打开 v2 数据库的行为。

推荐迁移不修改 `messages` 表定义，只新增解析表和索引，以最大程度保留 A 行为。但 `user_version` 仍应显式升级为 2；旧阶段 A 二进制**重新打开**数据库时会拒绝 v2，已打开的旧连接不会自动重新检查版本，仍可能操作 unchanged messages。因此升级必须先停止旧 Receiver/Worker，再迁移并启动兼容 v2 的进程，不能把版本拒绝当作运行中互斥。

## 2.2 Receiver

Receiver 的 `receive()` 只把原始 Envelope 包装为 `SupervisorSubmission` 并调用 Store；它不读取 pending、不调用 LLM，也不更新 Message 处理状态。
**[源码已实现]** `contextflow_core/receiver.py:61-102`

这证明“Worker 与 Receiver 解耦”与现有结构方向一致。Worker 不需要占用 Bridge 的外部 WebSocket，也不应阻塞 accepted ack。

Receiver 当前不自动重连；这不影响独立 Worker 对已持久化 Message 的扫描，但 Worker 需要自己的生命周期和恢复策略。

## 2.3 ParseResult 契约

### 2.3.1 实际字段

`ParsedInstruction`：

```text
target_agent: str
content: str
```

**[类型契约]** `contextflow_core/contracts/models.py:167-180`

`ParseResult`：

```text
status: ParseStatus
instructions: tuple[ParsedInstruction, ...] = ()
error: ErrorInfo | None = None
intent_fingerprint: 计算属性
```

`ParseStatus` 为：

- `parsed`
- `unresolved`
- `failed`

**[类型契约]** `contextflow_core/contracts/models.py:38-41,182-210`

### 2.3.2 构造时约束

| status | instructions | error | fingerprint |
|---|---|---|---|
| parsed | 必须是非空 `tuple[ParsedInstruction, ...]` | 必须为 NULL | 64 位小写 SHA-256 |
| unresolved | 必须为空 | 必须存在 | NULL |
| failed | 必须为空 | 必须存在 | NULL |

**[源码已实现]** `contextflow_core/contracts/models.py:188-210`
**[测试已覆盖]** `tests/contracts/test_models.py:153-194`

`ParsedInstruction` 还检查非空、长度及 UTF-8；目标最长 256 字符，正文最长 2,000,000 字符。
**[源码已实现]** `contextflow_core/contracts/models.py:167-179`

因此 unresolved/failed **不能携带部分可派发指令**。但这只是结构上“全有或全无”；构造器无法证明 LLM 没有漏掉原文中的一项任务。

### 2.3.3 MessageId 关联

`ParseResult` 本身没有 `source_message_id`。`InstructionParser.parse()` 也只接受文本和可选候选名称：

```python
parse(text, known_agents) -> ParseResult
```

**[接口契约]** `contextflow_core/contracts/ports.py:55-67`

MessageId 由调用方保留；阶段 C 的接口是：

```python
create_tasks(source_message_id, parsed)
```

**[接口契约]** `contextflow_core/contracts/ports.py:136-147`

因此 B 持久化时应以数据库行的 `message_id` 作为外部关联，不应把它强行加入纯值对象 `ParseResult`。

### 2.3.4 intent_fingerprint

算法：

1. 按原 tuple 顺序构造 JSON 数组；
2. 每项包含 `target_agent` 和 `content`；
3. 对对象键排序，但不排序数组；
4. 紧凑 UTF-8 JSON；
5. SHA-256。

**[源码已实现]** `contextflow_core/contracts/models.py:198-210`

因此指纹依赖：

- 指令条数；
- 指令顺序；
- 每项目标；
- 每项正文。

不包含：

- MessageId；
- AgentId；
- 模型或解析器版本；
- 时间；
- 错误。

测试显式验证顺序、目标、正文和条数变化会改变指纹。
**[测试已覆盖]** `tests/contracts/test_models.py:182-194`

同样的 ParseResult 来自不同 Message 时可以具有相同指纹；MessageId 提供批次命名空间。
**[文档中声明]** `contextflow_core/contracts/README.md:116-125`

### 2.3.5 严格 Schema Validation 与 Business Validation

#### 数据结构可以表达

- 多目标、有序指令；
- parsed/unresolved/failed；
- 完整错误；
- 精确指纹；
- 不可变快照。

#### 构造器可以验证

- enum 类型，不接受裸字符串；
- tuple 类型，不接受 list；
- 元素类型；
- 状态／指令／错误组合；
- 文本非空、长度、UTF-8。

#### 当前运行时尚未实现

- 将 LLM 原始 `object` 严格解析为上述对象；
- 拒绝额外字段和错误嵌套结构；
- 判断原文目标是否明确；
- 判断是否漏项、错拆或不完整；
- 选择一致的错误码；
- 不提供默认 Agent；
- 持久化与恢复。

`LlmExtractionPort.extract()` 明确返回裸 `object`，由 B 负责校验。
**[接口契约]** `contextflow_core/contracts/ports.py:55-67`

当前测试 Fake 只返回预置 dict/ParseResult，不是运行时 Parser。
**[测试 Fake]** `tests/contracts/test_ports.py:53-68,128-151,255-272`

### 2.3.6 对阶段 B 是否足够

结论：

- **作为内存中的 Parser 输出，足够。**
- **作为可领取、可恢复、可审计的持久作业记录，不足。**

缺失的是持久化上下文和执行状态，不是 ParseResult 内容本身。最小兼容扩展应是 B 自己的 `StoredParseResult/ParseJobRecord`，关联：

```text
message_id + ParseResult + 作业状态/租约/重试元数据
```

不推荐修改现有 ParseResult 字段。

## 2.4 Endpoint Registry 与 Agent 身份

### 2.4.1 Endpoint Registry 的实际能力

公开方法：

- `get(endpoint_id)`
- `find(provider, native_session_id)`
- `all()`
- `register(provider, native_session_id, session_kind)`

**[源码已实现]** `contextflow_core/registry.py:37-85`

持久唯一索引为：

```text
endpoint_id
(provider, native_session_id)
```

EndpointBinding 包含：

- endpoint_id
- provider
- native_session_id
- session_kind
- created_at/updated_at

不包含 AgentId、Agent 名称、别名或路由。

`RegistryEndpointIdentityReader` 只返回 `EndpointIdentity(endpoint_id, provider, native_session_id)`，不探测 tab 或在线状态。
**[源码已实现]** `contextflow_core/endpoint_identity.py:13-31`

### 2.4.2 在线状态与稳定身份已经分离

对于**已知 Endpoint binding**，EndpointService 的 `resolve()` 返回：

- available
- offline
- ambiguous

未知 Endpoint 不进入这三态，而是抛出 `ENDPOINT_NOT_FOUND`；多标签页不自动选择，离线不删除持久绑定。
**[源码已实现]** `contextflow_core/service.py:99-166`
**[测试已覆盖]** `tests/core/test_service.py:148-184`

这是阶段 B/C 可以复用的设计原则，但不是 Agent 目录本身。

### 2.4.3 Agent 身份现状

`AgentId` 只是运行时可区分的外部字符串 ID，不生成、不注册 Agent。
**[类型契约]** `contextflow_core/contracts/ids.py:76-89`

`AgentDescriptor` 只有：

```text
agent_id: AgentId
available: bool
```

**[类型契约]** `contextflow_core/contracts/models.py:418-426`

`AgentDirectory` 只有 Protocol：

- `known_agents()`
- `resolve(target)`
- `get_agent(agent_id)`

未知应返回 NULL，歧义应报 `AGENT_AMBIGUOUS`，已知但离线仍是目录成员。
**[接口契约]** `contextflow_core/contracts/ports.py:76-89`

全仓没有实际 AgentDirectory、名称、别名、大小写策略或可信候选配置。测试 Fake 对任何输入都返回同一个离线 Agent，不覆盖未知或歧义。
**[测试 Fake]** `tests/contracts/test_ports.py:60-68,265-272`

### 2.4.4 Parser 与 Dispatcher 的身份边界

```text
Parser:
自然语言 -> target_agent: str

Dispatcher:
target_agent: str -> AgentDirectory.resolve() -> AgentDescriptor | None
              -> descriptor.agent_id / descriptor.available
              -> 整批准入 -> Task.target_agent_id
              -> 单独判断可用性和发送
```

未知目标返回 `None`，歧义抛出 `AGENT_AMBIGUOUS`；只有唯一 Descriptor 才能取稳定 AgentId。

Parser 不生成 TaskId 或 AgentId。TaskId 由 C 创建；Task 的目标必须是稳定 AgentId。
**[类型契约]** `contextflow_core/contracts/models.py:213-238`
**[文档中声明]** `contextflow_core/contracts/README.md:35-42,71-88`

Parser 可使用可信候选列表减少幻觉，但 C 仍必须通过 Directory 重新解析**目标引用**并验证全部目标；这里不指重新调用 LLM。合法但离线的 Agent 不能当作未知目标，也不能自动替换。

### 2.4.5 阶段 B 的最小身份缺口

不可使用 Endpoint Registry 推导 “Claude Code” 或 “Codex”。最小选择有两个：

1. **临时只读候选配置**：Worker 从可信配置得到唯一规范选择符 tuple，传给 Parser；C 以后仍需 AgentDirectory。
2. **提前补最小配置驱动 AgentDirectory**：保存稳定 AgentId、唯一规范名称和别名反向索引；B 只读 canonical candidates，C 后续复用 resolve。

推荐第二种，避免 B/C 各维护一份不同映射。它不需要 Project/Role，也不需要把 Agent 等同于 browser Endpoint。

但当前 `known_agents() -> tuple[str, ...]` 无法表达“规范名称 + aliases”的结构。第一版可以：

- 对 Parser 只暴露唯一 canonical selector；
- aliases 仅由 Directory 内部 resolve 使用；
- 若必须把 aliases 显式提供给 LLM，则需要一个新的候选 DTO 或独立候选 Port，不应把格式化说明字符串冒充名称。

## 2.5 现有文档差异

根 `README.md` 仍描述该目录为一次性调查、无正式实现，并称数据库未实现：
**[过时文档]** `README.md:1-4,97-102`

这与当前正式 SQLite Store/Receiver 源码及 CF-CORE-003 报告不一致。本报告以实际源码、contracts README 和 CF-CORE-003 为准。

CF-CORE-003 仓库报告仍记录真实 Extension 链路未验证：
**[历史文档]** `reports/CF-CORE-003.md:5-16,210-217`

**[会话声明]** 用户随后已完成实际消息落盘验收，并在本任务中明确确认阶段 A 完成；该事实没有回写旧报告。它不是本轮新增浏览器验证，本差异也不影响阶段 B 的 Schema/契约判断。

## 2.6 逐问覆盖矩阵

本表将用户任务 1、2、3 的 27 个问题映射到正文和源码，避免把缺失实现误写成已有能力。

| 问题 | 结论 | 正文／关键证据（路径相对 contextflow-research） |
|---|---|---|
| 1.1 Message 如何存储 | messages 保存完整 Envelope、Core ID、来源、指纹和标记 | §2.1.1；`contextflow_core/message_store.py:27-76,242-260` |
| 1.2 当前状态和语义 | pending/processed/blocked 是下游处理标记，不是解析状态 | §2.1.2；`contextflow_core/contracts/models.py:32-35,112-142`；`contextflow_core/contracts/ports.py:34-52` |
| 1.3 pending 设置/读取/更新者 | Store 接收时设置；生产中无 Worker；公开 mark 是被动写入 | §2.1.3；`contextflow_core/message_store.py:312-325,377-433`；`contextflow_core/receiver.py:67-80` |
| 1.4 查询 pending 接口 | 已有 list_pending(kind, limit)，普通 SELECT 不领取 | §2.1.3；`contextflow_core/message_store.py:377-390` |
| 1.5 通用事务封装 | 没有 Unit of Work，各操作内部独立事务 | §2.1.4；`contextflow_core/message_store.py:290-332,411-433` |
| 1.6 SQLite 连接/事务 | 单长期连接、线程互斥、to_thread、显式 BEGIN/COMMIT | §2.1.4；`contextflow_core/message_store.py:127-164,290-332` |
| 1.7 WAL/busy/并发配置 | 设置 WAL/FULL/FK；connect timeout，默认5秒 | §2.1.5；`contextflow_core/message_store.py:136-191` |
| 1.8 唯一约束/索引 | Core ID、Supervisor SID、Agent report对；pending索引 | §2.1.6；`contextflow_core/message_store.py:34-75` |
| 1.9 版本/迁移 | user_version=1；只初始化空v0，无v1→v2 | §2.1.7；`contextflow_core/message_store.py:166-213` |
| 1.10 B 对 A 影响 | 初始化、严格Schema、并发锁及旧版本进程行为需适配 | §2.1.8、§5.1；`tests/core/test_message_store.py:458-558` |
| 2.1 ParseResult 字段 | status/instructions/error；fingerprint是计算属性 | §2.3.1；`contextflow_core/contracts/models.py:182-210` |
| 2.2 ParsedInstruction 字段 | target_agent字符串、content字符串 | §2.3.1；`contextflow_core/contracts/models.py:167-180` |
| 2.3 三种解析状态约束 | parsed非空无错；unresolved/failed空集合有错 | §2.3.2；`contextflow_core/contracts/models.py:188-210` |
| 2.4 部分指令是否禁止 | unresolved/failed构造时禁止；语义漏项仍须B处理 | §2.3.2、§2.3.5；`tests/contracts/test_models.py:153-175` |
| 2.5 来源Message关联 | 值对象无来源字段；调用方/C参数/持久化行关联 | §2.3.3；`contextflow_core/contracts/ports.py:63-66,136-147` |
| 2.6 指纹与顺序 | 有序数组UTF-8规范JSON SHA-256，数组不排序 | §2.3.4；`contextflow_core/contracts/models.py:198-210` |
| 2.7 严格Schema校验 | DTO构造校验已有；LLM原始对象codec未实现 | §2.3.5；`contextflow_core/contracts/models.py:172-196`；`contextflow_core/contracts/ports.py:55-67` |
| 2.8 Business Validation | 状态组合已有；语义、真实目标、漏项核验未实现 | §2.3.5；`tests/contracts/test_ports.py:55-68,130-151`（Fake） |
| 2.9 目标Agent身份 | B为target_agent:str；C解析Descriptor后使用AgentId | §2.4.4；`contextflow_core/contracts/models.py:168-170,214-228`；`contextflow_core/contracts/ports.py:76-89` |
| 2.10 能否满足B/最小扩展 | 输出值足够；Worker需关联记录/Repository，不改原DTO | §2.3.6、§5.3；`contextflow_core/contracts/models.py:182-210` |
| 3.1 Registry查询 | get/find/all/register已实现 | §2.4.1；`contextflow_core/registry.py:37-85` |
| 3.2 Endpoint/Agent/Provider/Session关系 | Endpoint绑定provider/session；Agent独立，无映射实现 | §2.4.1、§2.4.3；`contextflow_core/models.py:16-44`；`contextflow_core/contracts/models.py:407-426` |
| 3.3 名称/别名映射 | 没有生产实现，只有AgentDirectory接口和Fake | §2.4.3；`contextflow_core/contracts/ports.py:76-89`；`tests/contracts/test_ports.py:60-68` |
| 3.4 可信候选列表 | known_agents参数预留，真实来源未实现 | §2.4.5；`contextflow_core/contracts/ports.py:55-67,85-89` |
| 3.5 同名/同类Endpoint | 无name/alias字段；provider/session唯一；多tab是位置歧义 | §2.4.1–2；`contextflow_core/registry.py:40-48,64-85`；`contextflow_core/service.py:99-166` |
| 3.6 在线与合法性分离 | Endpoint实际已分离；Agent只在契约中要求 | §2.4.2–4；`tests/core/test_service.py:157-184`；`contextflow_core/contracts/ports.py:77-89` |
| 3.7 复用与B最小缺口 | 复用类型/身份原则；新增可信候选源，路由和可用性由C | §2.4.5、§7决策4；`contextflow_core/contracts/ports.py:76-101,136-145` |

任务 4 的领取、取消、计数与 lease 见 §4.4–4.7，全部异常场景见 §6；任务 5 的 C 输入、TaskId、部分结果、不可变性与消费权威见 §5.6。这些提议不等于当前 Worker 或 Dispatcher 已实现。

---

# 3. Compatibility Assessment

## 3.1 总表

| 初步设计 | 结论 | 核心条件/主要风险 |
|---|---|---|
| 1. 独立 Parser Worker，与 Receiver 解耦 | **有条件兼容** | 结构可行，但 Worker 的数据库领取/恢复属于原 E 编排职责；需显式批准在阶段 B 同步实现，`InstructionParser` 仍保持无数据库 |
| 2. 优先扩展 Message 状态，否则独立 parse 状态 | **有条件兼容** | 直接扩展 Message 状态不兼容；独立 parse job 状态是推荐条件，但需批准阶段 0 边界变化 |
| 3. SQLite 原子领取 + Lease + Fencing | **有条件兼容** | SQLite 基础足够；当前无 claim schema/CAS，需 Schema v2 和 fenced completion |
| 4. ParseResult 与最终解析状态同事务提交 | **有条件兼容** | 必须用同库新表；parsed 不能把 Message 标为 processed；无通用事务封装 |
| 5. 正式结果前有界自动重试，无完成后重解析 | **有条件兼容** | 只重试最终 ParseResult 产生前的瞬态错误；unresolved/最终 failed 不自动重试 |

没有一项属于“证据不足”；现有源码足以判断兼容条件。真正 Provider 的超时和 SDK 自动重试行为尚未选择，因此具体参数仍需后续决定。

## 3.2 独立 Parser Worker — 有条件兼容

### 源码依据

Receiver 只保存消息：
`contextflow_core/receiver.py:61-102`。

Parser 是独立 Protocol：
`contextflow_core/contracts/ports.py:55-67`。

### 现有能力

- durable pending Message；
- MessageReader/Store；
- Parser 与 LLM Provider Port；
- Receiver 快速确认，不依赖解析。

### 缺失能力

- Worker 生命周期和轮询；
- claim/retry/result repository；
- Provider adapter；
- 关闭、取消和崩溃恢复流程。

### 兼容性风险

若把“Parser Worker”理解成 `InstructionParser` 自身直接查询 SQLite、写状态和创建 Task，会破坏现有纯解析边界。阶段 0 将 B 描述为“语义提取、结构校验；无数据库或发送”：
`contextflow_core/contracts/README.md:84-90`。

### 推荐最小改动

拆为：

```text
ParserWorker（编排）
  ├─ Message/ParseJob Repository
  ├─ Agent candidate source
  └─ InstructionParser
       └─ LlmExtractionPort
```

Receiver 不变，Worker 不连接 Bridge。

### 是否需要用户决策

**需要。** 解耦方向本身兼容，但阶段 0 把数据库读取、处理推进和恢复归给 E，而本轮初步范围把这些编排提前放进阶段 B。建议明确授权“阶段 B 包含 ParserWorker 编排，但 `InstructionParser` 仍保持纯解析”；随后再确认 Worker 是单实例还是多实例，因为后者决定第 3 项复杂度。

## 3.3 Message 状态／独立 parse 状态 — 有条件兼容

### 源码依据

ProcessingState 与 ParseStatus 是独立枚举：
`contextflow_core/contracts/models.py:32-41`。

Message processing_state 是全流程标记：
`contextflow_core/contracts/ports.py:34-52`。

### 现有能力

- pending/processed/blocked；
- 被动持久标记和 pending 查询；
- blocked 错误保存。

### 缺失能力

- ready/running/retry_wait/finalized；
- owner/lease/generation；
- next attempt 和计数；
- ParseResult 唯一持久记录。

### 风险

直接扩展 Message 状态会：

- 修改冻结公共枚举；
- 修改 SQL CHECK 和精确 DDL；
- 把 Parser 执行状态与 Message 整体状态混为一个真相源；
- 让 parsed 被误解为 processed；
- 污染 Agent report 使用的通用 Message 表。

### 推荐最小改动

新增独立 parse job 状态；Message 规则：

- 解析执行中和 parsed 已持久化：保持 `pending`；
- unresolved：同事务标 `blocked`；
- 最终 failed：同事务标 `blocked`；
- C 完成必要的下游持久步骤后，未来 E 才标 `processed`。

### 是否需要用户决策

**需要。** 这等于批准独立 parse 状态和推翻阶段 0 的“不持久化解析检查点”边界。

## 3.4 SQLite 原子领取 + Lease + Fencing — 有条件兼容

### 源码依据

已有 WAL 与短 `BEGIN IMMEDIATE`：
`contextflow_core/message_store.py:166-191,290-332`。

但 pending 查询只是 SELECT：
`contextflow_core/message_store.py:377-390`。

### 现有能力

- 同库原子写；
- SQLite 单写者仲裁；
- busy timeout；
- 两连接并发测试；
- 事务失败回滚和读回验证。

### 缺失能力

- job row；
- lease owner/expiry；
- generation token；
- fenced heartbeat/finalize；
- retry schedule；
- result唯一性。

### 风险

- 两个 Worker 同时从 list_pending 得到同一 Message；
- lease 只保护领取、却不保护 finalize；
- 旧 Worker 在过期后无条件 mark Message；
- LLM 期间持有写事务会阻塞 Receiver；
- busy wait 可能吞掉续租窗口；
- await 取消时 SQLite claim 可能已在后台成功。

### 推荐最小改动

- 独立作业表；
- 短事务领取；
- LLM 在事务外；
- 每次领取 `claim_generation += 1`；
- call-intent、finalize、heartbeat/renew 和 retry 更新均检查 `owner + generation + running + 未过期 lease`，同时确认 Message 仍为 pending；
- rowcount=0 即丢失所有权，旧 Worker 丢弃结果；
- lease 已过期时旧 Worker不得提交，即使尚未看到新 Worker。

### 是否需要用户决策

**需要确认是否支持多 Worker/自动 failover。**若只有单 Worker 且允许崩溃后重新扫描，Lease/Fencing 可以延后；若按当前初步方案要求可靠接管，则必须实现。

## 3.5 ParseResult 与最终状态同事务 — 有条件兼容

### 源码依据

`ParseResult` 是候选值；指纹不是持久化操作：
`contextflow_core/contracts/models.py:182-210`。

Message 标记是独立事务，无通用跨表 Unit of Work：
`contextflow_core/message_store.py:392-433`。

### 现有能力

- 同一 SQLite 文件和显式事务；
- 可重构出专用原子 finalize 方法；
- ErrorInfo 和 ParseResult 构造校验。

### 缺失能力

- ParseResult 表／codec；
- message_id 关联记录；
- result uniqueness；
- fenced finalize；
- v1→v2 migration。

### 风险

- 单独调用 `put_result()` 再调用 `mark_blocked()` 会留下半完成状态；
- 单独调用现有 mark 方法没有 fence；
- parsed 若标 Message processed，会跳过未来 C；
- 单独数据库文件无法与 Message 状态原子提交；
- 隐藏表而不升级 user_version 会造成 Schema 漂移。

### 推荐最小改动

同一 SQLite 事务：

- `parsed`：写入唯一正式结果并 finalize job；Message 保持 pending；
- `unresolved/failed`：写结果、finalize job、更新 Message blocked/error；
- 所有 owner 写入带 owner+generation+未过期 lease CAS，Message 更新也在同一事务中核验 pending 前态；
- commit 后才报告 finalize 成功。

### 是否需要用户决策

**需要。**这是对阶段 0 持久化边界和数据库版本的明确改变。

## 3.6 有界重试、无完成后重解析 — 有条件兼容

### 源码依据

阶段 0 允许 Task 批次提交前重新解析，但禁止混合候选；已有批次后不能重解析：
`contextflow_core/contracts/README.md:106-134`。

它同时声明 unresolved/failed 应 blocked，v1 不自动循环解析：
`contextflow_core/contracts/README.md:108-115`。

### 现有能力

- ErrorInfo.retryable；
- ParseStatus；
- fingerprint；
- 后续 C 的同 source/fingerprint 幂等边界。

### 缺失能力

- retry taxonomy；
- max attempts；
- next_attempt_at/backoff；
- 跨重启预算；
- LLM 调用意图与响应计数；
- Provider 层 SDK 自动重试认知。

### 兼容条件

允许自动重试：

- Provider 明确瞬态错误；
- 网络超时／响应未知（承认可能重复付费）；
- 模型输出结构无效（若预算允许）；
- SQLite busy 不消耗 LLM 预算。

不自动重试：

- `UNRESOLVED`；
- 已正式提交的 `FAILED`；
- Message 已 blocked；
- 已有 Task 批次；
- 完成后的模型／提示更新。

### 兼容性风险

- 超时后 Provider 可能已经处理请求；重试会重复解析或计费，不是 exactly-once。
- SDK 的隐式重试会使一次 `extract()` 对应多个网络请求；不能把 Worker 尝试数当作 Provider 调用数。
- 把临时错误立即写成最终 `failed` 再自动重新解析，会违反“finalized 后不再改结果”的边界。
- 重启后重新从零计数会使有界重试变成无限重试。
- 保存已有有效结果遇到 busy 时，不应立即再次调用 LLM，增加成本并丢弃可复用结果。

### 推荐最小改动

持久保存调用意图预算和 `next_attempt_at`，Worker 统一管理有限重试；Provider 层明确设置 SDK retry 上限，优先关闭隐藏重试或把其上限纳入预算。临时故障只更新作业 `last_error`，不提交正式 ParseResult；达到上限或遇不可重试错误才原子提交最终 `failed`。已有合法响应的 finalize 若暂时被锁，应在未失去租约的前提下重试同一结果保存，而不是再次调用 LLM；失去租约后放弃该结果。

### 是否需要用户决策

**需要确定可重试错误集合、跨重启上限及成本容忍度。**“完成”建议定义为正式 ParseResult 已原子持久化；阶段 C 仍另有 Task 批次提交点。

---

# 4. Proposed Minimal Architecture

以下是设计建议，不是已实现代码。

## 4.1 模块划分

### `InstructionParser`

职责：

- 接受 Message 原始正文及可信候选名称；
- 调用 `LlmExtractionPort`；
- 严格解析 raw object；
- 返回一个合法 ParseResult；
- 不读数据库、不领取、不生成 TaskId、不发送。

### `LlmExtractionPort` 实现

职责：

- 调用选定 Provider；
- 设置明确请求 timeout；
- 将 Provider 错误映射为可诊断错误；
- 对 SDK 内部自动重试有明确配置；
- 不承担 Worker job 状态。

当前项目没有 Anthropic/OpenAI 等实际 SDK代码或依赖声明；Provider 必须另行选择。若选择 Claude，应使用官方 Anthropic Python SDK，而不是自行拼 raw HTTP；但本报告不批准安装依赖或 API 调用。

### `ParserWorker`

职责：

- 轮询 due job；
- 原子领取；
- 在事务外调用 Parser；
- 根据 retry policy 调度重试；
- fenced finalize；
- 不创建 Task、不调用 Agent、不处理报告。

### 数据库 Schema 生命周期入口

由一个窄的、内部的 Schema 管理入口统一负责空 v0 建库、已知 v1→v2 迁移和 v2 校验。MessageStore 与 ParseJobRepository 均调用该入口，不能各自修改 `user_version`，也不需要引入 ORM 或通用迁移框架。原有 v1 DDL保留为迁移前验证依据。

### `ParseJobRepository`

职责：

- 通过统一 Schema 入口打开／验证数据库，不自行定义另一个版本生命周期；
- 对新 pending Supervisor Message 惰性补齐 ready job；
- claim、renew（若启用）、retry、finalize；
- final result 读取；
- 与 Message blocked 更新的同库事务；
- 不暴露任意 SQL transaction callback 给业务层。

### `AgentDirectory` 或候选源

职责：

- 提供可信 canonical target selectors；
- 对别名做精确反向索引；
- 0 个匹配为 unknown，多个匹配为 ambiguous；
- 不从 Endpoint Registry 或当前打开标签页推断 Agent。

B 仅使用候选；C 仍负责将目标解析为稳定 AgentId 并检查整批合法性。

## 4.2 数据流

```text
Receiver
  -> MessageStore.commit(Message pending)
  -> ack accepted

ParserWorker
  -> 短事务补齐 job：Supervisor kind + Message pending + 尚无 job
  -> COMMIT（也可与 claim 合并为同一个短事务）
  -> 调用前生成唯一 claim_request_id，保留在 Worker 内存
  -> 短事务 claim_next(owner, claim_request_id, lease_duration)
  -> COMMIT，返回 message_id + generation + owner + expiry
  -> 读取正文 + Agent candidate snapshot
  -> 短事务幂等记录该 generation 的 LLM call intent
  -> COMMIT
  -> 调用 LLM（无 SQLite 写事务）
  -> InstructionParser 严格验证 raw output
  -> 短 fenced transaction finalize
       - 写唯一 ParseResult
       - job = finalized
       - unresolved/failed 时 Message = blocked
       - parsed 时 Message 仍 pending
  -> COMMIT

未来 C
  -> 读取 finalized + parsed
  -> AgentDirectory.resolve 全批目标
  -> 原子创建 Task/Delivery/fingerprint
  -> Task 批次存在成为“已消费”权威事实
```

## 4.3 推荐作业状态机

| job_state | 含义 | 可进入路径 |
|---|---|---|
| `ready` | 可领取，尚无正式结果 | Repository 对新 pending Supervisor Message 惰性创建 |
| `running` | 某 owner/generation 持有未过期 lease | 原子 claim |
| `retry_wait` | 瞬态失败且预算未耗尽，等待 next_attempt_at | fenced retry schedule |
| `finalized` | 唯一正式 ParseResult 已提交 | fenced finalize |

不建议增加：

- completed 后人工 reparse；
- 多结果版本；
- “部分成功”；
- `consumed` 布尔字段；
- 与 Task 派发混合的状态。

## 4.4 推荐的最小表

逻辑结构示意：

```text
message_parse_jobs
- message_id                 PK, FK -> messages.message_id
- job_state                  ready/running/retry_wait/finalized
- claim_generation           monotonic integer
- lease_owner                nullable process instance id
- claim_request_id           nullable unique caller-generated claim identity
- lease_expires_at_ms        nullable UTC epoch
- next_attempt_at_ms         nullable UTC epoch
- llm_call_intent_count      nonnegative integer
- last_llm_call_generation   nullable generation with an authorized call intent
- last_error_json            nullable diagnostic ErrorInfo
- result_status              nullable parsed/unresolved/failed
- instructions_json          nullable ordered array
- result_error_json          nullable ErrorInfo
- intent_fingerprint         nullable 64 lowercase hex
- created_at
- updated_at
- finalized_at               nullable
```

推荐的数据库约束与 Repository 不变量：

1. `message_id` 主键／外键，每个 Message 最多一行；`claim_request_id` 非空时唯一，用于一次领取的幂等关联，不是解析版本。
2. generation 和调用意图计数非负；`last_llm_call_generation` 为 NULL 或不大于当前 generation。
3. 状态字段存在性按下表检查，非 running 清空 owner、claim_request_id 和 lease；`claim_generation` 保留，不能重置。
4. finalized+parsed：有非空有序 instructions、无 result_error、有 fingerprint。
5. finalized+unresolved/failed：instructions 是空数组、有 result_error、无 fingerprint。
6. 非 finalized 时，所有正式结果字段及 finalized_at 必须为空；最后一次临时错误只能放入 last_error。
7. kind 与 Message 状态是跨表不变量：lazy-create、claim、call-intent、renew、retry 和 finalize 在同一事务中核对来源是 Supervisor 且仍为 pending；blocked/processed 不能继续领取或被旧 Worker 覆盖。外键本身不能验证 kind。

| job_state | owner / claim_request_id / lease | next_attempt_at | 正式结果字段 | finalized_at |
|---|---|---|---|---|
| ready | 全 NULL | NULL | 全 NULL | NULL |
| running | 全部非空，generation > 0 | NULL | 全 NULL | NULL |
| retry_wait | 全 NULL | 必须非空 | 全 NULL | NULL |
| finalized | 全 NULL | NULL | 满足 ParseResult 组合 | 必须非空 |

上述是设计不变量，不是当前 Schema 已有的保障。未来实现需对 NULL 和非法组合做实际测试，不能只依赖枚举名或一条不完整 CHECK。

推荐索引覆盖：

```text
(job_state, next_attempt_at_ms, lease_expires_at_ms, message_id)
```

第一版不需要 append-only attempt 历史表。若以后需要审计每一次 Provider request ID、账单或 unknown 调用，再独立增加；不要为了未来需求提前实现。

## 4.5 领取和 Fencing

### 惰性创建／补齐

Receiver 仍只提交 messages，不写解析作业。ParserWorker 每轮通过 Repository 的短事务补齐满足以下条件的记录：`kind=supervisor_instruction`、`processing_state=pending`、尚无 parse job。使用 `message_id` 唯一约束确保两个 Worker 的补齐不会重复建作业；既有 pending 消息也通过同一路径回填。blocked/processed 消息不创建作业，已存在 job 不重置 generation 或重试预算。该操作可以合并到 claim 的短事务，不需要额外队列、消息事件或 messages trigger。

### Claim 的输入、返回与短事务

调用前 Worker 已知道：本次进程唯一 `owner`、每次领取调用新生成的 `claim_request_id`、lease_duration。Repository 在事务内选择 Message，返回 `message_id/generation/owner/lease_expires_at`；不要假定调用前已知道选中的 MessageId。

1. `BEGIN IMMEDIATE`；
2. 先查当前 owner/claim_request_id 的既有领取，防止同一次领取调用的不确定重试再次领取另一项；
3. 无既有领取时，选择 Message 仍为 pending 的 due ready/retry_wait，或 lease 已过期 running；
4. `claim_generation += 1`；
5. 写 owner、claim_request_id 和 lease_expires_at，清除旧 next_attempt_at；
6. 检查 UPDATE rowcount，读回完整 token；
7. COMMIT，返回 token。

取消 claim await 时，Worker 尚未拿到 MessageId/generation，只能用**调用前已知的 owner/claim_request_id**查询，再从已提交记录读回 token。SQLite worker 可能尚未完成，因此应先等待自己的在途 DB 操作结束或通过连接序列化确保查询在该操作之后。若仍未找到记录，或已被新 generation 接管，不调用 LLM，等待下一轮；不能用不存在的返回值恢复。

### 所有 owner 写入的统一 Fencing 条件

call-intent、heartbeat/renew、retry schedule 和 finalize 均必须原子检查：owner、generation、running、**lease_expires_at > now**，以及 Message 仍为 pending。所有日期比较使用一致的本机 UTC/数据库时间；持久 lease 不能只用跨进程无意义的 monotonic 值。系统时钟跳变可能改变接管时机，generation 与结果唯一性仍必须阻止旧提交；v1 不支持跨机器时钟协调。

过期 lease 不允许旧 owner 自行复活。需要继续工作时必须重新领取并获得新 generation；不因“尚未被新 Worker 领取”就放行。检查必须在获得写事务之后取当前时间并执行条件写，不能先在 Python 检查 lease，再调用无条件 mark。

### Finalize 短事务

条件至少包括：

```text
message_id = ?
job_state = running
lease_owner = ?
claim_generation = ?
lease_expires_at > now
result_status IS NULL
```

rowcount=0 表示所有权丢失；旧 Worker 丢弃返回结果，不得更新 Message。成功路径还必须清空 owner/claim_request_id/lease/next_attempt_at，填写 finalized_at，在同一事务内读回核验正式结果，并在 COMMIT 后报告成功。若业务响应丢失，查询已 finalized 结果进行幂等确认；不要重新调用 LLM。

### 是否需要 heartbeat

推荐第一版默认**不做 heartbeat**，前提是：

- Provider adapter 有硬 timeout；
- SDK 内部 retry 上限已知；
- lease 时长大于最坏请求时间 + 本地调度／busy／finalize 余量。

若 Provider 调用不能被可靠限时，或单次流式请求可能超过 lease，则需要 fenced heartbeat。heartbeat 间隔必须显著小于 lease，并考虑 SQLite busy timeout。该选择依赖 Provider，当前证据不足以确定具体时长，但不影响架构判断。

## 4.6 尝试次数和 LLM 调用次数

不能把 claim_generation 当作 LLM 次数。

| 计数 | 定义 |
|---|---|
| `claim_generation` | 每次成功领取/重新领取 +1；也是 fencing token，不等于 LLM 次数 |
| `llm_call_intent_count` | 准备发起 Provider 调用前，在未过期 lease 的 fenced 短事务中 +1；跨重启保留 |
| `last_llm_call_generation` | 同一 generation 的调用授权幂等标记，重复登记不得再次加预算或授权另一次调用 |
| observed response | 可作为日志／未来历史记录；不等于 Provider 实际计费次数 |

第一版每个 generation 最多授权一次 `extract()`；retry schedule 释放领取，下次尝试重新领取后获得新 generation。若 call-intent 的 await 被取消、无法确认是否已经授权，读取 last_llm_call_generation 可以避免双计数，但**不能据此证明远程请求是否已经发出**；该 generation 不再发起第二次请求，释放或等待 lease 后按下一次尝试处理。

崩溃可能发生在“调用意图 commit”与“请求真正发出”之间，所以 `llm_call_intent_count` 是保守预算，不是精确网络调用计数。反过来，timeout 后 Provider 可能已经完成，因此重试可能多调用一次。

## 4.7 崩溃恢复

- running lease 未过期：其他 Worker 不领取；
- lease 到期：新 Worker generation+1 接管；
- 若调用意图预算已经耗尽、上一 Worker 未留下正式结果，则新 owner 在有效 lease 内提交最终 failed；不再发起 LLM。领取次数不作为调用预算，但可设置独立的调度退避防止持续崩溃造成忙循环；
- 旧响应晚到：fence 拒绝；
- finalized：任何 Worker 不再调用 LLM；
- parsed finalized、C 尚无 Task 批次：未来 C 可消费；
- Task 批次已存在：未来 E/C 恢复后续步骤，不重解析；
- unresolved/failed：Message blocked，不自动重新解析。

---

# 5. Schema and Contract Impact

## 5.1 Schema 影响

### 推荐：共享数据库显式升级 v2

不修改 `messages` DDL，只新增：

- `message_parse_jobs`
- due job 索引
- `PRAGMA user_version = 2`

统一 Schema 生命周期入口处理三条路径：

1. **空 v0 新库**：确认不存在用户对象，在单事务中创建原 messages 结构和解析表/索引，验证后设置 user_version=2 并提交。
2. **已知 v1 库**：先验证真实 v1，再通过下面的短迁移事务升级。
3. **已知 v2 库**：只校验，不重复迁移。未知版本或不符合定义的数据库拒绝，不能接管。

已知 v1→v2 迁移：

1. 升级前由操作者停止旧 Receiver/Worker，并完成 SQLite 一致性备份；本轮不执行这些操作；
2. 在持久化 PRAGMA 或 DDL 变更前只读验证真实 v1；
3. `BEGIN IMMEDIATE`；
4. 再次验证版本和 owned Schema，避免并发首次迁移；若其他进程已完成 v2，则验证并复用 v2；
5. 创建解析表／索引，不改写消息或接受幂等身份；
6. 设置 user_version=2 并完整校验；
7. COMMIT；
8. 任一步失败全部 rollback，保留可重新打开的 v1。新的 v2 创建失败也不得留下半成品版本标记。

现有 `_initialize()` 不能迁移，未来 MessageStore 和 ParseJobRepository 应调用同一个小型 Schema 入口，避免各自提升版本。保留 v1 DDL作迁移前验证，不要求构建通用迁移框架。
**[源码已实现]** `contextflow_core/message_store.py:166-213` 是上述建议必须适配的现有初始化边界。

### 不推荐方案

- 在 `user_version=1` 下偷偷建表：未版本化、未校验；
- 单独 parse SQLite 文件：不能与 Message blocked 更新同事务；
- 在 messages 上加 trigger：当前 Store明确拒绝 trigger；
- 直接更改现有 `_SCHEMA`：会使真实 v1 在迁移前无法可信验证。

### 对阶段 A 的兼容风险

- 旧阶段 A 二进制重新打开时会拒绝 v2；但已打开旧连接不会重新检查版本，迁移前必须停止旧进程，不能依赖 Schema version 自动阻止混跑；
- 迁移时数据库需要短写锁；Receiver 可能暂时遇到 busy；
- 运行中数据库含 WAL，备份和回滚不能只复制主文件；
- 手动验收数据库和用户数据不得在未授权时迁移；
- A 的首次/重复/冲突、完整 Envelope 和处理状态行为必须保持测试通过。

## 5.2 Message 状态影响

推荐不扩展 `ProcessingState`，不改 Message DTO。

终态映射：

| ParseResult | parse job | Message processing_state |
|---|---|---|
| parsed | finalized | pending |
| unresolved | finalized | blocked + ErrorInfo |
| failed（重试耗尽／不可重试） | finalized | blocked + ErrorInfo |

parsed 保持 pending 是为了避免把“解析完成”误写为“所有下游步骤完成”。Task 批次即使已提交，Message 暂时仍可能 pending；阶段 0 已明确允许这个恢复窗口。
**[测试已覆盖]** `tests/contracts/test_models.py:294-305`（仅验证契约组合，非 Task 批次持久化）。

## 5.3 ParseResult 契约影响

现有形状可以保持不变：

- 不添加 source_message_id；
- 不添加 parse_id；
- 不添加 consumed；
- 不添加版本字段；
- 不添加 TaskId/AgentId。

需要新增的是持久化包装／Repository，例如概念上：

```text
StoredParseResult
- source_message_id: MessageId
- result: ParseResult
- finalized_at: UTC
```

如果只在 B 内部使用，可保持私有类型。如果 C 必须跨模块读取，建议新增窄的只读 Port，例如：

```text
get_final_result(message_id) -> ParseResult | None
```

这应作为**新增阶段公共契约**单独审批，而不是修改原 `ParseResult`。

## 5.4 严格编解码

`ParseResult` 当前没有 `from_dict/to_dict`。B 持久化需要显式内部 codec：

- exact fields；
- 枚举解析；
- ordered instructions；
- ErrorInfo 结构；
- 构造 `ParsedInstruction/ParseResult` 触发阶段 0 校验；
- 读取时重算 fingerprint；
- 禁止把模型原始 JSON 直接当作已验证 ParseResult。

是否把 codec 提升为公共方法属于冻结契约决策；最小实现无需这样做。

## 5.5 Agent 身份影响

现有 `ParsedInstruction.target_agent: str` 无需修改。阶段 B 的最小补充应是候选来源，而非把目标改成 AgentId。

如果采用配置驱动 AgentDirectory，配置至少需要：

- stable agent_id；
- unique canonical selector；
- exact aliases；
- 可选 availability source；
- Gateway route 保留给 C 私有实现。

别名反向索引：

- 0 个匹配：unknown；
- 1 个：唯一 Agent；
- 多个：AGENT_AMBIGUOUS；
- 不做相似度猜测或自动替代。

第一版应把 canonical selector 与 stable AgentId 的绑定视为不可重新分配的配置身份；改名可以增加别名，但不能把同一已保存选择符悄悄映射到另一个 Agent。否则 B 保存文本目标到 C 提交之间的目录变化仍可能改变意图，ParseResult 指纹不会发现这种变化，因为其中不包含 AgentId。若需要动态重新分配名称，则必须另行设计候选快照／版本和 C 的一致性复核；本轮不实现。

B 可以把 canonical selectors 给 LLM；C 仍必须在 Task 提交前重新 resolve，未知或歧义时整批拒绝。

## 5.6 阶段 C 边界

### ParseResult 是否足够

作为 C 输入值足够：`source_message_id + ParseResult` 已是当前接口。
**[接口契约]** `contextflow_core/contracts/ports.py:136-147`

### Parser 是否生成 TaskId

不生成。TaskId、稳定 AgentId 和 TaskDelivery 都由 C 负责。
**[类型契约]** `contextflow_core/contracts/models.py:213-298`；**[文档中声明]** `contextflow_core/contracts/README.md:35-42`。

### C 如何拒绝 unresolved/failed

C 运行时必须只接受 `ParseStatus.PARSED`，并验证非空 fingerprint。当前 Protocol 的 Python 类型仍允许传任意 ParseResult，因此需要 C 实现和测试该前置条件；阶段 0 DTO 只保证状态内部组合。

### 如何防止部分结果

- B 的 unresolved/failed 构造不允许 instructions；
- C 必须先解析全部目标；
- 任一未知/歧义，整批 rejected，不建立子集；
- Task/Delivery/fingerprint 原子提交。

### 不可变性与唯一性

- 内存对象已 frozen；
- DB 以 `message_id` 主键确保一个正式解析记录；
- finalize 只允许 `result_status IS NULL`；
- 完成后不提供 reparse API；
- fingerprint 不是全局唯一键。

### 如何识别未消费

不要添加 `ParseResult.consumed`。未来 C 应以：

```text
finalized parsed result + TaskQuery.get_tasks_for_message(message_id) == ()
```

识别未提交候选。已有完整 Task 批次就是权威“已消费”；避免 C commit 成功、consumed 标记失败造成双真相源。
**[接口契约]** `contextflow_core/contracts/ports.py:104-115,219-240`

---

# 6. Failure Scenarios

| 场景 | 当前基础设施 | 推荐预期行为 |
|---|---|---|
| 两 Worker 同时领取同一 Message | 当前 list_pending 会重复返回 | BEGIN IMMEDIATE + 条件 UPDATE；只有一个 generation 获得所有权 |
| Worker 领取后立即崩溃 | 当前 Message 会继续 pending，无 owner | lease 到期后新 generation 接管；未记录 LLM call 时不消耗调用预算；领取循环仍需退避，不能承诺故障总次数有界 |
| claim await 被取消 | SQLite worker 仍可能 commit | 用调用前已知 owner/claim_request_id 查询；等待在途 DB 操作结束后读回 MessageId/generation，不假定已取得取消调用的返回值 |
| LLM 请求 timeout，但服务端可能完成 | 当前无 Provider/尝试记录 | 记为结果未知；有界重试，承认重复调用／计费可能 |
| Worker lease 过期后旧响应返回 | 当前无 fence | finalize CAS 失败，旧结果丢弃 |
| heartbeat 因 DB busy 失败 | 当前无 heartbeat | Worker 不能假定仍拥有 lease；重新验证 fence；必要时丢弃响应 |
| ParseResult INSERT 失败 | 当前无结果表 | 同事务 rollback；job 保持 running 或等 lease 接管，不更新 Message |
| ParseResult 成功、Message 状态更新失败 | 分开调用会产生半状态 | unresolved/failed 的 result+job+Message block 在同一事务 |
| parsed result 提交后崩溃 | 当前无结果持久化 | finalized 结果保留；重启不得再调 LLM，未来 C 继续 |
| commit 前崩溃 | SQLite 回滚 | lease 到期后重试；没有正式结果 |
| commit 后、Worker 未观察成功就崩溃 | SQLite 已持久 | 重启读取 finalized，不重复提交；与 Receiver ack 丢失语义相同 |
| 重试次数耗尽 | 当前无预算 | 构造最终 failed，保存 ErrorInfo，Message blocked，不自动重解析 |
| semantic unresolved | 现有契约支持 | 立即 finalized unresolved + Message blocked；不浪费 transient retry |
| 模型输出 JSON 结构非法 | 现有 Llm Port 只返回 object | B 严格拒绝；预算允许时作为 transient parse-output failure 重试 |
| DB 暂时锁定 | 现有 Store 有 busy timeout | claim/finalize 调度重试；不增加 LLM call intent；不返回成功 |
| DB rollback 失败 | 现有 Store会关闭连接 | 作业 Repository同样 fail closed；不得继续复用连接 |
| v1→v2 迁移失败 | 当前无迁移 | 单事务 rollback，保留完整 v1；不能半升级 user_version |
| 多次解析输出不同 | 提交前现有契约允许候选变化 | 只有 fenced finalized 结果成为 B 正式结果；不拼接／追加 |
| C 已有 Task 批次、Message 仍 pending | 阶段 0明确存在该窗口 | 未来 E/C 先查批次，绝不重解析 |
| Agent 已知但离线 | AgentDirectory 仅有接口 | B 可识别名称；C 将合法性与可用性分开，不自动替代 |
| 目标名称未知或歧义 | 无真实 Directory | B 可 unresolved；C 必须再次 resolve 并整批拒绝 |

---

# 7. Open Decisions

## 决策 0（范围门槛）：阶段 B 是否包含 ParserWorker 编排

### 背景

阶段 0 的 `InstructionParser` 是无数据库的纯解析 Port，Message 读取、处理推进和恢复原属 E；本轮初步方案则要求 Worker 从 SQLite 领取并保存结果。

### 选项

A. 阶段 B 只实现 Parser/Provider，Worker 留到 E。
B. 阶段 B 同时实现独立 ParserWorker、作业持久化与恢复，但保持 `InstructionParser` 本身纯净。

### 推荐

**B。** 这与本轮阶段 B 目标一致，也能真正验证从持久 Message 到持久 ParseResult；必须把它记录为有意识的职责范围前移，而不是声称阶段 0 已经这样定义。

### 不决策会阻碍

无法判断第 3、4 项可靠性机制属于本轮必做还是应推迟到 E。

## 决策 1（最高）：是否正式持久化 ParseResult

### 背景

阶段 0 明确不持久化；本轮目标要求可靠保存并恢复。

### 选项

A. 维持阶段 0：ParseResult 仅内存候选，C Task 批次是第一个持久确认点。
B. 新增单一正式 ParseResult 记录，不保留版本历史。
C. 保存每次尝试和多版本结果。

### 推荐

**B。** 它满足本轮阶段 B 目标，又不引入人工 reparse 和复杂版本管理。

### 理由

- Worker 重启后不会因重新调用 LLM产生不同正式结果；
- C 可读取稳定输入；
- 一行每 Message 足以；
- 多尝试审计可延后。

### 不决策会阻碍

Schema、恢复状态机、重试边界和 C 读取接口都无法定稿。

## 决策 2：Schema v2 迁移

### 背景

当前 MessageStore只接受 v0空库/v1，没有迁移路径。

### 选项

A. 同库 `user_version=2`，显式 v1→v2 迁移。
B. user_version仍为1，偷偷增加表。
C. 解析结果使用独立数据库。

### 推荐

**A。**

### 理由

- 同事务更新作业、结果和 Message blocked；
- Schema 可验证、可回滚；
- 不产生隐藏漂移。

### 不决策会阻碍

不能可靠实现结果/状态原子提交。数据库迁移属于需单独授权的高影响操作；下一轮实现授权必须明确包含它，且不得自动迁移用户数据库进行测试。

## 决策 3：Message 与 Parser 状态关系

### 选项

A. 在 `ProcessingState` 增加 parsing/retry/parsed。
B. 保持 Message 枚举不变，使用独立 job_state。

### 推荐

**B。**

### 理由

- 避免解析完成冒充全流程 processed；
- 避免污染 Agent report 的通用状态；
- 不修改冻结 Message DTO和 messages CHECK；
- Lease 是运行状态，不应写入不可变消息语义。

### 不决策会阻碍

作业表 CHECK、终态映射和 C 消费门槛无法确定。

## 决策 4：可信 Agent 候选来源

### 选项

A. 仅传手工 tuple[str]，B/C 各自维护映射。
B. 增加最小配置驱动 AgentDirectory，B/C 共享 canonical names/aliases/AgentId。
C. 从 Endpoint Registry 或在线标签页推断。

### 推荐

**B；明确拒绝 C。**

### 理由

- 避免两个阶段名称映射不一致；
- 复用现有 AgentDirectory Port；
- 不需要 Project/Role；
- Endpoint 和 Agent 身份本来就是不同域。

### 不决策会阻碍

真实风格测试无法定义“Claude Code”“Codex”等名字的权威含义；B 只能做结构解析，不能获得可信候选。

## 决策 5：Worker 拓扑与 Lease/Heartbeat

### 选项

A. 单 Worker，不做 Lease/Fencing；崩溃后重启扫描。
B. 支持多个 Worker/自动接管，固定 lease + generation；Provider 有硬 timeout，第一版无 heartbeat。
C. 多 Worker + heartbeat 续租。

### 推荐

**B**，前提是 Provider 请求可以设置可预测的硬 timeout；否则选择 C。

### 理由

- 保留初步方案的可靠接管；
- generation 能阻止旧结果；
- 不先引入高频 heartbeat 写入；
- 单 Core 可以只运行一个 Worker，但 schema 仍可安全接管。

### 不决策会阻碍

claim 字段、测试矩阵和 timeout/lease 参数无法定稿。

## 决策 6：Provider、SDK 与重试预算

### 背景

仓库当前没有真实 LLM SDK、Provider adapter 或依赖清单；`LlmExtractionPort` 只是接口。

### 需要确认

- Provider 和模型；
- 凭据配置方式；
- API timeout；
- SDK 内部 max retries；
- ContextFlow Worker 的最大 call intent 次数和退避；
- 是否接受 timeout 后潜在重复计费。

### 推荐

保持 `LlmExtractionPort` provider-neutral，但下一轮只实现一个明确 Provider adapter和一个 Fake。使用该 Provider 的官方 SDK，凭据只从环境／项目配置读取；LLM 实际调用和可能费用必须单独授权。

### 不决策会阻碍

无法实现真实 Parser、确定 lease 时长或测试错误映射。

## 决策 7：B/C 只读结果接口

### 选项

A. C 直接访问 B 的表。
B. 新增窄 `ParseResultReader`／Repository Port。
C. 修改 ParseResult 增加 MessageId/consumed。

### 推荐

**B。**保留现有 ParseResult 不变；Reader 以 MessageId 返回最终结果。是否现在将它加入冻结公共导出，需用户明确批准；也可以先作为 B 内部 Protocol，在 C 设计时再冻结。

### 不决策会阻碍

不阻碍 B 内部实现，但会影响阶段 C 是否依赖私有 SQLite 表。

---

# 8. Suggested Stage B Implementation Scope

以下仅是建议范围，不是开发授权或正式开发提示词。

## 8.1 必须实现

1. **确认上述架构决策和迁移授权。**
2. **Schema v2 管理**
   - v1严格验证；
   - 原子 v1→v2；
   - v2严格验证；
   - 失败回滚、并发迁移和旧数据保留测试。
3. **Parse job/result Repository**
   - lazy create/ready；
   - atomic claim；
   - generation/owner/lease；
   - retry_wait；
   - fenced finalize；
   - final result读取；
   - unresolved/failed 与 Message blocked 同事务。
4. **严格 ParseResult codec**
   - raw LLM object exact validation；
   - ordered instructions；
   - ErrorInfo；
   - fingerprint 重算；
   - 不保存部分结果。
5. **InstructionParser 实现**
   - 无默认 Agent；
   - parsed/unresolved/failed 清晰分类；
   - 多目标真实风格样本；
   - 不生成 AgentId/TaskId。
6. **一个明确 LLM Provider adapter + Fake**
   - 官方 SDK；
   - 凭据配置；
   - timeout/error mapping；
   - SDK retry 与 Worker retry 分离。
7. **可信 Agent candidate source**
   - 根据决策 4 实现最小共享目录或经批准的临时配置；
   - 不使用 Endpoint Registry 推断 Agent。
8. **ParserWorker**
   - polling/backoff；
   - claim 后事务外 LLM；
   - 有界重试；
   - 正常关闭／取消；
   - lease 恢复；
   - 不连接 Bridge、不创建 Task。
9. **故障测试**
   - 双 Worker 同时 claim；
   - claim 后崩溃；
   - lease 过期旧响应；
   - timeout 结果未知；
   - finalization busy/write/commit failure；
   - retry exhausted；
   - v1→v2 migration failure；
   - finalized 不重解析；
   - A 全回归继续通过。

## 8.2 可以延后

- heartbeat（若硬 timeout 可覆盖）；
- 每次尝试 append-only 历史表；
- Provider usage/cost 精确对账；
- 人工 reparse；
- Parser/模型版本选择 UI；
- Agent 候选展示 DTO；
- WAL checkpoint/运维工具；
- 多 Core 协调。

## 8.3 明确不属于阶段 B

- Task/TaskDelivery 创建；
- TaskId 生成；
- AgentGateway 投递；
- Agent 执行；
- Agent Report Intake；
- ReportDelivery；
- 完整 Project/Role；
- 完整 Dispatcher；
- 完成后人工重新解析和多版本管理；
- 管理前端。

## 8.4 阶段 B 完成标准建议

阶段 B 完成时应能证明：

1. 已持久化 Supervisor Message 可被单个 Worker 原子领取；
2. 双 Worker 不会同时提交两个正式结果；
3. 过期 Worker不能覆盖新 generation；
4. LLM 在 SQLite 写事务外调用；
5. raw 输出严格构造成 ParseResult；
6. 正式结果和 job final state 原子提交；
7. unresolved/failed 不携带部分指令；
8. parsed 后 Message 仍等待 C；
9. 崩溃重启不会重写已 finalized 结果；
10. 不宣称 LLM exactly-once；
11. 阶段 A 的接收、幂等和数据库恢复行为不回归；
12. 没有 Task 创建或 Agent 发送。

---

# 9. 勘察验证与工作区

## 9.1 检查过的关键实现

- `contextflow_core/message_store.py`
- `contextflow_core/receiver.py`
- `contextflow_core/registry.py`
- `contextflow_core/service.py`
- `contextflow_core/endpoint_identity.py`
- `contextflow_core/models.py`
- `contextflow_core/contracts/models.py`
- `contextflow_core/contracts/ports.py`
- `contextflow_core/contracts/ids.py`
- `contextflow_core/contracts/_validation.py`
- `contextflow_core/contracts/README.md`
- `tests/core/test_message_store.py`
- `tests/core/test_receiver.py`
- `tests/core/test_registry.py`
- `tests/core/test_service.py`
- `tests/contracts/test_models.py`
- `tests/contracts/test_ports.py`
- `tests/contracts/test_edge_cases.py`
- `reports/CF-CORE-003.md`

## 9.2 执行的测试

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/core/test_message_store.py \
  tests/core/test_receiver.py \
  tests/core/test_registry.py \
  tests/core/test_service.py \
  tests/contracts/test_models.py \
  tests/contracts/test_ports.py \
  -q -p no:cacheprovider
```

**[本轮命令实测]** 最后一次复验结果：

```text
140 passed, 93 subtests passed in 17.58s
```

这些是现有隔离测试；未调用真实 LLM，未迁移或写入用户数据库。此前同一测试集合也通过，最后结果以此次复验为准。

## 9.3 工作区基线与未触碰内容

勘察开始时已有未跟踪文件：

- `_artifacts/cfcore003-manual.sqlite3`
- `_artifacts/cfcore003-manual.sqlite3-shm`
- `_artifacts/cfcore003-manual.sqlite3-wal`
- 根目录三份用户设计文档，其中 `消息拆解和分发预备11.7.md` 当前为空

本轮未读取、修改或迁移手动数据库，也未修改这些用户文档。测试使用临时数据库且禁用 pytest cache/字节码。

本报告 `reports/CF-CORE-004-B-investigation.md` 是唯一预期仓库变更；未提交、未推送、未合并。

---

# 10. Final Assessment

当前 ContextFlow 已经具备阶段 B 的可靠输入来源、SQLite 事务基础和足够的 ParseResult 值契约。真正阻碍开发的不是“如何让 LLM 返回两个字符串”，而是三个边界尚未批准：

1. 是否正式持久化 ParseResult并迁移 Schema v2；
2. 是否为 Worker引入独立 job state、lease 和 fencing；
3. Agent 候选的权威来源是什么。

在这些问题确认之前直接实现，最可能产生的错误是：把 Message pending 当作领取锁、把 parsed 当作 processed、从 Endpoint 推断 Agent、或在未版本化数据库中偷偷建表。

建议 Supervisor 与用户先确认第 7 节的决策 0–6，并决定 C 读取接口是否在本阶段冻结（决策 7），再授权阶段 B。确认后，下一轮应按第 8 节做最小实现，而不是重新设计整个 Core。
