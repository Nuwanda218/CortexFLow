# CF-ADAPTER-001｜ChatGPT Supervisor Adapter 手动消息提交 MVP

- 日期：2026-11-06
- 扩展版本：v0.6.0
- Bridge 版本：v0.3.0
- 范围：只做 ChatGPT Supervisor Adapter（读取 + 手动提交 + 反向发送），不实现 Core、Agent、队列、权限或任务状态机。

## 1. 结论

已完成一条可独立验证的双向通信链路：

```text
ChatGPT Assistant 消息
        │ 用户点击纸飞机
        ▼
Adapter（content.js）提取正文 + 原生身份
        │ 标准 Envelope
        ▼
background.js → bridge_client.html → bridge_server.py
        │
        ▼
Python Mock Core（mock_core.py）
        │ 校验 / submission_id 幂等 / 落盘
        ▼
accepted 确认 → 原路返回 → 纸飞机变为“已送达”
```

反向链路也已验证：

```text
Python 脚本（Endpoint ID）
        ▼
bridge_server.py（Endpoint Registry 只读解析）
        ▼
background.js（严格 session 路由，不回退活动标签页）
        ▼
content.js（prepare_message → send → snapshot 验证）
        ▼
指定 ChatGPT 会话
```

**自动化证据**：Python 66/66、协议级 8/8、Playwright 浏览器 19/19。
**真实页面证据**：真实 ChatGPT 页面注入纸飞机按钮；真实点击产生的 Envelope 被 Mock Core 接收并确认；Bridge 中断时按钮正确显示失败，不显示虚假“已送达”。

## 2. 修改的文件与职责

| 文件 | 变更 |
|---|---|
| `contextflow-extension/content.js` | v0.6.0：Assistant 操作栏纸飞机注入、Envelope 构造、提交状态机、`chrome.storage.local` 恢复、反向发送处理、127.0.0.1 页面 relay 兜底 |
| `contextflow-extension/background.js` | v0.6.0：`cf:submit_envelope`、`cf:resolve_endpoint`、`send_message_to_endpoint`、`debug_action_bars`、`click_submit_button`、`reload_tab`；桥接页健康探测 |
| `contextflow-extension/bridge_relay.js` | 增加 extension → bridge page 的提交/Endpoint 查询中继与 `cf:bridge_ping` |
| `contextflow-extension/bridge_server.py` | v0.3.0：提交广播与确认路由、Endpoint Registry 只读解析、`send_message_to_endpoint`、页面自愈重载、健康信息 |
| `contextflow-extension/manifest.json` | v0.6.0，描述更新 |
| `contextflow_core/submission.py`（新） | Envelope 严格校验、fingerprint、`MockCoreReceiver` 幂等/冲突/落盘 |
| `contextflow_core/__init__.py` | 导出提交协议 API |
| `tests/adapter/test_submission.py`（新） | 20 项 Envelope/幂等/冲突/持久化测试 |
| `tests/adapter/test_bridge_protocol.py`（新） | 7 项真实 Bridge WebSocket 协议集成测试 |
| `experiments/adapter-mock/mock_core.py`（新） | 可独立运行的 Mock Core / 外部接收端 + 反向发送测试 |
| `experiments/adapter-mock/adapter_protocol_test.py`（新） | 无浏览器协议级脚本测试（8 项） |
| `experiments/playwright-probe/cfadapter001_test.py`（新） | 真实扩展 + Mock ChatGPT 页面自动化测试（19 项） |
| `_artifacts/cfadapter001-mock/mock.html`（新） | 支持动态渲染、重挂载、缺失 ID、同回合多回复的 Mock 页面 |

未修改 Endpoint Registry 的持久化格式、Endpoint ID 生成规则或既有 Bridge 请求/响应协议；新增能力均为增量。

## 3. Envelope 协议

