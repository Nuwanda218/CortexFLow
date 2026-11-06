# CF-CORE-002-0：Core v1 公共契约

本包是阶段 0 交付：可导入的不可变类型、结构校验与 `Protocol`。它不是
Message Store、Parser、Dispatcher、Report Return 或 Runtime 的业务实现。
所有后续阶段仍须单独授权。

## 使用入口与文件职责

```python
from contextflow_core.contracts import ParseResult, ParseStatus, ParsedInstruction

candidate = ParseResult(
    status=ParseStatus.PARSED,
    instructions=(
        ParsedInstruction(target_agent="codex", content="修复 Adapter 并运行测试"),
    ),
)
assert candidate.intent_fingerprint is not None
```

- `ids.py`：运行时可区分的 string ID；不生成、注册或恢复身份。
- `envelopes.py`：Content、Supervisor Envelope v1 包装、Agent Report Envelope。
- `models.py`：Message、ParseResult、Task、两种 Delivery、结果类型和只读身份视图。
- `ports.py`：所有外部依赖及模块入口的结构化调用契约，没有服务实现。
- `_validation.py`：纯类型、UTC 时间和不可变 JSON 快照校验。
- `__init__.py`：本包公开导入入口；不改动旧顶层公开入口。

类型采用 frozen dataclass，直接构造也校验字段。ID 使用不同 str 子类型；
记录字段不能用裸字符串或其他 ID 类型代替。Core ID 使用 `cfmsg_`、`cftask_`、
`cfdel_` + 32 位小写十六进制；`AgentId`、原生 ID、`ReportId` 为非空、最长 256
字符的外部身份。既有 Endpoint/Submission ID 复用原 v1 正则，不收紧其规则。

## 对象、身份与状态

| 类型 | 身份／约束 |
|---|---|
| Message | Core 创建 message_id；持久接收后 Envelope、来源和正文不变 |
| ParseResult | parsed 必须包含非空的完整指令 tuple；unresolved/failed 必须无指令、有错误 |
| Task | C 创建 task_id；关联 source_message_id + instruction_index + 已验证 agent_id |
| TaskDelivery | subject_id 必须 TaskId，target_id 必须 AgentId |
| ReportDelivery | subject_id 必须 Core MessageId，target_id 必须 EndpointId |
| AgentReportEnvelope | Agent 创建 report_id，必须包含 task_id；发送方身份由可信接入上下文提供 |

`Delivery` 是 `TaskDelivery | ReportDelivery`，不是可直接构造的通用 DTO。
ParsedInstruction 的目标和正文必须可编码为 UTF-8；孤立 surrogate 在构造时以
INVALID_CONTRACT 拒绝，避免一个 parsed 候选在计算指纹时才抛出编码异常。
这没有改变既有 Supervisor Envelope v1 校验。

`ErrorInfo` 是既有 `ContextFlowError` 的不可变表示，不是另一套异常体系；
`ErrorInfo.from_error()` 与 `to_dict()` 保留原结构。
错误 details、投递 evidence 会深度快照为只读 Mapping／tuple；字符串枚举保留
底层 JSON 字符串值，不使用枚举显示名称。循环值、非 JSON 值和非有限数拒绝；
容器嵌套深度最多 64（顶层深度为 0），超限返回 INVALID_CONTRACT。
序列化时应使用明确的字段映射，ErrorInfo 使用 `to_dict()`，不要假设
`dataclasses.asdict()` 能够处理只读 MappingProxyType。

- Message：pending / processed / blocked。processed 是下游持久化步骤已完成，
  **不是任务完成，也不是发送成功**；blocked 必须保留原因。
- Task：pending_delivery / dispatched / completed / failed。
  completed/failed 必须关联一个 final_report_message_id；非终态不得带此字段。
- Delivery：pending / sending / confirmed / failed / unknown。
  failed/unknown 必须有错误；confirmed 必须有接收／发送验证证据。
- `can_attempt_send` 只是状态级必要条件：仅 pending 为 true。
  目标合法性、可用性等仍由 C/D 判断。`retryable=true` 不允许绕过 unknown。
