# CF-CORE-003：阶段 A — SQLite Message Store 与 Core Receiver

日期：2026-11-06。基线：`08462a9`。分支：`feat/cf-core-003-message-store`。

## Supervisor Summary

**阶段 A 的编码与自动化验收已完成：正式 Receiver 已能够经现有 Bridge 将 Supervisor 消息校验并保存到 SQLite。真实 ChatGPT Extension 到 SQLite 的人工/真实网页验收仍未完成。**

- 实现 SQLite MessageStore、稳定 Endpoint 来源核验、正式 Receiver CLI、幂等确认和数据库恢复查询。
- 保留完整 Envelope、原生 Session/Message 信息和 Core Message ID，不引入 Project/Role 或新身份模型。
- 首次、重复、冲突、并发、插入/commit/回滚失败、Schema 损坏、锁超时、确认丢失与重启有针对性测试。
- Bridge 使用单外部连接、接收连接/页面归属和既有 request_id 的尝试关联；没有新握手、注册消息、Envelope 字段或冻结接口修改。
- 最终完整回归：**208 passed、145 subtests passed**；标准库契约测试：**62 tests，OK**。
- 没有安装依赖、推送、合并 main、迁移用户数据库、重启已有服务、修改浏览器配置或向 ChatGPT 发布新文本。

当前不实现 Parser、Task Dispatcher、Agent 自动执行/报告 Intake、ReportDelivery Runtime 或完整 Project/Role。

## Technical Details

### 1. 仓库检查与复用

仓库没有独立的 CF-CORE-002-0 报告文件。本阶段参考此前对话报告、`contracts/README.md`、实际源码及测试。实施前基线为 128 passed、132 subtests passed。

复用：

- `SupervisorEnvelope.from_dict()` 及既有 `validate_envelope()`。
- `canonical_envelope()`、`envelope_fingerprint()` 的规范化 JSON/SHA-256。
- `Message`、ID、状态枚举、结果 DTO 和冻结 Port。
- `EndpointRegistry`、默认配置路径及旧绑定格式。
- 既有 submission/submission_ack、Bridge `/ws` 与 `/ws_page`。
- 既有 Bridge 协议测试 harness 和固定测试样例。

未修改 `contracts` 生产代码、旧 Adapter、Registry 或 Supervisor Envelope v1。阶段 0 的导入隔离测试改为在独立 Python 进程导入契约，保留原来的“不加载业务模块”断言，不让完整测试集合的其他导入影响它。

### 2. 文件与职责

所有路径相对 `contextflow-research`。

| 文件 | 职责 |
|---|---|
| `contextflow_core/message_store.py` | Schema、持久化、并发幂等、读取恢复、被动处理标记、存储核验 |
| `contextflow_core/endpoint_identity.py` | 复用 Registry 读取当前持久身份，不探测在线状态 |
| `contextflow_core/receiver.py` | Supervisor 接收、旧 ack 映射、尝试关联、配置与 CLI 生命周期 |
| `contextflow-extension/bridge_server.py` | 最小连接互斥和确认归属调整；Extension JS 不变 |
| `tests/core/test_message_store.py` | 真实 SQLite 的成功、竞争、故障、Schema、取消和恢复测试 |
| `tests/core/test_endpoint_identity.py` | 读取、刷新、未知及损坏 Registry 测试 |
| `tests/core/test_receiver.py` | 格式、结果映射、取消/确认超时、URI 与敏感信息测试 |
| `tests/adapter/test_core_receiver_integration.py` | 真 Bridge/Store/Receiver/CLI 与 fake-page 的集成 |
| `tests/adapter/test_bridge_protocol.py` | 保留旧协议测试，提取可复用 harness、隔离测试 token |
| `tests/contracts/test_ports.py` | 独立进程检查契约导入隔离 |
| `docs/CF-CORE-003-usage.md` | 启动、配置、查询、真实网页人工验收与限制 |
| `docs/CF-CORE-003-implementation-plan.md` | 阶段范围、设计和验收清单 |

### 3. SQLite Schema

使用文件数据库，`PRAGMA user_version=1`、WAL、`synchronous=FULL`、`foreign_keys=ON`。不允许内存/URI 数据库冒充持久 Message Store。

`messages` 表：