```json
{
  "schema_version": 1,
  "submission_id": "sub_<uuid>",
  "source": {
    "adapter": "chatgpt_web",
    "endpoint_id": "cfep_<id>",
    "native_session_id": "<ChatGPT 原生会话 ID>",
    "message_id": "<Assistant 原生消息 ID>",
    "user_message_id": "<同回合 User 原生消息 ID 或 null>",
    "turn_group_id": "<DOM 推导的回合标识或 null>"
  },
  "content": {
    "format": "markdown",
    "text": "<Assistant 完整规范化正文>"
  }
}
```

字段语义与缺失处理：

- `submission_id`：一次逻辑提交的唯一 ID。首次点击生成；失败/未知后重试复用同一个 ID。
- `endpoint_id`：通过 Bridge 只读查询 CF-CORE-001 Registry 得到；未绑定时明确失败 `ENDPOINT_NOT_BOUND`，不伪造。
- `native_session_id`：来自 URL 的稳定会话 ID；缺失（新对话/draft）时禁止提交。
- `message_id`：来自 `data-chatgpt-search-message-ids` / `data-dil-message-id` 的原生 ID；**缺失时按钮禁用并显示“缺少消息 ID”，不伪造、不提交**。
- `user_message_id`：同 `turn_group_id` 下唯一 User 回合的原生 ID；无法可靠确定时为 `null`。
- `turn_group_id`：明确标注为 DOM 结构证据，不是永久业务 ID；页面变化时允许变化。
- `content.text`：当前 Assistant 渲染内容的 Markdown 规范化结果；只代表已渲染 DOM。

校验规则（`contextflow_core.submission.validate_envelope`）：

- 顶层/`source`/`content` 字段集合严格匹配，未知字段拒绝；
- `schema_version == 1`；
- `submission_id` 匹配 `sub_<id>`，`endpoint_id` 匹配 `cfep_<id>`；
- `adapter == "chatgpt_web"`，`content.format == "markdown"`；
- `message_id`、`native_session_id`、`content.text` 非空；
- `user_message_id`、`turn_group_id` 允许 `null`。

确认结构：

```json
{"submission_id": "sub_xxx", "status": "accepted", "duplicate": false}
```

- 同一 `submission_id` + 同一内容：返回 `accepted`，`duplicate=true`，不重复入库；
- 同一 `submission_id` + 不同内容：返回 `conflict` + `SUBMISSION_CONFLICT`，不覆盖原记录；
- 非法 Envelope：返回 `rejected` + `INVALID_ENVELOPE`，不入库；
- 无外部接收端：返回 `rejected` + `NO_RECEIVER`；
- 确认只代表接收端已接受，不代表任务已派发或执行。

## 4. 纸飞机状态机

| 状态 | 触发 | UI |
|---|---|---|
| `idle` | 初始/无记录 | 纸飞机图标 |
| `submitting` | 点击后、等待确认 | 禁用按钮 + 轻量加载 |
| `accepted` | 收到有效 `accepted` | 对勾图标 + “已送达”（约 2 秒） |
| `failed` | 明确拒绝/无接收端/未绑定 Endpoint | 警告图标 + “提交失败，点击重试” |
| `unknown` | 超时/连接中断，结果未知 | 问号图标 + “结果未知，点击重试” |
| `disabled` | 缺少原生 ID / 回复生成中 | 禁用 + 原因提示 |

关键行为：

- 重复点击已 `accepted` 的消息只回放“已送达”，不创建新逻辑提交；
- 安全重试复用原 `submission_id` 和原 Envelope 快照；
- 超时/断线不判定为失败，不生成新 ID；
- 提交记录写入 `chrome.storage.local`，键为 `native_session_id:message_id`，页面刷新后按原生 ID 恢复，不依赖 DOM 下标或顺序；
- 若页面在提交过程中刷新，记录显示为 `unknown`，可安全重试。

### 4.1 “已送达”闪烁修复