- DeliveryOutcome 不允许返回 sending；pending 表示已知没有外部接收的等待，
  必须提供原因；不能把响应丢失包装成 pending。

Snapshot 类型校验不能证明目标实际存在、外键指向正确 Message 类型、数据库已
提交或外部真的接收；这些保证分别由 A/C/D/Gateway 实现与业务测试负责。

## 已确认业务规则与 Port 所有权

1. C 先完成全部目标合法性校验，再原子建立全部 Task、初始 Delivery 和批次比较
   依据。不能因为某个目标无效而建立有效子集。
2. 准入后，各 Agent 的实际接收不要求跨端点原子性。保留 confirmed/failed/unknown
   的真实混合结果，不做分布式事务、撤回已接受 Task 或自动补偿。
3. 目标未明确返回 unresolved，不使用默认 Agent；Parser 不创建 Agent 或规划任务。
4. progress 只保存；question、blocked、final 自动回传。
5. 一个 Task 只允许一个有效 final。同一个 report_id 重试幂等；不同的第二份 final
   不能覆盖第一次结果。处理时才发现冲突的已接收报告必须保留并标记 blocked。
6. Agent 以 task_id + 正文实现持久接收幂等；确认接收不等于完成。
7. 外部发送前持久标记 sending；遗留 sending 恢复为 unknown，不自动重发。

| 阶段 | Port／状态维护权 |
|---|---|
| A | MessageStore/MessageReader：格式、幂等、持久化、查询和被动处理标记；不查询 Task |
| B | InstructionParser + LlmExtractionPort：语义提取、结构校验；无数据库或发送 |
| C | TaskDispatcher、TaskQuery、TaskStatus；AgentDirectory、AgentGateway 抽象；只维护 Task 与派发 Delivery |
| D | ReportReturn：验证 Task/可信 Agent，调用通用 Store，调用 TaskStatus，维护回传 Delivery |
| E | MessageProcessor 与 SupervisorGateway 的实现、依赖组装、单连接事件分流和恢复推进 |

EndpointIdentityReader 只提供现有 Registry 的持久身份，不探测标签页或绑定新端点。
AgentDirectory 把名称解析为稳定 AgentId，未知返回 None，歧义报 AGENT_AMBIGUOUS；
已知但离线 Agent 仍保留在目录中。实际认证和 Agent Adapter 网络接入不在阶段 0。

Report Return 通过 TaskQuery + MessageReader 获得原 Supervisor Endpoint：
`Report.task_id → Task.source_message_id → Message.envelope.source.endpoint_id`。
Task 不重复保存该 Endpoint，不访问 Dispatcher 的私有变量或表。
TaskStatus.finish_task 原子、幂等地记录唯一最终结果：相同 final Message/outcome
重放成功，不同 final 或不同 outcome 冲突。迟到的派发确认不得回退终态。

## 解析结果与恢复一致性：最小边界

**不新增 ParseResult 持久化实体、解析版本、检查点或调度组件。**

### 尚未提交任务意图

Message 已保存但 TaskQuery 没有已提交批次时，ParseResult 仅是候选。
在此边界内，重启后允许重新调用 LLM，结果可能不同：

- 不把两次解析的指令拼接、追加或混合。
- 单次候选必须完整解析并完成全部目标校验，才能由 C 提交。
- 某次候选未提交前不得发送任何 Task，因此重新解析不会改写已承诺的执行内容。
- unresolved/failed 应将 Message 标记 blocked，v1 不自动循环重新解析。

### 已经提交任务意图

C 的原子提交是意图确认点。`ParseResult.intent_fingerprint` 为完整、有序的
`target_agent + content` 列表的稳定 SHA-256；它不是解析版本或接收幂等键。
Message ID 提供批次命名空间。

