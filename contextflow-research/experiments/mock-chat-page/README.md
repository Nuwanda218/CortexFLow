# mock-chat-page — 本地模拟聊天页

用于把“浏览器技术限制”与“ChatGPT 平台限制”分开验证。**它的 DOM 是为可观察性设计的，不代表 ChatGPT 的真实结构。**

## 特性

- 两个会话（session-a / session-b），SPA 切换（`history.pushState` + `popstate`）；
- 消息输入、用户消息、Assistant 分段流式回复（约 0.8 秒完成）；
- 可观察契约：
  - `body[data-session-id]`、`body[data-app-state]`（idle/generating）
  - `#conversation[data-conversation-id]`
  - `.message[data-role][data-message-id][data-status=streaming|complete]`
- 会话切换、刷新后从 `sessionStorage` 恢复；
- 主世界标记 `window.__mockChat`（用于隔离世界实验）；
- 回复包含代码块与表格（用于结构化提取实验）。

## 运行

```powershell
python serve.py --port 8787
# http://127.0.0.1:8787/mock/chat/session-a
```

`/mock/chat/*` 路径回退到 `index.html`，模拟真实 SPA 对宿主的要求。

## 注意

模拟页实验成功 **不代表** ChatGPT 网页端适配成功。真实页面的消息边界、流式结束信号、多模态结构均未验证。