问题：MutationObserver 每 250ms 重新应用一次按钮状态，`setButtonState` 每次都重建 `innerHTML`，导致“✓ 已送达”和“✓”之间反复切换。

修复：

- `setButtonState` 增加幂等判断：state / label / title / aria-label 都没变时直接返回，不重建 DOM；
- “已送达” toast 只在“点击 → 收到 accepted”这一次状态跃迁时显示；
- 用 `data-cf-toast-until` 记录 toast 截止时间，MutationObserver 重应用状态时保留 toast，直到计时器自然结束；
- toast 结束后只保留对勾图标，不再反复显示文字。

### 4.2 反向发送误判草稿修复

问题：ChatGPT 页面同时存在两个 ProseMirror 编辑器——聊天输入框（`aria-label="询问 ChatGPT"`）和“写作”编辑器（`aria-label="开始写作"`）。旧 `findComposer()` 取第一个 `div.ProseMirror[contenteditable="true"]`，命中了写作编辑器，于是把 4575 字的写作内容误判为聊天草稿，反向发送返回 `DRAFT_PRESENT`。

修复：

- 选择器优先级改为 `#prompt-textarea` → `div.ProseMirror[data-composer-markdown]` → `form div.ProseMirror` → `form [contenteditable][role=textbox]` → `form textarea`；
- 显式排除 `aria-label="开始写作"` 及 writing/canvas 容器；
- `get_composer` 返回值增加 `aria_label`，便于确认命中的是哪个编辑器。

### 4.3 后台标签页路由修复

问题：ChatGPT 标签页在后台被 Edge 休眠/丢弃后，content script 不再响应 `probeSession`，反向发送返回 `SESSION_NOT_FOUND`；把标签页切到前台后立即成功。

修复：

- 用 URL 中的 `/c/<session_id>` 识别标签页，不再只依赖 content script 响应；
- 对 ChatGPT 标签页设置 `autoDiscardable: false`，减少被休眠的概率；
- 如果 URL 命中但 content script 未响应，自动 `chrome.tabs.update(tabId, {active:true})` 唤醒并重试探测，操作完成后恢复用户原来的活动标签页；
- 唤醒后仍无响应时返回明确的 `CONTENT_SCRIPT_UNAVAILABLE`，不静默回退到其他会话。

## 5. 反向发送接口

外部脚本通过 Bridge 发送：

```json
{"id": "send-1", "type": "send_message_to_endpoint",
 "endpoint_id": "cfep_...", "text": "...", "force": false}
```

Bridge 行为：

1. 只读解析 `endpoint_id → native_session_id`；未知 Endpoint 返回 `ENDPOINT_NOT_FOUND`；
2. 把请求转给浏览器页面；
3. background 按 `session_id` 严格匹配标签页：0 个 → `SESSION_NOT_FOUND`，多个 → `AMBIGUOUS_SESSION`，**绝不回退活动标签页**；
4. content script 执行 `prepare_message`（默认不覆盖草稿）→ `sendCurrentComposer` → 轮询新 User 消息验证；
5. 返回 `prepared / sent / verified / user_message_id`；发送结果不明确时返回 `SEND_UNVERIFIED`，不自动重试。

## 6. 自动化测试结果

### 6.1 Python 单元/集成测试

```powershell
cd D:\CortexFlow\contextflow-research
python -m pytest tests -q
# 66 passed
```

其中新增：

- `tests/adapter/test_submission.py`：20 项（Envelope 校验、缺失 ID、幂等、冲突、非法输入、持久化重载）；
- `tests/adapter/test_bridge_protocol.py`：7 项（真实 Bridge 进程 + 两个 WebSocket 客户端：Endpoint 查询、提交路由、确认路由、重复确认、无接收端、反向发送、未知 Endpoint）。

### 6.2 无浏览器协议脚本

```powershell
python experiments/adapter-mock/adapter_protocol_test.py
# 8/8 checks passed
```

