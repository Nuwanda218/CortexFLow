# C-01 Supervisor 绑定、提交与解析反馈设计

日期：2026-11-11  
状态：已获用户授权实施  
范围：C-01；不包含 Task Creation、Dispatcher、Agent 执行、Report Intake 或 C-02 Adapter 原型

## 1. 目标与完成标准

C-01 在不建立第二套消息入口的前提下，把既有链路扩展为可由用户直接操作、可恢复和可观察的 Supervisor 入口：

```text
ChatGPT Web
  → Extension（绑定、严格指令识别、手动/自动提交、状态 UI）
  → Bridge（认证后的双向中继）
  → Core Receiver / Supervisor Control（可信身份、消息接收、状态查询）
  → SQLite Message Store / ParseJob / ParseResult
  → Parser Worker
```

完成后：

- 一个扩展实例只绑定一个 ChatGPT Session 为 Supervisor；
- 普通 Assistant 回复不自动提交；
- 完整合法的指令块在消息生成完成后自动提交；
- 手动和自动路径对同一消息、同一规范化内容使用同一逻辑 `submission_id`；
- 用户可以区分 Core 已接收、Parser 等待/处理中、parsed、unresolved、failed 和状态未知；
- 刷新、扩展重载、Bridge 短暂断线、同 Session 多标签页不会把历史消息重新自动提交；
- 不迁移 Schema v2，不删除 Endpoint、Message、ParseJob 或 ParseResult；
- 不提前创建 Task 或投递 Agent。

## 2. 勘察结论与复用范围

### 2.1 已有能力

正式扩展 `contextflow-extension/` 已有：

- 真实 ChatGPT Session 识别；稳定 Session 只信任 `/c/<id>` 或 project chat URL，不使用可能残留的 DOM candidate；
- Assistant 原生 `message_id`、同回合 User ID 和 turn group 读取；
- `data-talvt-turn-state` 与 Stop 按钮组合的生成状态判断；
- Assistant 操作栏纸飞机、提交状态机和 `chrome.storage.local` 恢复；
- Bridge 页面中继、Core Receiver、`SupervisorEnvelope` v1；
- MessageStore 的 `submission_id + canonical envelope` 幂等/冲突；
- Schema v2、持久 ParseJob、Parser Worker 和 ParseResult。

现有正式扩展没有 popup。早期 `experiments/edge-extension-probe/` 有 popup/side panel 原型，仅作为交互参考，不直接复制其状态模型。

### 2.2 必须修改的边界

- **Extension**：单一 Supervisor 绑定、popup、自动识别、统一提交身份、跨标签安全锚点、状态恢复。
- **Bridge**：为 Extension→Core 控制请求增加相关性中继；不读 SQLite、不生成 Endpoint、不决定权限。
- **Core**：幂等注册 Endpoint；按已认证调用方和会话归属查询 Message/ParseJob/ParseResult。
- **不修改**：`SupervisorEnvelope` v1、MessageStore 幂等定义、Schema v2、Parser Worker 生命周期、C-02 原型和证据。

## 3. 总体职责

### 3.1 Extension

Extension 是交互状态所有者：

- 持久保存唯一 Supervisor Session、Endpoint、active/paused 和自动扫描锚点；
- 每次管理操作和提交前实时读取当前标签页 Session；
- 从 DOM 读取 Assistant 消息和生成状态；
- 严格解析指令标记，但不解析 Agent 任务语义；
- 生成并持久保存确定性 `submission_id` 和完整 Envelope 快照；
- 展示连接、接收和解析状态。

绑定对象是 Session，不是 tab。`tab_id` 只用于一次路由与诊断，不作为持久信任依据。

### 3.2 Bridge

Bridge 仅提供：

- token / Origin 校验；
- page client 与唯一 Core client 的连接所有权；
- submission 和 Supervisor control 请求/响应的 request-id 相关性；
- 断线、超时和无 Core 的明确错误。

Bridge 不访问 Endpoint Registry 或 SQLite，不授权状态查询，也不修改 Parser 状态。

### 3.3 Core

Core 是可信身份和处理状态所有者：

- `bind_supervisor`：在已认证 Bridge/Core 通道上幂等注册 `chatgpt_web + native_session_id`，Endpoint ID 只能由 Core 生成；
- 继续通过原 Receiver 接受 `SupervisorEnvelope`；
- `get_submission_status`：验证 Registry 身份与 Message 归属后组合 Message、ParseJob 和 ParseResult 的只读视图；
- 不保存 active/paused/当前 tab 等 Extension UI 状态。

## 4. Supervisor 绑定模型

Extension 使用版本化 `chrome.storage.local` 文档保存：

