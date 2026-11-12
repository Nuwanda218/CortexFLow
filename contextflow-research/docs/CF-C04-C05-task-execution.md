# C-04 / C-05 任务创建与执行

C-04 从阶段 B 的 SQLite 库读取已持久化的 `PARSED` 结果。每条指令生成一对 Task 和初始 TaskDelivery。C-05 接受一个 Delivery ID，通过已配置的 Adapter 启动执行并保存关联。

## 调用入口

在已有 Core 调用方中使用同一个消息数据库和 Agent Directory：

```python
from contextflow_core.contracts import AgentId, MessageId
from contextflow_core.parse_jobs import SQLiteParseJobRepository
from contextflow_core.task_dispatch import AgentRuntimeBinding, TaskDeliveryDispatcher
from contextflow_core.task_store import SQLiteTaskRepository, TaskCreationService

tasks = SQLiteTaskRepository(database_path)
parse_results = SQLiteParseJobRepository(database_path)
try:
    creation = TaskCreationService(tasks, parse_results, agent_directory)
    batch = await creation.create_tasks_from_parse_result(MessageId(message_id))
finally:
    await parse_results.close()

# 从 batch.deliveries 中显式选择一条，runtime_registry 由 Core 注入。
binding = AgentRuntimeBinding(
    agent_id=AgentId("codex-local"),
    runtime="codex-cli",
    workdir=absolute_workdir,
)
dispatcher = TaskDeliveryDispatcher(tasks, agent_directory, runtime_registry, binding)
# 以下调用会启动所配置的 Agent。
# execution = await dispatcher.dispatch_delivery(delivery_id)
# result_record = await dispatcher.wait_for_execution(delivery_id)
```

- C-04 返回 `created`、`duplicate`、`conflict` 或 `rejected`。批次键为 `source_message_id`，解析指纹覆盖按序排列的目标与指令内容。已提交批次重放时返回原 Task 和 Delivery。沿 Task 的来源 Message 可以读取原 Supervisor Endpoint，协作关联字段首版为空。
- `dispatch_delivery` 返回已保存的执行记录。仅当 Delivery 为 `pending` 且成功写入启动意图，才调用 Adapter。首版每个配置 Agent 同时保留一条待确认或运行中的执行。运行目录、权限和超时由调用方配置，默认新会话与只读权限。
- `wait_for_execution` 通过 Adapter 等待并执行配置的超时。只需查看进度时，`get_execution` 读取上次保存的状态，`refresh_execution` 向 Adapter 查询一次。C-06 可以按 Delivery ID 调用 `get_execution_result`。大于 64 KiB 的正文另存为 UTF-8 文件，`output_ref` 相对于 SQLite 库所在目录。

## SQLite 与恢复

`task_batches`、`tasks`、`task_deliveries` 和 `task_executions` 是原 v2 数据库的新增表。Task 批次与初始 Delivery 同事务提交，旧的 Message 和 ParseJob 表保持原结构。

启动进程退出或结果不明时，在确认原派发调用已结束后显式调用 `recover_delivery`。若已保存 Execution ID，按该 ID 查询；否则用 Delivery ID 和请求指纹核对 Adapter 的 Journal。没有可靠记录时保留 `unknown`，同一 Delivery 不重启。跨进程能恢复的状态和正文取决于 Adapter 留下的证据。

Adapter 接受执行后，Task 为 `dispatched`。执行终态保存在 Execution 记录中，审核决定由 C-06 接入。
