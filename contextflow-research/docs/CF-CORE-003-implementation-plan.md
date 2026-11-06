# CF-CORE-003：阶段 A 实施计划

## 范围与基线

- 基于 `08462a9`；实施前回归为 128 passed、132 subtests passed。
- 阶段 0 报告保存在此前对话，仓库对应说明为 `contextflow_core/contracts/README.md`。
- 不修改冻结契约、Envelope v1、Registry 文件格式或旧 Adapter。
- 不开发 Parser、Task Dispatcher、Agent Report Intake、ReportDelivery Runtime 或 Project/Role。
- 不新增依赖；SQLite 使用标准库，通信复用已有 websockets。

## 设计

1. `endpoint_identity.py` 实现 EndpointIdentityReader，复用 EndpointRegistry 读取当前持久身份；不探测标签页或在线状态。
2. `message_store.py` 实现 SQLiteMessageStore。messages 表保存 Core ID、种类、完整 Envelope、既有 SHA-256、来源字段、接收时间、处理状态与错误；唯一约束覆盖 Supervisor submission_id 和契约要求的内部 Agent 报告身份对。
3. 通用 Store 的 AgentSubmission 仅为 D 以后提供的已验证内部输入；本阶段没有 Agent 外部接入、认证、Task 查询、报告处理或回传。
4. 来源检查在 Supervisor 持久接收前完成。BEGIN IMMEDIATE、参数绑定、唯一索引、显式 commit/rollback 保证并发幂等；不使用消息内存缓存。
5. 数据读取重新构造阶段 0 对象，并验证 Envelope 指纹和冗余身份字段。不迁移未知 Schema 或删除已有数据。
6. `receiver.py` 提供正式 Supervisor 接收入口和 CLI。仅接受既有 submission，输出既有 submission_ack 语义并回显已有 request_id 关联本次尝试；无 request_id 的旧形式仍兼容。Core Message ID 保留在内部查询和诊断结果。
7. Bridge 限制一个外部 WS 连接，第二连接拒绝、不抢占；关联接收连接和页面，避免失效连接 ack；不添加 hello 或 Receiver 注册协议。RPC 调试需复用唯一连接。
8. 同 ID 在途 Envelope 变化不得被旧 ack 错误确认；Bridge 不替代 SQLite 的权威幂等判断。
9. SQLite 锁等待、来源查询和发送确认使用明确错误/超时策略。确认失败不会撤销已提交消息；取消后台写入不等于事务已经中断。
10. 阶段 0 导入独立性断言在独立 Python 进程执行，排除完整测试集合的导入顺序影响，不删除或弱化断言。

## 增量与验证

- [x] SQLite Schema、持久化、查询、被动处理标记及来源 Reader。
- [x] 首次、重复、冲突、重新打开、写入/commit 失败、锁竞争、并发、数据损坏测试。
- [x] Receiver、ack 映射、配置和连接生命周期。
- [x] Bridge 单连接与 ack 归属、Bridge 到 SQLite 集成测试。
- [x] 契约、现有协议与完整回归。
- [x] 检查真实浏览器运行条件；CDP 不可连接且既有 Bridge 未连接页面，真实 ChatGPT 链路未验证，人工步骤已记录。
- [x] 独立代码审查、针对性修复及最终复验。
- [x] 使用说明与 `reports/CF-CORE-003.md`，区分已实现、已测试和未验证。

计划按存储、接入、文档形成小提交，不纳入原有未跟踪设计文档，不推送或合并 main。
