# ContextFlow ChatGPT Adapter v0.6.0（CF-DEV-001 + CF-ADAPTER-001）

ChatGPT 网页适配器：读取会话/消息、观察生成状态、**手动提交 Assistant 回复到本地 Bridge**，并支持**按 Endpoint ID 反向发送**。

- 不读取凭据、不调用 ChatGPT 内部 API、不自动发送。
- 纸飞机只在用户点击时提交当前 Assistant 消息。
- 所有提交都经过本地 Bridge（`127.0.0.1:8766`）和外部接收端确认。

## 安装（Edge 开发者模式）

1. 打开 `edge://extensions`；
2. 开启 **开发人员模式**；
3. 点 **加载解压缩的扩展**，选择本目录 `contextflow-extension/`；
4. 打开任意 ChatGPT 会话页；
5. 另开一个常驻标签页：`http://127.0.0.1:8766/bridge_client.html?token=dev-token-cf-exp-002`。

> 扩展 reload 后，已打开的 ChatGPT 标签页需要刷新一次才能注入新内容脚本。
> 扩展目录中不要出现 `__pycache__` 等以 `_` 开头的文件/目录，否则 Edge 会拒绝加载。

## 功能

### 1. 读取（CF-DEV-001）

- 会话识别、结构化消息、生成状态；
- 调试：`__cfAdapter.snapshot()` / `readMessages()` / `getGenerationState()`。

### 2. 手动提交（CF-ADAPTER-001）

- 每条 Assistant 回复的原生操作栏旁注入纸飞机按钮；
- 点击后构造标准 Envelope，经 Bridge 提交给外部接收端；
- 只有收到有效 `accepted` 才显示“已送达”；
- 失败/未知/重试/刷新恢复语义见下。

### 3. 反向发送

- Bridge 按 Endpoint ID 解析目标会话；
- 严格匹配标签页，0 个报 `SESSION_NOT_FOUND`，多个报 `AMBIGUOUS_SESSION`，不回退活动标签页；
- 发送后轮询新 User 消息验证，返回 `prepared / sent / verified / user_message_id`。

## 纸飞机状态

| 状态 | UI | 说明 |
|---|---|---|
| `idle` | 纸飞机 | 可提交 |
| `submitting` | 禁用 + 加载 | 等待确认 |
| `accepted` | 对勾 + “已送达” | 收到有效确认，约 2 秒后回 idle |
| `failed` | 警告 + “提交失败，点击重试” | 明确失败，可安全重试 |
| `unknown` | 问号 + “结果未知，点击重试” | 超时/断线，不判定失败 |
| `disabled` | 禁用 | 缺少原生 ID 或回复生成中 |

- 同一逻辑提交只生成一个 `submission_id`，重试复用；
- 提交记录存 `chrome.storage.local`，键为 `native_session_id:message_id`，刷新后按原生 ID 恢复；
- 页面在提交过程中刷新时，记录变为 `unknown`，可重试。

## Envelope

```json
{
  "schema_version": 1,
  "submission_id": "sub_<uuid>",
  "source": {
    "adapter": "chatgpt_web",
    "endpoint_id": "cfep_<id>",
    "native_session_id": "<ChatGPT 会话 ID>",
    "message_id": "<Assistant 原生消息 ID>",
    "user_message_id": "<同回合 User 消息 ID 或 null>",
    "turn_group_id": "<DOM 回合标识或 null>"
  },
  "content": { "format": "markdown", "text": "<完整正文>" }
}
```

缺失原生 ID 时按钮禁用，不伪造 ID。

## 调试

**Content Script（页面 Console）**

- `[ContextFlow:cs] content v0.6.0 ready on <url>`
- `[ContextFlow:cs] report(...) session=... msgs=... gen=...`
- `__cfAdapter.snapshot()` / `readMessages()` / `getGenerationState()`
- `__cfAdapter.debugActionBars()` / `clickSubmitButton(messageId)`

**Background（Service Worker Console）**

- `[ContextFlow:bg]` 日志；
- `__cfBg.getState()`。

**Bridge**

- `GET http://127.0.0.1:8766/health`：页面连接、外部客户端、pending 请求/提交；
- `POST /internal/reload_page`（header `x-cf-token: dev-token-cf-exp-002`）：重载 bridge 页面，修复 relay。

## 测试

```powershell
cd D:\CortexFlow\contextflow-research
python -m pytest tests -q                                  # 66 passed
python experiments/adapter-mock/adapter_protocol_test.py   # 8/8
python experiments/playwright-probe/cfadapter001_test.py   # 19/19
```

真实页面验证与完整报告见 `reports/CF-ADAPTER-001.md`。

## 已知限制

- 只能读取**当前已渲染**的消息；长会话历史需滚动到可见。
- `turn_group_id` 是 DOM 结构证据，不是永久业务 ID。
- `user_message_id` 无法唯一确定时为 `null`，不猜测。
- 提交需要当前会话已绑定 Endpoint；未绑定时明确失败。
- Bridge 页面是单页面客户端；同一浏览器 profile 只需一个。
- 提交当前广播给所有外部客户端；未来 Core 接入时应改为按接收方路由。
- 扩展 reload 后旧标签页需要刷新。
