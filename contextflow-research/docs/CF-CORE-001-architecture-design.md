# CF-CORE-001 架构设计：ChatGPT Endpoint 基础能力

状态：2026-11-05 已经人工确认，允许进入实现。

## 目标与边界

在不改变 ChatGPT Web Adapter v0.5.0 通信链路的前提下，为上层调用者提供稳定的 Endpoint ID，并实现发现、绑定、重新定位和规范化读取。

本阶段不实现 Operation 状态机、发送因果关联、多 Agent 调度、新 UI 或侧边栏全量会话抓取。

## 核心决策

1. Endpoint 的持久身份是 `(provider, native_session_id)`；`tab_id` 只是当前运行时位置。
2. Endpoint ID 由 Core 生成并通过轻量 JSON 注册表持久化。注册表默认写入 `%LOCALAPPDATA%\ContextFlow\endpoints.json`，测试使用临时路径覆盖；不引入数据库。
3. 每次解析都调用 Adapter 的实时 `list_sessions`，只接受原生 Session ID 精确匹配，不使用标题、URL 模糊匹配，也不自动替换目标。
4. 同一 Session 同时出现在多个标签页时返回 `ambiguous`；不存在、Bridge 断开或内容脚本不可用时返回带原因码的 `offline`。
5. Core 读取时把 `session_id` 和本次解析出的 `tab_id` 一起传给 Adapter，利用现有双重校验防止解析与读取之间发生页面切换。
6. DOM 回合键的拆解留在 Adapter。Core 只消费 `turn_group_id` 和 `turn_position`，不复制 DOM 选择器或格式规则。
7. 当前快照只代表已渲染 DOM。读取结果固定声明 `history_scope=rendered_dom` 和 `absence_means_deleted=false`。

## 模块职责

- `contextflow_core.models`：稳定数据契约和枚举。
- `contextflow_core.errors`：Core/Adapter 的结构化错误。
- `contextflow_core.adapter`：最小 Adapter Protocol 及现有 WebSocket Bridge 客户端。
- `contextflow_core.registry`：Endpoint 唯一性、原子 JSON 持久化和重载。
- `contextflow_core.service`：`discover / bind / resolve / read` 编排。
- `content.js`：兼容增加 `turn_group_id`、`turn_position`，保留原 `turn_key`。

## 最小数据模型

```text
EndpointBinding
  endpoint_id
  provider = chatgpt_web
  native_session_id
  session_kind
  created_at
  updated_at

EndpointResolution
  endpoint_id
  state = available | offline | ambiguous
  reason_code
  location = {tab_id, url, ready} | null
  candidates = []
  observed_at
```

注册表不把 `tab_id` 当作绑定身份。重复绑定同一个 `(provider, native_session_id)` 返回原 Endpoint，并标记 `created=false`。

## 接口契约

### `discover()`

复用 Adapter `list_sessions`，返回打开页面的实时记录：`native_session_id`、`tab_id`、URL、kind、ready、bindable、source 和底层错误。

### `bind(session_id)`

- 只接受非空稳定 Session ID。
- 零个精确匹配：`SESSION_NOT_FOUND`。
- 多个精确匹配：`AMBIGUOUS_SESSION`。
- 唯一且 ready：创建或复用 Endpoint。
- 新建对话、draft、无 Session ID 页面返回 `bindable=false`，本阶段不做身份升级。

### `resolve(endpoint_id)`

- 未知 Endpoint：`ENDPOINT_NOT_FOUND`。
- 唯一精确匹配且 ready：`available`。
- 无匹配、Bridge/Adapter 不可用：`offline`，保留绑定。
- 多个精确匹配：`ambiguous`，不选标签页。

### `read(endpoint_id)`

先解析，再用 `session_id + tab_id` 获取实时快照。只读调用遇到一次短暂断线或 Session 路由竞态时，允许重新连接并重新解析一次；第二次失败即返回结构化错误，不循环猜测。

规范化消息包含：`message_id`、`message_ids`、`role`、`content`、`turn_group_id`、`turn_position`、`status` 和原始结构证据。

## 侧边栏目录

侧边栏链接适合未来补充“已知会话目录”，但目录项不等于当前可操作位置。v0.1 的权威绑定与解析来源仍是 `list_sessions` 的打开页面；模型预留 `source`、`bindable` 和 `availability`，未来由 Adapter 增加 `list_catalog_sessions`，Core 不直接解析网页侧边栏 DOM。

## 测试策略

- 单元/Mock：绑定唯一性、重复绑定、刷新换 tab、关闭、歧义、Bridge 断开与恢复、读取竞态、回合位置为 2、注册表重载。
- Adapter 回归：现有 CF-DEV-002/003/004 测试继续通过。
- 真实页面：发现、绑定、读取、刷新后以同一个 Endpoint ID 再读取。Mock 与真实结果分开报告。

## 已知限制

- `turn_group_id` 是当前 DOM 的结构证据，不是永久业务 ID。
- DOM 未观察到的消息不能解释为删除。
- v0.1 不自动打开侧边栏中的离线会话。
- `wait_for_reply` 的因果关联缺陷留给 CF-CORE-002。

