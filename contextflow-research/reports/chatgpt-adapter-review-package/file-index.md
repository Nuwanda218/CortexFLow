# 关键源码文件索引

> 路径均相对于 `contextflow-research/`。行号对应审计时的源码版本。

## 扩展（`experiments/edge-extension-probe/`）

| 文件 | 行数 | 职责 | 关键位置 |
|---|---|---|---|
| `manifest.json` | 46 | MV3 配置：权限、host、内容脚本、侧边栏 | 权限 `7-22`；content_scripts `27-38`；side_panel `43-45` |
| `background.js` | 283 | Service Worker：导航事件、页面状态缓存、Core WebSocket 桥、命令路由、测试钩子 | WS 连接 `56-103`；命令路由 `105-125`；导航监听 `129-155`；runtime 消息 `159-232`；生命周期 `236-266`；`__cf` 钩子 `269-283` |
| `content.js` | 260 | 隔离世界内容脚本：页面分类、会话引用、消息统计、MAIN world 探测、假剪贴板、Core 通知条 | 页面分类 `83-88`；URL 会话引用 `92-101`；`readPageState` `113-149`；观察器 `186-199`；消息处理 `202-246`；假剪贴板 `208-231` |
| `popup.js` | 60 | 工具栏弹窗：Core 状态、页面状态、隔离实验统计 | `renderState` `9-33` |
| `sidepanel.js` | 126 | 侧边栏：四态"用户确认式交接"原型（pending/copied/confirmed/cancelled） | 状态机 `12-16`；格式化 `29-40`；写剪贴板 `62-77`；测试钩子 `118-123` |

## 本地 Core（`experiments/local-core-bridge/`）

| 文件 | 行数 | 职责 | 关键位置 |
|---|---|---|---|
| `core_server.py` | 207 | FastAPI + WebSocket 研究版 Core：`/health`、`/api/events`、`/events`、`/api/notify`、`/ws` | Origin/token `62-73`；notify `121-147`；WS 端点 `150-193`；启动 `196-203` |
| `bridge_test.py` | 103 | 协议测试（13 项）：双向、队列、重放、鉴权、Origin、非法/重复消息 | `main` 全文 |

## 测试与模拟（`experiments/playwright-probe/`、`experiments/mock-chat-page/`）

| 文件 | 职责 |
|---|---|
| `audit_repro.py` | 本次审计复现（19 项）：加载、页面识别、SPA、消息读取、双向通信、路由、重连、假剪贴板、真实 chatgpt.com 公开页 |
| `fake_clipboard_test.py` | 假剪贴板跨世界验证（6 项） |
| `handoff_test.py` | 四态交接验证（7 项） |
| `mock-chat-page/index.html` + `app.js` | 本地模拟聊天页（两会话、SPA、流式、可观察 DOM 契约） |
| `mock-chat-page/prosemirror-like.html` | 模拟 ProseMirror paste 契约的测试页 |
| `mock-chat-page/serve.py` | 静态服务器 + SPA 回退 |

## 不在本包中的文件

- `electron-probe/`：Electron 容器实验，与本审计无直接关系。
- `reports/` 下的历史报告：结论以本次审计为准，历史报告仅作背景。
- `_artifacts/`：原始证据（JSON/日志/截图），体积较大，未复制；索引见 `test-evidence.md`。