证据：`_artifacts/cfadapter001-protocol-evidence.json`。

### 6.3 真实扩展 + Mock 页面（Playwright）

```powershell
python experiments/playwright-probe/cfadapter001_test.py
# 19/19 checks passed
```

证据：`_artifacts/cfadapter001-browser-evidence.json`。

覆盖：

| 编号 | 场景 | 结果 |
|---|---|---|
| T1 | 只对 Assistant 注入按钮 | PASS |
| T2 | 原生操作栏按钮保留 | PASS |
| T3 | 按钮绑定正确原生 message_id | PASS |
| T4 | 点击 → Envelope → accepted → “已送达” | PASS |
| T5 | 重复点击不产生新提交 | PASS |
| T6 | 同回合多个 Assistant 回复独立选择 | PASS |
| T7 | DOM 重渲染后恢复 accepted | PASS |
| T8 | 页面刷新后恢复 accepted | PASS |
| T9 | 缺失原生 ID → 禁用、不提交 | PASS |
| T10 | 未绑定 Endpoint → 明确失败、不提交 | PASS |
| T11 | 丢失确认 → unknown；重试复用 submission_id 并被接受 | PASS |
| T12 | Bridge 断开 → 失败状态、无虚假“已送达” | PASS |
| T13 | 多标签/多会话不串提交 | PASS |
| T14 | 反向发送按 Endpoint ID 到达目标会话并验证 | PASS |
| T15 | 同会话多标签 → AMBIGUOUS_SESSION，不回退 | PASS |
| T16 | 未知 Endpoint → ENDPOINT_NOT_FOUND | PASS |

## 7. 真实 ChatGPT 页面验证

环境：用户真实登录态 Edge，真实 ChatGPT Project 会话，真实本地 Bridge（127.0.0.1:8766）。

### 7.1 真实页面注入

- 真实会话 `6aeb4db5-d930-83ee-8b14-0cf74ef0a84d` 中检测到 5 条 Assistant 回复，全部注入纸飞机按钮；
- 按钮与原生 message_id 绑定，例如 `6c77e485-eaa2-4253-bced-54621e3fcfeb`；
- 原生“复制/评价回复”等按钮保留；
- 截图：`_artifacts/screenshots/real-paper-plane.png`（右侧纸飞机图标）。

### 7.2 真实提交（成功）

点击真实页面纸飞机后，Mock Core 收到并接受：

```json
{
  "schema_version": 1,
  "submission_id": "sub_44aa77731cbc4364a5a6f42c6c47d239",
  "source": {
    "adapter": "chatgpt_web",
    "endpoint_id": "cfep_0c9463663ee6465c8c98484186f9682b",
    "native_session_id": "6aeb4db5-d930-83ee-8b14-0cf74ef0a84d",
    "message_id": "6c77e485-eaa2-4253-bced-54621e3fcfeb",
    "user_message_id": "4217a044-9804-42a5-bc31-e7a584db7a75",
    "turn_group_id": "fallback-turn-4"
  },
  "content": {
    "format": "markdown",
    "text": "我建议给 DSH 再补充一份开发边界说明……（完整正文已落盘，报告中截断）"
  }
}
```

确认：`{"submission_id": "sub_44aa77731cbc4364a5a6f42c6c47d239", "status": "accepted", "duplicate": false}`。

原始证据：`_artifacts/cfadapter001-real-submissions.json`（含完整正文，属于用户自己的会话内容，未提交到版本库）；脱敏摘要：`_artifacts/cfadapter001-real-evidence.json`。

### 7.3 真实页面失败处理

在 Bridge relay 不可用的状态下点击真实纸飞机，按钮进入 `failed`，标题为 `no acknowledgement within 6000ms`，**没有显示“已送达”**。这验证了“只有收到有效确认才显示成功”的约束。

### 7.4 未完全验证的真实场景