| 列 | 类型 | 含义 |
|---|---|---|
| `message_id` | TEXT PRIMARY KEY NOT NULL | Core `cfmsg_` + 32 位小写十六进制 |
| `kind` | TEXT NOT NULL | supervisor_instruction / agent_report |
| `submission_id` | TEXT | Supervisor 逻辑提交 ID；Agent 行为 NULL |
| `source_agent_id` | TEXT | 已验证内部 Agent 输入来源；Supervisor 行为 NULL |
| `report_id` | TEXT | 内部 Agent 报告幂等 ID；Supervisor 行为 NULL |
| `endpoint_id` | TEXT | Supervisor 稳定端点 |
| `native_session_id` | TEXT | Supervisor 原生会话 |
| `native_message_id` | TEXT | Envelope.source.message_id，不是 Core Message ID |
| `envelope_json` | TEXT NOT NULL | 完整规范化 Envelope 字段及正文 |
| `fingerprint` | TEXT NOT NULL | 既有 SHA-256，64 位小写十六进制 |
| `received_at` | TEXT NOT NULL | UTC ISO 接收时间 |
| `processing_state` | TEXT NOT NULL | pending / processed / blocked |
| `processing_error_json` | TEXT | 完整 ErrorInfo 或 NULL |

CHECK 约束覆盖 Core ID/指纹格式、消息种类、处理状态/错误组合、Supervisor 与 Agent 身份列的互斥组合。

索引：

```sql
CREATE UNIQUE INDEX messages_supervisor_submission_unique
ON messages(submission_id) WHERE kind = 'supervisor_instruction';

CREATE UNIQUE INDEX messages_agent_report_unique
ON messages(source_agent_id, report_id) WHERE kind = 'agent_report';

CREATE INDEX messages_pending
ON messages(kind, processing_state, received_at, message_id);
```

Agent 行是现有 MessageStore Port 所要求的通用内部存储，不是新增外部 Agent Intake；本阶段没有认证、Task 查询、final 历史更新或报告回传。

初始化先只读检查版本与 schema 对象。空、未版本化库才能建表；未知版本或非空未知库拒绝。重开 v1 时检查本模块拥有的表/索引定义和 messages 触发器，不能只凭列名就接受缺失约束的数据库。检查在持久化 PRAGMA 变化之前进行，事务内再次检查竞争条件，不自动迁移或覆盖。

### 4. 序列化与恢复

Supervisor 输入通过阶段 0 包装和旧校验器规范化，再调用既有 canonical/fingerprint 函数。没有新的 Supervisor 指纹算法。

- JSON 键排序和紧凑序列化用于稳定比较；保留所有字段值和正文，不保存原 WS 空白/键顺序。
- `user_message_id`、`turn_group_id`、adapter、format 等均保存在完整 Envelope 中，不因独立列没有列出而删除。
- 新消息分配合法 Core MessageId，初始状态 pending；Core MessageId 与原生消息 ID 分离。
- 读取时重新构造 Envelope、ID、Message、状态和 ErrorInfo，核验完整 Envelope 指纹及冗余来源列；损坏返回 MESSAGE_STORE_CORRUPT。
- 闭库重开保留原 MessageId、正文、接收时间、状态和重复提交身份。
- v1 允许的孤立 surrogate 无法进入既有 UTF-8 指纹/SQLite 表示时，返回 INVALID_ENVELOPE_ENCODING，不改变冻结 v1 校验器或伪造 accepted。

### 5. 消息接收与事务

1. 现有 Extension 通过 Bridge 页面发送 adapter_submission。
2. Bridge 登记尝试与连接关联，转发既有 submission。
3. Receiver 仅处理 Supervisor submission，调用 MessageStore。
4. Store 校验 Envelope，并在写入前查询稳定 Endpoint 身份。
5. SQLite worker 获得该连接互斥锁，执行 BEGIN IMMEDIATE。
6. 按唯一提交身份查询既有、已提交记录。相同完整 Envelope 返回原 MessageId；不同返回 conflict，不覆盖。
7. 新输入生成 MessageId，参数绑定 INSERT，检查影响行数并读回预期 Message。只有确实写入且 COMMIT 成功，才返回 accepted/duplicate=false。
8. Receiver 输出旧 accepted/duplicate/conflict/rejected 语义，Core MessageId 留在内部查询/日志。
9. Bridge 核验接收连接、页面及 request_id，再把确认交给当前页面。

同 Envelope 的重复结果同样经过已有记录的契约/指纹核验，并返回原 MessageId 与接收时间。不同 submission_id 即使正文相同也分别保存。

