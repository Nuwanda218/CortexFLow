# CF-ADAPTER-001｜Supervisor Adapter 接口与 Envelope 协议

本文档面向未来接入 Core 的开发者，说明 Adapter 对外可用的接口、Envelope 格式、确认语义和 Mock Core 测试方法。

## 1. 角色边界

```text
Core（未来）  ──dispatch/send──▶  Bridge  ──▶  Adapter（浏览器扩展）──▶ ChatGPT 页面
Core（未来）  ◀──submit/ack────  Bridge  ◀──  Adapter（浏览器扩展）◀── 用户点击纸飞机
```

Adapter 只负责：

1. 从 ChatGPT 页面读取原生会话/消息身份与正文；
2. 用户点击时构造 Envelope 并提交；
3. 等待接收端确认并更新按钮状态；
4. 接收 Bridge 的反向发送请求，写入指定会话并验证。

Adapter 不负责：任务拆分、调度、持久化业务状态、权限、LLM 解析、正式队列。

## 2. 正向接口：提交 Assistant 消息

### 2.1 触发方式

用户在 ChatGPT Assistant 回复的原生操作栏旁点击纸飞机按钮。Adapter 自动完成：

1. 读取当前 Assistant 消息的 `message_id`、`native_session_id`、`user_message_id`、`turn_group_id`；
2. 读取当前已渲染正文并规范化为 Markdown；
3. 通过 Bridge 只读查询 `native_session_id → endpoint_id`；
4. 生成 `submission_id`（一次逻辑提交只生成一次）；
5. 通过 Bridge 广播 Envelope；
6. 等待 `submission_ack`。

### 2.2 Envelope

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
    "turn_group_id": "<DOM 回合标识或 null>"
  },
  "content": {
    "format": "markdown",
    "text": "<Assistant 完整规范化正文>"
  }
}
```

字段约束：

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定 `1` |
| `submission_id` | 是 | `sub_` + 32 位十六进制；重试复用 |
| `source.adapter` | 是 | 固定 `chatgpt_web` |
| `source.endpoint_id` | 是 | `cfep_` + 32 位十六进制；来自 Registry |
| `source.native_session_id` | 是 | 非空；来自 URL |
| `source.message_id` | 是 | 非空；来自原生 DOM 属性，不伪造 |
| `source.user_message_id` | 否 | 无法唯一确定时为 `null` |
| `source.turn_group_id` | 否 | DOM 结构证据，可为 `null` |
| `content.format` | 是 | 固定 `markdown` |
| `content.text` | 是 | 非空 |

### 2.3 确认

```json
{"submission_id": "sub_xxx", "status": "accepted", "duplicate": false}
```

| 状态 | 含义 |
|---|---|
| `accepted` | 接收端已接受；`duplicate=true` 表示同一 ID+内容重复提交 |
| `rejected` | 非法 Envelope、无接收端、Endpoint 未绑定等；`error.code` 说明原因 |
| `conflict` | 同一 `submission_id` 但内容不同；不覆盖原记录 |

`accepted` 只代表接收端已接受，不代表任务已派发或执行完成。

### 2.4 幂等与冲突

- 同一 `submission_id` + 相同内容：返回 `accepted`，`duplicate=true`，不重复入库；
- 同一 `submission_id` + 不同内容：返回 `conflict`，`error.code=SUBMISSION_CONFLICT`；
- 非法 Envelope：返回 `rejected`，`error.code=INVALID_ENVELOPE`；
- 无外部接收端：返回 `rejected`，`error.code=NO_RECEIVER`。

## 3. 反向接口：按 Endpoint ID 发送

外部客户端通过 Bridge 发送：

```json
{
  "id": "send-1",
  "type": "send_message_to_endpoint",
  "endpoint_id": "cfep_...",
  "text": "要发送的消息",
  "force": false
}
```

响应：

```json
{
  "id": "send-1",
  "type": "result",
  "success": true,
  "data": {
    "tab_id": 123,
    "prepared": true,
    "sent": true,
    "verified": true,
    "method": "shift+enter",
    "session_id": "<ChatGPT 会话 ID>",
    "user_message_id": "<新 User 消息 ID 或 null>"
  }
}
```

错误码：

| 错误码 | 含义 |
|---|---|
| `ENDPOINT_NOT_FOUND` | Registry 中无此 Endpoint |
| `SESSION_NOT_FOUND` | 没有打开的标签页显示该会话 |
| `AMBIGUOUS_SESSION` | 同一会话在多个标签页打开；必须显式指定 `tab_id`，不回退 |
| `COMPOSER_NOT_FOUND` | 找不到输入框 |
| `DRAFT_PRESENT` | 输入框已有草稿且未 `force` |
| `SEND_UNVERIFIED` | 已尝试发送但无法确认新消息出现；不自动重试 |

## 4. Bridge 协议（外部客户端视角）

连接：`ws://127.0.0.1:8766/ws`，首帧：

```json
{"type": "hello", "token": "dev-token-cf-exp-002", "client": "core"}
```

接收提交广播：

```json
{"type": "submission", "envelope": {...}}
```

发送确认：

```json
{"type": "submission_ack", "submission_id": "sub_xxx", "status": "accepted", "duplicate": false}
```

查询 Endpoint：

```json
{"id": "q1", "type": "endpoint_lookup", "session_id": "<native_session_id>"}
```

响应：

```json
{"id": "q1", "type": "result", "success": true, "data": {"endpoint_id": "cfep_..."}}
```

## 5. Mock Core 启动与测试

### 5.1 启动接收端

```powershell
cd D:\CortexFlow\contextflow-research
python experiments/adapter-mock/mock_core.py --store _artifacts/cfadapter001-submissions.json
```

可选参数：

| 参数 | 说明 |
|---|---|
| `--store PATH` | 落盘文件，默认 `_artifacts/cfadapter001-submissions.json` |
| `--ack-delay SECONDS` | 延迟确认，模拟慢接收端 |
| `--drop-first N` | 前 N 条不确认，模拟丢失确认 |
| `--once` | 收到一条后退出 |

### 5.2 查看已接收

```powershell
python experiments/adapter-mock/mock_core.py --store _artifacts/cfadapter001-submissions.json --list
```

### 5.3 反向发送

```powershell
python experiments/adapter-mock/mock_core.py `
  --send-endpoint cfep_... `
  --text "ContextFlow reverse send test"
```

### 5.4 协议级测试（无浏览器）

```powershell
python experiments/adapter-mock/adapter_protocol_test.py
```

### 5.5 浏览器自动化测试

```powershell
python experiments/playwright-probe/cfadapter001_test.py
```

## 6. 测试钩子（dev-only）

Bridge 暴露以下仅用于测试的请求，生产 Core 不应依赖：

| 请求 | 用途 |
|---|---|
| `debug_action_bars` | 列出当前页面 Assistant 回合、message_id、按钮状态 |
| `click_submit_button` | 按 message_id 点击纸飞机 |
| `reload_tab` | 重载指定标签页 |
| `POST /internal/reload_page` | 重载 bridge 页面 relay |

## 7. 失败与重试语义

- **明确失败**（`rejected` / 未绑定 Endpoint / 无接收端）：按钮 `failed`，可重试；
- **结果未知**（超时 / 连接中断）：按钮 `unknown`，不判定失败，不生成新 `submission_id`；
- **重试**：复用原 `submission_id` 和原 Envelope 快照；接收端幂等；
- **页面刷新**：按 `native_session_id:message_id` 恢复记录；在途提交变为 `unknown`；
- **重复点击**：已 `accepted` 只回放状态，不创建新逻辑提交。