```json
{
  "schema_version": 1,
  "native_session_id": "<stable ChatGPT Session ID>",
  "endpoint_id": "cfep_...",
  "session_kind": "chat",
  "state": "active",
  "bound_at": "<UTC>",
  "updated_at": "<UTC>",
  "anchor": {
    "message_id": "<latest observed Assistant ID or null>",
    "updated_at": "<UTC>",
    "reason": "bind|resume|observed"
  }
}
```

状态语义：

- 未绑定：无绑定文档；
- active：允许自动提交；
- paused：禁止自动提交，手动提交仍需当前 Session 与绑定一致；
- identity unavailable：当前页尚无稳定 Session；
- mismatch：当前标签页 Session 与绑定 Session 不同；
- Bridge/Core unavailable：身份可读，但控制链路不可用。

操作规则：

1. **绑定**：popup 请求 background 实时探测 active tab；校验允许 Origin、内容脚本可通信、Session 稳定；Core 幂等创建/复用 Endpoint；以当前最新 Assistant ID 建锚。已有不同绑定时显示明确替换确认。
2. **暂停**：只改 Extension 状态；停止自动提交。
3. **恢复**：实时核验当前 Session，重新以当前最新 Assistant ID 建锚，因此暂停期间内容不补交。
4. **解除**：删除 Extension 绑定和自动锚点，不删除 Registry 或 Core 历史数据。
5. **新会话**：没有稳定 Session 时拒绝绑定，提示先完成第一轮会话；不伪造 ID。

管理消息只接受 extension page 发起。Background 校验 `sender.id`、extension Origin 且无网页 `sender.tab`；content script 无权直接绑定、暂停、恢复或解除。

## 5. 指令块协议与严格解析

协议固定为大小写敏感、独占一行：

```text
[CONTEXTFLOW_DISPATCH_V1]
请 Codex 修复 Adapter。
再让 Claude Code 审查并运行测试。
[/CONTEXTFLOW_DISPATCH_V1]
```

### 5.1 词法规则

- 起止标记去除该行外层空白后必须完全相等；标记不能附带参数或正文；
- CRLF/CR 先规范化为 LF；
- CommonMark 风格 fenced code block（最多三个前导空格、至少三个反引号或波浪号；闭合 fence 使用同字符且长度不短于 opening）内的标记全部视为示例文本；
- fence 外只要出现标记 token 但该行不是合法独占标记，即判定歧义；
- block 内再次出现 opening 是嵌套，非法；
- block 外出现 closing、缺少 closing、空 block 均非法；
- 多个顺序、非嵌套且非空的完整 block 合法；重复的一对完整 block 是两个 block，不是语法错误；孤立或连续重复 opening/closing 是错误；
- Assistant 仍在生成时，不论当前文本看似完整与否，都不自动提交。

解析结果为：

- `none`：没有 fence 外标记；
- `valid`：一个或多个完整 block；
- `invalid`：包含歧义、嵌套、半成品、空块或非法标记。

### 5.2 提交正文

合法 block 的正文逐块执行：换行规范化、去除块首尾空白；内部内容保持原顺序；多个块用两个 LF 连接。标记本身不进入 Envelope。

同一 Assistant 消息只创建一个 `SupervisorEnvelope`；多个自然语言 Agent 指令仍由阶段 B Parser 完整验证。标记只选择提交边界，不能指定 Endpoint、Session、MessageId 或其他安全字段。

### 5.3 手动提交

- `valid`：纸飞机使用与自动路径完全相同的提取正文；
- `none`：用户显式点击后提交整条规范化 Assistant 正文；
- `invalid`：第一次点击只显示具体语法问题，不提交；只有用户再次执行明确的“提交整条回复”确认动作，才按整条正文生成独立逻辑提交；
- generating 或缺少稳定 Session/Message ID：禁止提交。

这样既保留人工恢复能力，也不会把歧义标记静默猜成某段指令。

## 6. 统一提交身份与幂等

### 6.1 规范化与 ID

所有提交都由 background 的同一个构造函数产生。逻辑身份输入为 canonical JSON：

```json
[
  "contextflow-supervisor-submission-v1",
  "chatgpt_web",
  "<endpoint_id>",
  "<native_session_id>",
  "<assistant_message_id>",
  "markdown",
  "<normalized submitted text>"
]
```

`submission_id = "sub_" + SHA-256(canonical JSON)`（小写十六进制）。

不加入 `auto/manual` 或 UI 路径，因此同一消息、同一 Endpoint、同一规范化正文通过自动和手动方式得到同一 ID。正文不同必然得到不同 ID，不会静默复用成功记录。`user_message_id` 与 `turn_group_id` 是辅助证据，不参与逻辑 ID，避免 DOM 重排改变逻辑身份；首次提交前持久保存完整 Envelope，所有重试复用该快照。

### 6.2 本地记录

记录键包含 Session、Message 和 payload digest；最多保留 500 条。记录包含：

