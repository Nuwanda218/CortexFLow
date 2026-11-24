# ContextFlow 开发阶段状态

**更新日期：2026-11-24**
**用途：** 汇总已实现的系统能力、当前代码基线和验证边界，帮助恢复开发上下文。它不是产品范围批准书，也不替代各阶段规格。

## 总体结论

后端已完成多阶段能力建设，最新完整阶段基线为 C-06。项目下一步应由单项后端模块开发转向统一前端与 Core 控制面的集成，形成可观察、可操作、可测试的完整工作台。

“后端阶段已实现”不等于“完整产品已建成”：当前没有覆盖消息、任务、执行、报告和审核的统一前端；部分跨组件接口仍由 Python 调用方显式组合。真实 ChatGPT 扩展至 Core 的当前配置端到端链路，以及真实 ChatGPT 会话中的报告回传，仍需人工验收。

## 代码基线与工作区

统一基线保留完整 C-06 历史（`392ee98`）、既有 UI 提交（`7ce1236`）和最新四级工作空间 Demo。前端仍为本地 Mock，尚未接入 Core。

| 分支 | 本机工作树 | 用途 |
| --- | --- | --- |
| `main` | `D:\CortexFlow-main` | 已验收的共同基线。 |
| `dev/ui-design` | `D:\CortexFlow-ui` | 前端 UI、交互、Mock 和前端测试。 |
| `dev/infrastructure` | `D:\CortexFlow-stage-c` | Core、Bridge、Runtime、持久化和后续基础设施。 |

原 `D:\CortexFlow` 与其嵌套 C-01 工作树作为历史现场保留，不作为后续开发入口；原始未提交文件不清理、不覆盖。三个分支均包含完整仓库，并分别跟踪同名远端分支。详细规则见 [分支约定与统一基线](../development-baseline.md)。

历史记录：2026-11-16 全套 Core 测试为 442 passed、4 skipped、360 subtests passed；2026-11-24 UI 原验收为构建通过、24 项浏览器测试通过。历史结果不代替本次整合后的实跑结果；本次记录见 [统一基线验证](../baseline-verification.md)。4 项真实 Agent 测试默认跳过，不能视为真实网页链路通过。

## 阶段能力

| 阶段 | 已有能力 | 证据边界 |
| --- | --- | --- |
| Adapter / 阶段 0 | 浏览器消息适配与人工提交基础；统一 ID、Envelope、状态和模块 Port 契约 | 契约和本地测试不证明当前真实浏览器会话稳定可用。 |
| A / B1 | Supervisor Envelope 校验、Endpoint 来源核验、SQLite Message Store、幂等接收；Schema v2、持久 ParseJob、带 lease/fencing 的 Parser Worker、ParseResult 持久化 | B1/B2 Fake Worker 链路和自动化测试已覆盖；真实网页提交不由 Fake 测试证明。 |
| B2 | 可信 Agent Directory、严格指令解析、Anthropic 与 OpenAI-compatible Provider Adapter | 历史记录含 DeepSeek 真实解析通过、Anthropic Router 返回 401。真实 Provider 调用不等于 ChatGPT Extension→Core 验收。 |
| C-01 | Supervisor 会话绑定、显式指令识别与提交、状态查询/反馈 | 有扩展、Bridge、Core Control 与自动化测试；完整真实 ChatGPT 页面验收仍待确认。 |
| C-03 | Codex CLI、Claude CLI、Codex Relay、DSH 等 Runtime Adapter 与统一执行契约 | 有适配器、隔离实验和测试；各适配器的终态证据、恢复和副作用能力不相同，unknown 必须保留。 |
| C-04 | ParseResult 按整批、幂等转换为 Task 与 TaskDelivery | 有持久化实现和测试。 |
| C-05 | 显式派发 Delivery、Execution 关联、状态观察和恢复对账 | 有 Dispatcher/存储和测试；不是自动多 Agent 调度系统。 |
| C-06 | Agent 报告持久化、按原 Supervisor 身份回传、结构化审核决定 | 本地 Bridge + 模拟浏览器闭环有测试；真实 ChatGPT 页面回传仍待人工验收。 |

主要交付说明：`contextflow-research/docs/CF-CORE-003-usage.md`、`CF-CORE-004-B1-usage.md`、`CF-CORE-004-B2-usage.md`、`CF-C03-agent-runtime-usage.md`、`CF-C04-C05-task-execution.md`、`CF-C06-report-review.md`。上述实现已纳入统一基线，可在三个正式分支中查看。

## 尚缺的系统层

1. **统一工作台前端**：尚无一个主界面统一展示消息来源、解析结果、Task、Execution、Report、审核和错误/unknown 状态。C-01 扩展 popup/页面状态不是完整任务工作台。
2. **统一交互控制面**：现有 Supervisor Control 覆盖绑定、提交状态查询和报告审核等操作；Task 创建、执行派发、执行刷新、报告管理等流程尚未形成面向统一 UI 的完整一致 API。部分阶段能力通过 Core Python 服务直接组合。
3. **应用级运行与装配**：需要验证并定义 Bridge、Receiver、Parser Worker、Task/Report 服务和前端的启动、配置、健康状态、日志、错误呈现及生命周期管理。
4. **纵向集成验收**：需要从统一界面操作隔离 Fake 流程，再单独验证真实扩展、Bridge、Receiver、Provider/Parser 与受控 Agent Runtime 的集成边界。不能将单元测试或模拟网页测试写成真实网页验收。

## 开发约束

- 前端只调用明确的 Core 控制接口，不直接操作 SQLite 或拼接模块私有存储。
- 状态界面区分“已持久接收、解析完成、已创建任务、Agent 已接受、执行终态、报告已送达、Supervisor 已审核”；它们不能合并为一个“完成”状态。
- 外部发送或执行结果不明时保留 `unknown`，不盲目重试。
- UI 的手动触发、用户确认和危险权限应与后台状态展示分开；开始搭建系统不代表默认启用自动执行。
- `F:\26271\11月底面试准备\uxuescript` 可参考其主布局拆分、集中式 Tauri command wrapper、事件驱动状态更新；它不能替代 ContextFlow 的控制 API，也不预先决定复用其代码或 UI 技术。
- 原始工作树和未提交成果另行保留。阶段变更通过两个开发分支合回 `main`，不直接在历史现场继续开发。

## 接续工作的顺序

1. 前端和基础设施分别在对应开发分支继续，阶段验收通过后合回 `main`。
2. 对照 C-01 到 C-06 的公共接口，列出前端需用的查询/操作及当前缺口，确定单一控制面边界。
3. 结合现有扩展、候选桌面形态和 `uxuescript` 参考，定义首条 UI 纵向切片及明确验收条件；先选交互结构，再根据现有工程决定实现载体。
4. 以隔离数据库和 Fake Parser/Agent 完成工作台操作与状态反馈；通过后再逐层安排真实环境验证。
