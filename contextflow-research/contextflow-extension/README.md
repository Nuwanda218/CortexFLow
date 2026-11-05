# ContextFlow ChatGPT Adapter v1（CF-DEV-001）

只读 ChatGPT 网页适配器：识别会话、读取结构化消息、观察生成状态。
**不发送消息、不读取凭据、不调用内部 API、不需要本地服务。**

## 安装（Edge 开发者模式）

1. 打开 `edge://extensions`；
2. 右下角开启 **开发人员模式**；
3. 点 **加载解压缩的扩展**，选择本目录 `contextflow-extension/`；
4. 打开任意 ChatGPT 会话页。

## 调试

**Content Script（页面 Console）**

1. 在 ChatGPT 页面按 F12 → Console；
2. 看 `[ContextFlow:cs]` 日志：
   - `content v0.1.0 ready on <url>` —— 注入成功；
   - `report(<reason>) session=... msgs=... gen=...` —— 每次状态变化；
   - `SPA navigation -> <url>` —— 会话切换；
3. 手动读取：
   - `__cfAdapter.snapshot()` —— 完整结构化快照（session/generation/messages）；
   - `__cfAdapter.readMessages()` —— 消息数组；
   - `__cfAdapter.getGenerationState()` —— 生成状态与证据。

**Background（Service Worker Console）**

1. `edge://extensions` → 本扩展 → 点 **Service Worker** 链接；
2. 看 `[ContextFlow:bg]` 日志：每个标签页的最新快照；
3. `__cfBg.getState()` —— 全部标签页状态（含消息数组，仅调试用）。

## 数据结构

快照（content → background）：

```json
{
  "version": "0.1.0",
  "at": "2026-11-05T05:00:00.000Z",
  "reason": "mutation | spa_navigation | heartbeat | document_idle | manual",
  "page": { "url": "https://chatgpt.com/..." },
  "session": {
    "session_id": "<uuid 或 null>",
    "kind": "chat | project | new_chat | unknown",
    "url": "…",
    "source": "url:chat | url:project_chat | dom:… | null",
    "candidates": { "selection": "local-chatgpt:…", "above_composer": "chatgpt:…" }
  },
  "generation": {
    "state": "idle-free | generating | completed | unknown",
    "evidence": { "turn_states": ["complete", "…"], "stop_button": false }
  },
  "messages": [
    {
      "order": 0,
      "role": "user | assistant | null",
      "turn_key": "fallback-turn-0:0:user",
      "message_ids": ["<uuid>"],
      "session_id": "<uuid>",
      "content": "Markdown 文本",
      "text_length": 123,
      "status": "final | complete | in_progress | unknown"
    }
  ]
}
```

## 状态语义

- `generation.state`：
  - `generating` —— 停止按钮存在或任一回合 `in_progress`；
  - `completed` —— 无停止按钮且最后回合 `complete`；
  - `unknown` —— 证据不足（不猜测）。
- `messages[].status`：
  - 用户消息：`final`（渲染后不可变）；
  - 最后一条 assistant：跟随最后一个 `data-talvt-turn-state`；
  - 更早的 assistant：`complete`（有更新回合存在即已定型）。

## 已知限制

- 只能读取**当前已渲染**的消息；长会话的历史消息懒加载，向上滚动后才可见。
- Markdown 保真：标题/引用/表格/代码块按语义属性还原；嵌套列表等复杂结构可能退化为纯文本。
- Selector 是网页内部实现，页面改版会失效；`session_id` 以 URL 为准，DOM 属性仅作候选。
- 页面上其他修改 DOM 的脚本（如用户的优化脚本）可能影响观察，异常时优先排查。

## 下一步（未实现）

- WebSocket 接入 Python Core（background.js 已预留消息类型）；
- `read_messages` / `get_session` 请求-响应协议（带 request_id、超时）；
- 会话路由（严格匹配，失败报错不回退）。