- `submission_id`、完整 Envelope 快照、触发方式；
- `not_submitted/submitting/accepted/unknown/failed`；
- Core `message_id`（查询获得后）；
- parser 状态摘要；
- retry/query 时间与安全错误摘要。

本地记录是 UI 与恢复缓存，不是接收权威。Core MessageStore 仍以 submission ID + canonical Envelope 判定 duplicate/conflict。

既有 v0.6 随机 submission 记录若 Envelope 规范化正文与当前逻辑提交完全一致，则继续复用原 Envelope/ID，避免历史已接受消息被重新创建；不同正文建立新版本记录，不覆盖旧记录。

### 6.3 未知结果

Envelope 在发送前落盘。超时、页面刷新或断线后：

1. 使用同一 submission ID 和绑定身份调用状态查询；
2. 查到 Message 即转为 Core 已接收并继续轮询 Parser；
3. 查不到且先前结果明确未送达时，可重发同一 Envelope；
4. 无法判定时保持 `unknown`，有界退避后再次查询；
5. 永不为重试生成新 ID。

## 7. 历史边界与多标签安全

绑定和恢复时记录最新已渲染 Assistant 原生 ID 作为高水位锚点。Active 状态下对绑定 Session 的有序快照：

- 找到锚点：只考察锚点之后的消息；每个完成消息无论是否含指令块都推进锚点；合法块提交，普通文本跳过，非法块记录诊断但不提交；
- 锚点缺失：不把当前已完成消息当作新消息；显示“自动提交边界无法恢复”，防止旧标签页/懒加载历史误提交；
- 锚点缺失时若实时观察到某原生消息处于 `in_progress`，可将该 ID 武装；同一 ID 后续变为 complete 时允许处理并恢复锚点；
- pause→resume 总是以当前最新消息重新建锚，不补交暂停期内容；
- Session mismatch 的快照完全不能改变绑定锚点。

Background 是跨标签共享协调点。同 Session 多标签可以使用，但每次候选提交都回读目标 tab 的当前 Session 和消息；确定性 ID、本地记录和 Core 幂等共同消除重复。旧 tab、DOM 重渲染、刷新和重复扫描不能创建第二个 Message。

## 8. Bridge/Core 控制协议

Extension 使用现有 bridge page，不直连 SQLite。新增通用控制帧：

```json
{
  "type": "supervisor_control_request",
  "request_id": "ctl_...",
  "action": "bind_supervisor|get_submission_status|ping_core",
  "params": {}
}
```

Core 返回：

```json
{
  "type": "supervisor_control_result",
  "request_id": "ctl_...",
  "success": true,
  "data": {}
}
```

Bridge 只验证连接所有权、保存短期相关性并中继。晚到响应只能返回原 page client；Core/page replacement 后旧响应作废。Bridge health 不返回 Registry/SQLite 路径或其他不必要内部信息。

### 8.1 `bind_supervisor`

输入：`native_session_id`、`session_kind`。调用只能来自 token + Origin 校验通过的唯一 Bridge/Core 通道。Core 固定 provider=`chatgpt_web`，幂等注册并返回 `endpoint_id`、`created`；请求正文不能指定 Endpoint ID。

### 8.2 `get_submission_status`

输入同时包含：

- `submission_id`；
- 当前绑定 `endpoint_id`；
- `native_session_id`；
- `native_message_id`。

Core 先验证 Registry 中 Endpoint 的 provider/session，再读取 submission；读取后再次验证 Message Envelope 的 endpoint/session/native message 全部归属该绑定。不存在或归属不符返回同一种不泄露式 `not_found`，不能通过可猜测 ID 枚举其他会话。

授权还依赖既有可信调用方边界：查询只存在于已认证 Bridge↔唯一 Core WebSocket 通道，不新增公开、匿名 HTTP 查询 API。

## 9. 状态视图

Core 返回独立维度：

```json
{
  "found": true,
  "submission_id": "sub_...",
  "message_id": "cfmsg_...",
  "core_status": "accepted",
  "parser": {
    "status": "waiting|processing|retry_wait|parsed|unresolved|failed",
    "instruction_count": 2,
    "target_agents": ["Codex", "Claude Code"],
    "error": null,
    "completed_at_ms": 0
  }
}
```

映射：

- Message 存在、ParseJob 不存在或 ready：Core 已接收 / 等待 Parser；
- processing：正在解析；
- retry_wait：等待 Parser 重试；
- completed + parsed/unresolved/failed：展示正式结果；
- 查询超时/断线：保持上次事实并显示“状态未知”，不改写为 failed；
- Worker 未启动：始终是 waiting，可在等待超过阈值后提示检查 Worker，但不制造 failed。

只返回 MessageId、有限状态、指令数、canonical Agent 名称和裁剪后的安全错误；不返回 Supervisor 正文、Provider credential、raw provider JSON、SQLite/Registry 路径、lease owner 或内部堆栈。