数据库唯一索引是持久层约束，BEGIN IMMEDIATE 在查找前获得写事务；不依赖未加锁的 Python “先查再插”。不同连接的竞争由 SQLite 仲裁，同一 Store 连接由互斥锁序列化。异步方法将 SQLite 调用放到标准库线程执行，不用 ORM 或新的异步数据库依赖。

插入/commit 失败会回滚并拒绝。触发器静默忽略或删除 INSERT、忽略 UPDATE 也不能伪造成功；写入后检查实际结果。回滚自身失败时保留操作及回滚异常类别，禁用/关闭该连接，不泄漏原始异常作为成功结果。

`mark_processed/mark_blocked` 只被动更新调用方请求的处理标记；不会调用 Parser、创建 Task 或执行恢复工作流。

### 6. 来源核验

RegistryEndpointIdentityReader 每次复用 EndpointRegistry 读取持久文件，避免 Reader 创建后新绑定不可见。

Supervisor 接受前检查：

- Endpoint 存在。
- Reader 返回的 EndpointId 就是所查询的身份。
- Envelope.adapter 与 binding.provider 一致。
- Envelope.native_session_id 与 binding.native_session_id 一致。

不调用 discover/resolve/read，不查询 tab_id、available/offline/ambiguous，不因会话离线删除绑定或更换目标。

这是稳定来源一致性，不是账号认证、消息原生 ID 真实性证明、Supervisor 身份认证或任务语义审查。Registry 文件也没有与 SQLite 合并为分布式事务；v1 使用单 Core 的稳定绑定设计。

### 7. Bridge 与确认兼容

- `/ws` 强制一个外部连接；第二个连接关闭 code 4409，不抢占。
- 在 await accept 前预留连接身份，使用 UUID 避免时间戳 ID 碰撞；异常和断线清理只释放自己拥有的槽位。
- 投递记录关联实际接收连接与来源页面，旧连接/旧页面 ack 不能确认新关联。
- 不属于当前接收连接的旧记录不再阻挡新 Receiver 做权威判断。
- 同 ID 在途 Envelope 变化拒绝，避免第一次内容的确认落到第二次内容上。
- 为保留丢失 ack 后的原 Envelope 重发，正式 Receiver 回显已存在于协议中的 request_id；Bridge 拒绝不匹配当前尝试的迟到确认。没有新 type、握手、注册消息或 Envelope 字段。
- 旧 Mock 不带 request_id 仍可独占连接按原方式运行；不能把其 SID-only ack 当作正式尝试关联保障。

运行影响：旧 Endpoint/RPC 调试客户端不能与 Receiver 并行占用第二连接，应在正式接收前绑定或复用同一连接。A 未实现 E 的通用 RPC 多路复用；既有 Bridge 等待长 RPC 期间的 ack 延迟仍是后续 E 适配边界。

### 8. 超时、取消与断线

默认 SQLite 锁等待、身份查询、建立连接和确认发送分别为 5 秒，含义不同。锁超时可诊断、可原提交重试，不能返回 accepted。

确认发送超时/断线不会撤销已经提交的 Message。原 SID/Envelope 重试返回原记录或冲突。取消 await 不会中断已经执行的 SQLite worker；关闭连接等待连接互斥锁，避免与事务并发关闭。

有真实 SQLite 测试：写事务被外部连接锁住后取消 await，再解除锁、关闭/重开，确认完整 Message 保留，重试 duplicate=true。该行为不等于向已取消调用返回 accepted；调用方没有确认，必须按不确定结果处理。

Receiver 默认不自动重连，不推进 pending 消息；连接结束后重新启动使用原数据库即可。恢复保存数据与 E 的处理恢复是不同能力。

URI/token 从配置提供，空 userinfo、重复 token 参数及 InvalidURI 等不会输出凭据或敏感异常链。没有改造旧 Bridge 的 token/Origin 体系或旧 Adapter 的错误行为。

### 9. 自动化验收与真实结果

环境：Python 3.10.11、SQLite 3.40.1、websockets 16.1.1。

最终完整验证：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests -q -p no:cacheprovider
```

```text
208 passed, 145 subtests passed in 67.97s (0:01:07)
```

阶段 0 的标准库独立验证：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest tests.contracts.test_ids tests.contracts.test_models tests.contracts.test_ports tests.contracts.test_edge_cases -q
```

```text
Ran 62 tests in 0.935s
OK
```