以下场景已在 Mock 浏览器测试中通过，但未在真实 ChatGPT 页面逐项复现，报告中明确标注：

- 真实页面刷新后按钮状态恢复（Mock T8 通过；真实页面因测试期间 ChatGPT 标签页被浏览器休眠/重载，未取得稳定截图）；
- 真实页面同会话多标签的 `AMBIGUOUS_SESSION`（Mock T15 通过；真实环境出现过多标签，但未在最终一轮复现）；
- 真实页面丢失确认后的 unknown → 重试幂等（Mock T11 通过）；
- 真实页面反向发送（Mock T14 通过；真实发送会向用户会话写入测试消息，本次未执行，避免污染真实对话）。

## 8. 运行方法

### 8.1 启动 Bridge

```powershell
cd D:\CortexFlow\contextflow-research\contextflow-extension
python bridge_server.py --port 8766
```

### 8.2 打开 Bridge 中继页（必须常开）

```text
http://127.0.0.1:8766/bridge_client.html?token=dev-token-cf-exp-002
```

### 8.3 启动 Mock Core（接收端）

```powershell
cd D:\CortexFlow\contextflow-research
python experiments/adapter-mock/mock_core.py --store _artifacts/cfadapter001-submissions.json
```

### 8.4 绑定 Endpoint（一次性）

```python
import asyncio
from contextflow_core import EndpointRegistry, EndpointService, WebSocketChatGPTAdapter

async def main():
    adapter = WebSocketChatGPTAdapter()
    service = EndpointService(adapter, EndpointRegistry())
    try:
        sessions = await service.discover()
        target = next(s for s in sessions if s.bindable)
        bound = await service.bind(target.native_session_id)
        print(bound.endpoint.endpoint_id)
    finally:
        await adapter.close()

asyncio.run(main())
```

### 8.5 查看收到的提交

```powershell
python experiments/adapter-mock/mock_core.py --store _artifacts/cfadapter001-submissions.json --list
```

### 8.6 反向发送测试

```powershell
python experiments/adapter-mock/mock_core.py `
  --send-endpoint cfep_... `
  --text "ContextFlow reverse send test"
```

## 9. 已知限制

1. **Endpoint 绑定是提交前提**：纸飞机需要当前会话已在 CF-CORE-001 Registry 中绑定；未绑定时明确失败，不自动创建 Endpoint。
2. **只提交已渲染的 Assistant 消息**：长会话懒加载的历史消息需滚动到可见后才能注入按钮。
3. **`turn_group_id` 是 DOM 结构证据**，不是永久业务 ID；页面改版可能变化。
4. **`user_message_id` 可能为 null**：同回合 User 消息无法唯一确定时不猜测。
5. **页面刷新期间的在途提交**：记录会变为 `unknown`，需要用户重试；重试复用原 `submission_id`，接收端幂等。
6. **Bridge 页面是单页面客户端**：多个浏览器 profile 需要各自打开 bridge_client.html；同一时间只有一个页面客户端。
7. **提交广播给所有外部客户端**：当前 Mock Core 是唯一接收端；未来 Core 接入时应改为按接收方路由。
8. **真实页面反向发送未执行**：为避免向用户真实会话写入测试消息，本次只做了 Mock 浏览器验证；接口与严格路由已实现。
9. **扩展 reload 后旧标签页需要刷新**：这是 MV3 内容脚本的固有限制，已在 README 中说明。

## 10. 后续建议

- 由 Core 实现正式的 submission 接收、持久化幂等和投递队列；Adapter 只保留“提交 + 确认”语义。
- 为 Bridge 增加接收方注册/路由，避免广播。
- 在真实页面补做反向发送与刷新恢复的逐项录屏/截图。
- 若需要跨浏览器 profile，考虑把 bridge 页面改为扩展内页面或使用原生消息。
- 将 `click_submit_button`、`debug_action_bars`、`reload_tab` 等测试钩子标记为 dev-only，生产构建中移除。
