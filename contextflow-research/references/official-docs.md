# 官方文档与参考资料（CF-EXP-001）

> 采集时间：2026-11-04。标注 `[403→reader]` 的页面直接抓取返回 403，引文经 JS 渲染阅读桥获取，文本为页面 markdown 提取，非原始 HTML。
> 本文件只记录“该文档支持了什么结论”，不复制大段原文。

## 1. OpenAI：官方集成路径（任务 G）

| 主题 | 结论 | URL | 强度 |
|---|---|---|---|
| Apps SDK | Apps SDK（preview）基于 MCP，可定义工具/资源/提示 | https://help.openai.com/en/articles/12515353-building-with-the-apps-sdk | 官方 |
| MCP server 概念 | 工具调用流程：发现 → 模型选择工具 → server 执行 → 模型继续 | https://developers.openai.com/plugins/concepts/mcp-server `[403→reader]` | 官方 |
| 生产传输 | 生产 MCP server 需稳定 HTTPS + Streamable HTTP | 同上 | 官方 |
| 自定义 MCP 连接 | 当前可直接从 Plugins 添加自定义 MCP server，无需 Developer Mode | https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt | 官方 |
| **本地 MCP** | **"Can I connect to a local MCP server? Not directly."** 需 Secure MCP Tunnel | 同上 | 官方 |
| Secure MCP Tunnel | 出站 HTTPS 隧道，可托管本地 stdio MCP（`--mcp-command "python server.py"`）；不支持公开分发 | https://developers.openai.com/api/docs/guides/secure-mcp-tunnels `[403→reader]` | 官方 |
| 本机 MCP app | 仅桌面端可用；保存到账号不会让工具在 web/mobile 可用 | https://help.openai.com/en/articles/20001256-plugins-in-chatgpt-and-codex | 官方 |
| 套餐限制 | 完整 MCP：Business/Enterprise/Edu（web）；Pro 仅 read/fetch；Plus 未说明 | https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt | 官方 |
| 工具快照 | 工具列表在审批时冻结，server 变更需管理员重新发布 | 同上 | 官方 |
| MCP Events | 唯一官方 server→ChatGPT 推送机制；投递到“已订阅的 Work chat”；需公网 HTTPS 回调；不支持轮询/流式 | https://developers.openai.com/plugins/build/mcp-events `[403→reader]` | 官方 |
| 请求预算 | Tunnel 单请求绝对预算 120 秒（队列时间计入，进度通知不重置） | https://developers.openai.com/api/docs/guides/secure-mcp-tunnels `[403→reader]` | 官方 |
| 异步工具调用 | `async: true` 仅 Responses API 有；ChatGPT app 无等价物 | https://developers.openai.com/api/docs/guides/async-tool-calling `[403→reader]` | 官方 |
| MCP 传输 | stdio 与 Streamable HTTP 两种标准传输 | https://modelcontextprotocol.io/specification/2025-06-18/basic/transports | 官方 |
| Conversations API | API 侧对象（create/retrieve/update/delete + items），**无 list-all 端点** | https://developers.openai.com/api/reference/resources/conversations `[403→reader]` | 官方 |
| 网页 vs API | ChatGPT 套餐不授予应用访问 ChatGPT 会话/记忆的权限 | https://help.openai.com/en/articles/20001542-using-your-chatgpt-plan-in-other-apps-and-sites | 官方 |
| Projects | Projects 组织相关 chats/files/instructions；有可分享的 project URL；应用可在项目聊天中使用 | https://help.openai.com/en/articles/10169521-projects-in-chatgpt | 官方 |

## 2. OpenAI：会话 URL 与平台规则（任务 A）

| 主题 | 结论 | URL | 强度 |
|---|---|---|---|
| 会话链接形态 | 开发者文档以 `conversation_url: https://chatgpt.com/c/123` 形式返回聊天链接 | https://developers.openai.com/workspace-agents/trigger-runs `[403→reader]` | 官方（形态） |
| 分享链接 | 官方文档化的分享形态为 `https://chatgpt.com/share/<conversation-ID>` | https://help.openai.com/en/articles/7925741-sharing-conversations-and-scheduled-tasks-in-chatgpt | 官方 |
| 项目 URL 语法 | `/g/g-p-<id>-<slug>/project`、`/g/g-p-<id>-<slug>/c/<chat-id>`；会话移入项目后 id 保留、规范 URL 变化 | https://community.openai.com/t/project-folders-and-share-links/1055969 | 第三方 |
| 项目 URL 刷新问题 | 直接打开/刷新项目 URL 可能报错，需从首页导航 | https://community.openai.com/t/project-page-refresh-error/1396097 | 第三方 |
| 标题自动变化 | ChatGPT 可能动态改写会话标题 | https://community.openai.com/t/dynamic-chat-renaming-makes-chatgpt-projects-unreliable-for-serious-long-term-professional-work/1390715 | 第三方 |
| **Terms of Use** | 禁止 "Automatically or programmatically extract data or Output"；禁止绕过保护措施 | https://openai.com/policies/row-terms-of-use/ | 官方 |

## 3. Microsoft Edge / Chromium 扩展（任务 B）

