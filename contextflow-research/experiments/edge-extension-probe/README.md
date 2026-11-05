# edge-extension-probe — MV3 扩展原型

研究用最小扩展，验证“ChatGPT 会话观察 + 本地 Core 桥接”的浏览器侧机制。**不是生产代码。**

## 文件

| 文件 | 作用 |
|---|---|
| `manifest.json` | MV3；权限 `tabs/webNavigation/storage/scripting/alarms/sidePanel`；host 仅 `127.0.0.1`、`localhost`、`chatgpt.com`、`chat.openai.com` |
| `background.js` | Service Worker：导航事件采集、页面状态缓存、WebSocket 到 Core（重连 + alarms 保活）、Core 命令路由、测试钩子 `globalThis.__cf` |
| `content.js` | 隔离世界内容脚本：页面分类、会话引用、消息统计、生成状态、MutationObserver、隔离世界/MAIN world 实验、Core 通知提示条 |
| `popup.html/js` | 工具栏弹窗：显示 Core 连接、页面类型、会话、消息数、隔离实验统计 |
| `sidepanel.html/js` | 侧边栏工作台雏形（`chrome.sidePanel`） |

## 已实测结论（Edge 156，Playwright 驱动无头 Edge）

- 扩展可加载，SW 正常启动；
- content script 可上报页面身份/会话/消息统计；
- `webNavigation.onHistoryStateUpdated` 捕获 pushState，`onCommitted` 不触发；
- 隔离世界读不到页面 JS 变量；`chrome.scripting.executeScript({world:"MAIN"})` 可以；内联 `<script>` 注入被 CSP 拦截；
- SW 终止后内存状态丢失、`storage.session` 保留；
- Core ↔ SW ↔ content ↔ DOM 全链路可用。

## 手动安装

`edge://extensions` → 开发人员模式 → 加载解压缩的扩展 → 选择本目录。

## 未验证

真实 chatgpt.com 登录态下的 DOM/会话提取；侧边栏实际打开效果；自动发送消息（未实施）。
