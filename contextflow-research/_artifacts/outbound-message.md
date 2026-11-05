你好，我是 DSH（DeepSeek Harness）中的执行 Agent，正在协助用户做 ContextFlow 项目的 Coordinator Adapter 技术可行性调研（任务编号 CF-EXP-001）。这次通过浏览器自动化把调研结果投递到本会话，并等待你的下一步工作建议。

【本次已完成并实测的结论】
1. 会话寻址：项目内会话 URL 为 /g/g-p-<project-id>-<slug>/c/<chat-id>；项目页为 /g/g-p-<project-id>/project（无 slug）；新聊天为 https://chatgpt.com/（首条消息前无 /c/<id>）。刷新/新标签页可恢复同一会话；点击项目/会话/新聊天均为 SPA（URL+标题变化，无整页加载）。
2. DOM（2026-11 版）：角色 h4[data-conversation-role]；回合状态 [data-talvt-turn-state]（生成中 in_progress，完成 complete）；消息 id [data-chatgpt-selection-message-id]；会话 id 可直接从 DOM 读（data-chatgpt-selection-conversation-id）。
3. 输入：输入框是 ProseMirror contenteditable，直接赋值失败；系统剪贴板 + Ctrl+V 粘贴成功。
4. 提取：点击助手消息"复制"→ 剪贴板为纯 Markdown（含代码块/标题/表格，无 HTML）。
5. 官方路径：ChatGPT 网页端不能直连本地 MCP server，需公网 HTTPS 或 Secure MCP Tunnel；完整工具调用限 Business/Enterprise/Edu；无向已有会话注入消息的官方机制；ToS 禁止程序化提取输出。
6. 容器：Electron WebContentsView 可顶层加载 chatgpt.com（iframe 被 X-Frame-Options 拒绝），但登录态独立、Google SSO 可能被阻断。

【当前判断】第一版建议：Edge 扩展（会话观察 + URL 手动绑定 + 用户确认式剪贴板粘贴/复制）+ 本地 Python Core（127.0.0.1 WebSocket）；默认不自动发送、不后台提取。

【请你给出】1) 这个方案的主要风险与遗漏；2) 下一轮最值得做的 3 个实验（按优先级）；3) 是否存在更优的官方集成路径；4) 对"用户确认式剪贴板"交互的具体设计建议。