## 10. Popup 与页面反馈

正式扩展增加轻量 popup：

- 当前标签页 Session 与可绑定性；
- 已绑定 Supervisor Session；
- active / paused / mismatch / identity unavailable；
- Bridge 与 Core 分级连接状态；
- 绑定/替换、暂停、恢复、解除；
- 最近提交：Core 接收状态、Parser 状态、指令数与目标 Agent；
- 折叠开发者详情：SessionId、EndpointId、MessageId、submission ID、安全错误码。

页面纸飞机保留并复用统一 background 提交流程。按钮展示 submitting、Core 已接收、等待 Parser、解析成功、unresolved、failed、unknown 和标记非法；主界面不直接展示数据库字段名。

## 11. 错误处理与恢复

- 非允许 Origin、content script 不可达、Session 不稳定或 mismatch：操作前失败；
- Core bind 成功但响应丢失：重试同一 session 的幂等 bind；
- submission timeout：查询同一逻辑身份，不新建；
- accepted ack 丢失：Core 状态查询恢复 MessageId；
- Bridge/Core 恢复：background/popup 使用原记录重新查询；
- Parser 等待不等于失败；
- ParseResult 为 unresolved/failed 时只显示安全摘要，不修改 Parser；
- Endpoint Registry 或数据库错误 fail closed，不回退到页面提供的身份。

## 12. 测试设计

### 12.1 纯协议与 Core 自动化

- Endpoint bind 幂等、Core 生成 ID、非法请求；
- 状态查询的 Registry + Message 四字段归属校验；
- 猜测 submission/Message ID、其他 Session/Endpoint 不泄露；
- Message 无 job、ready、processing、retry_wait、parsed、unresolved、failed 映射；
- Bridge control correlation、late result、replacement page/Core、无 receiver、非法 Origin；
- 阶段 A submission duplicate/conflict、Schema v2 与阶段 B Parser 回归。

### 12.2 Extension/Mock 浏览器自动化

- 单绑定、替换确认、新会话无 ID、刷新恢复、mismatch、暂停/恢复/解除；
- 完整块、多块、普通回复、fenced 示例、嵌套、孤立 closing、缺失 closing、空块、inline/变形标记、生成中半成品；
- 自动/手动同 payload 得到同 submission ID；不同 payload 得到不同 ID；
- 重复点击、DOM rerender、刷新、扩展 reload、同 Session 多 tab；
- 绑定锚点、pause/resume 锚点、旧 tab、anchor missing fail closed、in-progress re-arm；
- ack 丢失→查询恢复；Bridge/Core 断开与恢复；
- popup 和纸飞机的接收/解析/unknown 状态。

### 12.3 完整 Fake Parser 闭环

使用隔离 Schema v2 数据库和 Fake Provider：

```text
Mock ChatGPT page → real unpacked Extension → real Bridge
→ real Core Receiver/Control → real MessageStore
→ real Parser Worker + Fake Provider → real ParseResult
→ Core status query → popup/page state
```

验证两目标顺序、刷新不重复、暂停不自动提交，并记录 artifact。

### 12.4 真实验收边界

提供真实 ChatGPT 页面人工步骤和证据位置。未经单独确认：

- 不自动向真实 ChatGPT 会话发送测试提示；
- 不调用付费 Provider；
- 不能以 Mock 浏览器或 Fake Provider 冒充真实页面/真实模型通过。

若当前环境只能观察现有真实回复，则只做不产生外部内容的绑定、手动选择、状态读取或明确标记待人工步骤。

## 13. 文件与交付物预期

预期修改：

- `contextflow-extension/manifest.json`
- `contextflow-extension/dispatch_protocol.js`（新）
- `contextflow-extension/content.js`
- `contextflow-extension/background.js`
- `contextflow-extension/bridge_relay.js`
- `contextflow-extension/bridge_server.py`
- `contextflow-extension/popup.html`（新）
- `contextflow-extension/popup.js`（新）
- `contextflow-extension/popup.css`（新）
- `contextflow_core/supervisor_control.py`（新）
- `contextflow_core/receiver.py`
- 相关 Python / Playwright 测试、使用文档
- `reports/C-01-supervisor-binding.md`

实施使用独立 C-01 worktree/分支，不触碰 C-02 artifacts，不 commit、push、merge或清理用户未跟踪文件。

## 14. 阶段 C 可复用接口

阶段 C 可复用：

- Supervisor Session→Endpoint 的受控幂等绑定；
- submission→Message→正式 ParseResult 的授权只读状态；
- MessageId、ParseResult、目标 Agent 摘要；
- Extension 的可靠提交身份和状态恢复。

C-01 不把查询接口扩展成 Task 创建入口，也不改变 ParseResult 的正式结果契约。
