# CF-CORE-001｜ChatGPT Endpoint 绑定与基础生命周期管理

日期：2026-11-05

## 完成情况

- 实现 `discover / bind / resolve / read` 四个 Core 接口。
- Endpoint ID 由 Core 生成，通过原子 JSON 注册表持久化；重复绑定幂等。
- 原生 Session ID 是持久身份，`tab_id` 仅为实时位置。
- 生命周期状态为 `available / offline / ambiguous`，错误原因单独保留。
- 解析只使用原生 Session ID 精确匹配，不回退到活动页，不做模糊替换。
- 读取使用 `session_id + tab_id` 双重约束，路由竞态最多安全重解析一次。
- Adapter 兼容增加 `turn_group_id` 和 `turn_position`；已覆盖 Assistant position 为 2 的真实结构。
- 快照明确声明只代表当前渲染 DOM，未观察到消息不代表删除。

## 架构变化

新增 Python 包 `contextflow_core`：

- `models.py`：稳定数据契约。
- `errors.py`：结构化错误。
- `adapter.py`：正式 WebSocket Adapter 边界、断线失败和后续重连。
- `registry.py`：Endpoint 唯一性和原子持久化。
- `service.py`：Endpoint 发现、绑定、解析和读取。

现有 WebSocket Bridge、background 严格路由和 content 实时快照机制全部保留。

## 自动化测试

| 测试 | 结果 | 性质 |
|---|---:|---|
| Core unittest | 39/39 | 模型、注册表、真实本机 WebSocket、绑定、解析、读取和恢复 |
| CF-DEV-001 DOM 回归 | 21/21 | Playwright Mock，含 `fallback-turn-0:2:assistant` |
| CF-DEV-002 | 14/14 | Adapter 实时读取与严格路由 Mock |
| CF-DEV-003 | 12/12 | 回复等待 Mock；不代表已修复真实懒加载误关联 |
| CF-DEV-004 | 17/17 | 输入、发送和完整循环 Mock |

## 真实 ChatGPT 页面验证

目标原生 Session：`6aeb4db5-d930-83ee-8b14-0cf74ef0a84d`。

1. 真实 Adapter 发现唯一可绑定页面。
2. Core 创建 Endpoint `cfep_2aca39b708294c66a1c67ef871713c53`。
3. 刷新前解析为 `available`，读取 10 条当前渲染消息。
4. 精确刷新目标 ChatGPT 标签页。
5. 新 Python 进程重新加载 JSON 注册表。
6. 使用原 Endpoint ID 再次解析为 `available`，仍读取 10 条消息。
7. `same_endpoint_id=true`、`same_native_session_id=true`。

结构化证据：`_artifacts/cfcore001-real-evidence.json`。证据不含消息正文。

## 插件试用记录

- `bridge_client.html` 不带查询参数时页面读取到空 token，服务端拒绝连接；使用带 token URL 后恢复。后续应让页面由服务端安全注入 token，或修正文档和启动方式。
- 尝试向协作 ChatGPT 发送简短代码审查汇报时，插件检测到目标输入框已有 3894 字草稿并返回 `DRAFT_PRESENT`。未使用 `force`，没有覆盖草稿，也没有发送消息。
- `manifest.json` 的描述仍写着 read-only/no sending，与当前 v0.5.0 能力不一致。
- 浏览器自动化刷新调用未及时返回 UI 快照，但刷新后的 Adapter/Core 读取成功；真实验证以 Bridge 返回的 Session、Endpoint 和快照证据为准。

## 遗留限制

- 侧边栏目录尚未成为权威发现源；Core 模型已为未来目录来源预留字段。
- Core 重启后可恢复 Endpoint 身份，但当内容脚本完全不可用且 Adapter 无法报告 Session ID 时，只能报告离线，不能证明当前 Tab 仍是原会话。
- `turn_group_id` 是结构证据，不是永久消息身份。
- `wait_for_reply` 的消息因果关联仍可能受懒加载影响，本任务没有修改。
- JSON 注册表按单 Core 进程设计，暂不支持多个 Core 进程并发写入。

## 下一步建议

已具备进入 CF-CORE-002 的前置条件：上层可以依赖稳定 Endpoint ID、严格解析结果和规范化消息结构。下一阶段应在 Endpoint 之上实现 Operation 状态机、User 消息确认、同回合 Assistant 关联和 `ambiguous/unverified` 证据状态，不能继续以“基线中未出现的消息 ID”作为唯一因果依据。