- C 必须把 fingerprint 与整批 Task、初始 Delivery **原子持久化**。
- CreateTasksResult.created/duplicate 必须包含整批记录和相同的 intent_fingerprint。
- 同一 source_message_id、相同 fingerprint：返回既有 Task/Delivery。
- 同一 source_message_id、不同 fingerprint：TASK_BATCH_CONFLICT，不追加、替换或重分配。
- 冻结后的 Task 使用已验证的 AgentId；名称映射变化不能把既有 Task 改投其他 Agent。
- E 在调用 Parser **之前**查询整批 Task；存在批次时，即使 Message 仍为 pending，
  也不再次调用 LLM。只完成幂等后续步骤及处理标记。
- processed 的 Supervisor Message 却没有 Task 批次是 PROCESSING_INCONSISTENCY，
  不能通过重新解析并补建任务掩盖。

这一边界保证**已提交意图不变**，不保证提交前每次 LLM 输出相同。
若未来要求提交前候选也跨重启完全一致，才需要另行讨论保存 ParseResult；本轮
不擅自增加该要求。

A/B 职责无需修改。C 将原来就需要的批次幂等比较依据明确为 fingerprint；E 实现
“先查已提交批次，再决定是否解析”。本轮只实现 fingerprint 计算、结果表达与测试，
没有实现其持久化、数据库原子性或恢复分支。

## 现有协议兼容与已知限制

- SupervisorEnvelope.from_dict 直接调用既有 validate_envelope，保留严格字段集、
  schema_version=1、原生 source.message_id，以及包含 nullable 键的要求。
- 原始输入和导出的 dict 不与已构造 Envelope 共用可变嵌套对象。
- 不修改 accepted wire ack；内部 AcceptResult.message_id 不要求发给旧 Adapter。
- 不修改 Endpoint ID、Registry schema、Bridge token/welcome 或请求／响应协议。
- 既有 NormalizedMessage 仍是 DOM 快照，不是本包 Message。
- E 仍需单独授权对旧 Python Adapter 做最小事件分流／发送证据与 timeout 适配。

**现有顶层导入限制：** Python 导入子包前会执行 `contextflow_core/__init__.py`。
该旧入口主动导入 Adapter，因此即使本包新增代码只使用标准库与已有纯校验／错误
工具，正常 import 仍需要既有 websockets 依赖；`python -S` 下会被该旧入口阻断。
本轮未改动旧入口、未新增依赖。若要求整个 Core 的契约入口能在仅标准库环境独立
导入，需要单独授权改为顶层惰性导出；不使用 sys.modules 等兼容绕路。
这不影响当前环境导入，也不依赖尚未开发的 A/B/C/D/E 模块。

## 阶段 0 交付记录（2026-11-06）

本阶段交付包含公共契约目录的 7 个文件和契约测试目录的 6 个文件。既有
Adapter、Bridge、Registry、Supervisor Envelope v1 与顶层导出均未修改，
没有新增第三方依赖，也没有实现 A/B/C/D/E 的业务服务。

交付前验证记录：

| 验证范围 | 结果 |
|---|---|
| 标准库 unittest 契约测试 | 62 tests，OK |
| 全部 pytest 回归 | 128 passed，132 subtests passed |

契约测试包括 ID 与状态组合、Envelope 兼容、任务批次结构、意图指纹、
不可变快照、Fake Port，以及字符串枚举、Unicode、循环与深层 JSON 边界。
上述记录不是实际 Agent 投递、完整 Core 运行或用户手动验收的证明。

## 验证与后续阶段

从 `contextflow-research` 运行，避免写测试缓存／字节码：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/contracts -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests -q -p no:cacheprovider
```

契约测试验证实际构造／拒绝行为、深度不可变快照、指纹变化、结果组合、合法目标
类型、unknown 不可发送，以及仅使用预设响应的 Fake Port 和可解析的类型注解。
Fake 不是数据库幂等、final 唯一性或重启恢复的业务实现。

后续 A 必须测试写入失败不 accepted、重启后接收幂等；B 必须测试真实风格语义样本；
C 必须测试原子整批、fingerprint 冲突、Agent task_id 幂等及独立投递结果；D 必须
测试报告来源、final 冲突保留和回传 unknown；E 才验证单 Bridge 连接和崩溃窗口。
发现需要改冻结契约、跨目录、增加依赖或改变已确认行为时，先升级汇报并等待授权。