| 主题 | 结论 | URL | 强度 |
|---|---|---|---|
| Content scripts | 可读写页面 DOM；运行在隔离世界（isolated world） | https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts | 官方 |
| MAIN world | `ExecutionWorld.MAIN` 与页面共享 JS 环境；默认 `ISOLATED` | https://developer.chrome.com/docs/extensions/reference/api/scripting#type-ExecutionWorld | 官方 |
| webNavigation | pushState → `onHistoryStateUpdated`；fragment → `onReferenceFragmentUpdated`；`onCommitted` 为导航提交 | https://developer.chrome.com/docs/extensions/reference/api/webNavigation | 官方 |
| Service Worker 生命周期 | 30 秒空闲终止；全局变量丢失；WebSocket 流量重置空闲计时（Chrome 116）；alarms 最小 30s（Chrome 120） | https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle | 官方 |
| 消息传递 | 单条上限 64 MiB；异步响应返回 `true`；`runtime.lastError` | https://developer.chrome.com/docs/extensions/develop/concepts/messaging | 官方 |
| 权限 | `host_permissions` 启用跨域 fetch、读取 tab 的 url/title、程序化注入；`activeTab` 无安装警告 | https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions | 官方 |
| 网络请求 | Service Worker 有 host 权限即可跨域访问 | https://developer.chrome.com/docs/extensions/develop/concepts/network-requests | 官方 |
| Edge 侧边栏 | Edge 支持 `chrome.sidePanel`；manifest `side_panel` + `sidePanel` 权限；跨标签页保持；`sidePanel.open()` 需用户手势 | https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/sidebar | 官方 |
| Native Messaging | 注册表 `HKCU\SOFTWARE\Microsoft\Edge\NativeMessagingHosts\<name>`；4 字节长度前缀 JSON；host→browser ≤1 MB，browser→host ≤4 GB；`connectNative` 保持进程 | https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging | 官方 |
| 加载未打包扩展 | `edge://extensions` → 开发者模式 → 加载解压缩的扩展 | 同上 | 官方 |
| 无头模式 | Chromium 已统一 headless/headful；旧 headless 自 132 起为独立 `chrome-headless-shell`；**未提及扩展支持** | https://developer.chrome.com/docs/automation-and-testing/headless | 官方（扩展支持为实验确认） |

## 4. Electron（任务 F）

| 主题 | 结论 | URL | 强度 |
|---|---|---|---|
| WebContentsView | 当前推荐 API；BrowserView 已弃用；`<webview>` 官方不建议 | https://www.electronjs.org/docs/latest/api/web-contents-view | 官方 |
| 安全清单 | 加载远程内容须 `nodeIntegration:false`、`contextIsolation:true`、`sandbox:true`；不得关闭 `webSecurity`；`will-navigate`/`setWindowOpenHandler` 限制导航；**默认自动批准所有权限请求**，需 `setPermissionRequestHandler` | https://www.electronjs.org/docs/latest/tutorial/security | 官方 |
| Session/partition | `session.fromPartition('persist:name')` 持久化 cookie；与 Edge/Chrome 的 profile 相互独立，无导入 API | https://www.electronjs.org/docs/latest/api/session | 官方 |
| IPC | preload + `contextBridge.exposeInMainWorld` + `ipcRenderer.invoke` / `ipcMain.handle` | https://www.electronjs.org/docs/latest/tutorial/ipc | 官方 |
| 分栏布局 | 官方示例即两个 view 并排 `setBounds`，`resize` 时重算 | https://www.electronjs.org/docs/latest/api/web-contents-view | 官方 |
| 嵌入第三方站点限制 | Google 政策阻断嵌入式 webview 中的 Google OAuth（`disallowed_useragent`） | https://developers.googleblog.com/en/upcoming-security-changes-to-googles-oauth-2-0-authorization-endpoint/ | 官方（Google） |

## 5. 本地通信与浏览器自动化（任务 D/E）

| 主题 | 结论 | URL | 强度 |
|---|---|---|---|
| Playwright 浏览器渠道 | `channel="msedge"` 使用本机安装的 Edge，无需下载 Chromium | https://playwright.dev/python/docs/browsers | 官方 |
| Playwright 持久化上下文 | `launch_persistent_context(user_data_dir)` 使用独立 profile | https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context | 官方 |
| Playwright CDP | `connect_over_cdp` 连接已开启调试端口的浏览器 | https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp | 官方 |
| Playwright 扩展 | 扩展仅在持久化上下文中工作（官方说明） | https://playwright.dev/python/docs/chrome-extensions | 官方 |
| FastAPI WebSocket | `@app.websocket("/ws")` 原生支持双向消息 | https://fastapi.tiangolo.com/advanced/websockets/ | 官方 |
| uvicorn | ASGI server，可绑定 `127.0.0.1` | https://www.uvicorn.org/ | 官方 |

## 6. 本次未能获取的官方证据

- `developers.openai.com` 直接抓取 403，部分引文经阅读桥获取（已标注）。
- 官方未发布 ChatGPT 项目 URL 语法、会话 URL 永久性承诺。
- 官方未发布“浏览器扩展与 chatgpt.com 交互”的具体规则；只能依据 ToS 一般条款判断。
- Edge 官方未承诺 `--load-extension` 在无头模式可用（本次实验确认可用）。