| 需求/场景 | 证据范围 |
|---|---|
| 首次、相同重试、冲突、不同 SID 同正文 | 真 SQLite 单元测试与真 Bridge 集成 |
| 完整来源、正文、Core ID 保留 | SQLite 列和恢复后的阶段 0 对象检查 |
| 闭库重开 | Store 和实际 CLI 进程重启测试 |
| INSERT、COMMIT、ROLLBACK 失败 | 临时 SQLite 触发器、延迟外键及 authorizer 故障注入 |
| Schema/记录损坏、未知数据库 | 临时库重载及不覆盖检查 |
| Endpoint 不存在/会话或 provider 不符 | 真 Registry 与 Reader/Receiver 测试 |
| 双连接重复/冲突竞争 | SQLite 两个实际连接、数据库唯一约束 |
| 取消真实 SQLite 等待 | worker 运行后取消与重新打开测试 |
| 双外部连接、迟到 ack、页面替换 | 真 Bridge 的隔离连接测试 |
| commit 后丢失 ack、重启重试 | 实际 Store commit，故意不发送确认，再使用新 Receiver |
| URI 配置错误不泄漏 token | URI 参数和 InvalidURI 异常链回归 |
| 旧协议及阶段 0 | 全部原有测试和独立契约测试继续通过 |
| 真正 ChatGPT Extension 到 SQLite | **未验证** |

实际 `python -m contextflow_core.receiver --once` 进程也通过隔离 Bridge 驱动，首次 accepted，重启后同提交 duplicate=true，数据库仍一条。这不是仅 import 内部函数，但其 page 端仍是假 WebSocket。

两个独立只读审查共确认存储 5 项、接入 3 项边界缺陷，均补失败回归并修复；另补同类 UPDATE 与真实 SQLite 取消测试。修复后的结论来自本地回归，没有声称完成额外独立复审或真实浏览器验证。

### 10. 已验证与未验证分层

- **已实现且已测试**：本报告列出的 Store、身份 Reader、Receiver、CLI、连接互斥、尝试关联和事务/重启行为。
- **已实现但真实网页未验证**：Extension 到新 Receiver/SQLite 的组合。没有重新修改 Extension JS。
- **仅契约/后续设计**：Parser、Task/Delivery、Agent 目录/Gateway、报告处理、E 组装与处理恢复。
- **尚未实现**：Project/Role、Agent Intake/执行、外部报告回传、通用管理界面、分布式协调。

本轮检测到 Python Playwright 与 Edge 存在，但既有 CDP 9222/9333 不可连接，既有 Bridge 没有页面连接。没有启动新登录会话、清理用户缓存、替换已有服务或向 ChatGPT 发消息来强行验收。具体可复现人工步骤见 `docs/CF-CORE-003-usage.md`，包括配置、先绑定、Receiver 启动、选已有未提交 Assistant 消息、查询数据库、重启与缓存验证。

### 11. 已知限制和后续 B 支持

- SQLite 为本地明文文件，WAL/事务依赖 SQLite 与文件系统保障；不是独立防篡改、加密或多 Core 协调服务。
- 已知 Schema 定义严格核验；未来改表/索引/触发器须明确版本和迁移授权，不自动接管。
- 来源校验不认证账号或消息语义；正文非空不证明目标或任务完整。
- 原生 ID、Endpoint 和提交 ID 没有因抽象被删除，但本阶段也没有 Project/Role 业务绑定。
- 通用 Store 支持 D 以后提供的内部 AgentSubmission，不代表 Intake、Task 归属或 final 唯一性已实现。
- 旧顶层导入仍需已有 websockets；没有越界改为标准库隔离入口。
- pending、processed、blocked 是处理标记，不是 Task 完成或发送证明。

后续 B 可以使用 `MessageReader.get_message()` 取得原始 `message.envelope.content.text`，或由未来处理器通过 `list_pending()` 选择待解析消息。B 的 parse/extract Port 没有改变；A 不调用 LLM、不给默认 Agent、不创建 Task。解析结果和任务批次冻结仍由后续 B/C/E 按已有契约实现。

### 12. 提交记录与范围

- `f071490`：SQLite Store、来源核验、初始测试和计划。
- `ae244c2`：存储审查后的事务实际效果、Schema 和回滚边界修复。
- `5a4265c`：正式 Receiver、Bridge 归属、URI/确认回归及 CLI 集成。
- 最后文档/验收记录提交保留取消测试、使用说明与本报告。

原有两份根目录设计文档未修改、未纳入提交。没有推送或合并 main；没有继续阶段 B。
