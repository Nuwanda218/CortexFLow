# 实验记录（CF-EXP-001）

> 证据强度分级（本文件统一使用）：
> **L1 编写了代码** < **L2 静态检查通过** < **L3 模拟页面运行成功** < **L4 真实目标页面运行成功** < **L5 官方文档支持**。
> 每条实验明确标注实际达到的等级；不得把 L1/L2 当作 L3/L4。

## 环境基线

| 项 | 值 |
|---|---|
| OS | Windows 11 家庭版 Build 26200（主机名 NUWANDA） |
| 浏览器 | Microsoft Edge 156.0.4314.58（`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`） |
| Python | 3.10.11（`D:\python310\python.exe`） |
| Python 依赖 | fastapi 0.139.2、uvicorn 0.51.0、websockets 16.1.1、requests 2.34.2、playwright 1.64.1（本次新装） |
| Node | 24.15.0 / npm 11.12.1 |
| Electron | 45.2.2（npmmirror 镜像下载） |
| 网络 | 本机 Clash 代理 127.0.0.1:7890；chatgpt.com 对无头浏览器返回 Cloudflare challenge |
| 安全软件 | 火绒 HIPS、深信服 EDR、Defender 实时防护均开启（对 Electron 有实际影响，见 CF-F-00） |

---

## 任务 A：ChatGPT Project / Session 寻址

### CF-A-01 / A-02 / A-03 会话 URL 稳定性、区分能力、Project 关联

- **状态：未执行（阻塞）**
- **原因**：需要用户已登录的 ChatGPT 会话。本次执行时段用户不在电脑前，且任务明确禁止“未经授权访问用户账号或私人会话”。未使用用户真实 Edge profile。
- **替代证据**：CF-A-05（公开页探测）+ 官方文档核查（见 `references/official-docs.md`）。
- **需要用户提供**：在 Edge 中登录 ChatGPT，打开 2 个测试会话 + 1 个测试 Project，然后授权扩展只读观察（或人工记录 URL 后交给 Core）。
- **可执行性判断**：一旦获授权，扩展侧代码已就绪（`content.js` 的 `sessionRefFromUrl()` 已支持 `/c/<id>`、`/g/<project>/c/<id>`、`/g/<project>/project` 三种形态），预计 10–15 分钟可完成。

### CF-A-04 导航方式差异（点击侧栏 / 前进后退 / 刷新 / 新标签页）

- **状态：部分执行（在本地模拟页上）**
- **目的**：验证浏览器层能否区分“浏览器级导航”与“SPA 内部导航”。
- **步骤**：在 mock 页点击 Session B 链接（`history.pushState`）、刷新、`popstate`。
- **结果**：
  - 点击侧栏 → 仅触发 `webNavigation.onHistoryStateUpdated` + `tabs.onUpdated.url`，**不触发** `onCommitted`；
  - 刷新 → 触发 `onCommitted` + `status_complete`；
  - 前进/后退 → `popstate` + `onHistoryStateUpdated`（mock 页 `popstate` 监听生效）。
- **证据**：`_artifacts/playwright-evidence.json` 的 `nav_events`；B-03a/B-03c。
- **结论**：浏览器层可以可靠区分 SPA 导航与整页导航。**局限**：这是模拟页结论，真实 ChatGPT 的 pushState 行为需实测确认（预期相同，因为都是 History API）。

### CF-A-05 公开 chatgpt.com 可达性（无凭据）

- **目的**：在不登录的前提下观察 chatgpt.com 的公开响应与 URL。
- **环境**：Playwright 1.64.1 + Edge 156 无头 + 全新临时 profile；Electron 45 顶层 WebContentsView。
- **结果**：
  - Playwright 无头 Edge：HTTP **403**，`Cf-Mitigated: challenge`，页面标题「请稍候…」，URL 被附加 `__cf_chl_rt_tk` 参数；
  - Electron（有窗口）：**成功加载**，标题 `ChatGPT: Chat, Work, Create & Code with AI`，URL `https://chatgpt.com/`。
- **证据**：`_artifacts/playwright-evidence.json`（`chatgpt_public`）、`_artifacts/electron-evidence.json`（`chatgpt`）、`screenshots/06-chatgpt-public.png`。
- **结论**：Cloudflare 对不同客户端/无头特征的处理不同；**无登录态无法观察会话 URL**。
- **局限**：不能据此推断登录后的 URL 结构；也不能把“Electron 能加载”当作“平台允许自动化”。

---

## 任务 B：Microsoft Edge 扩展能力

扩展源码：`experiments/edge-extension-probe/`（manifest.json / background.js / content.js / popup.html / popup.js / sidepanel.html / sidepanel.js）。
测试驱动：`experiments/playwright-probe/playwright_probe.py`（Playwright 启动 Edge，`--load-extension` 加载未打包扩展，通过 Service Worker 的测试钩子 `globalThis.__cf` 读取状态）。

### CF-B-00 静态检查

- **等级**：L2
- **步骤**：`node --check` 四个 JS 文件；`node -e require('./manifest.json')`。
- **结果**：4/4 语法通过；manifest 可解析（MV3，permissions=tabs,webNavigation,storage,scripting,alarms,sidePanel）。
- **局限**：静态检查不能证明浏览器可加载。

### CF-B-01 扩展在 Edge 中加载

- **等级**：L3（模拟页环境，真实浏览器）
- **结果**：Service Worker 启动，扩展 id `ecdmenhoofjafenfmeflnkfomcffoohl`；manifest 在 worker 内可读。
- **证据**：`playwright-evidence.json` → checks B-01/B-01b。
- **结论**：**无头 Edge + `--load-extension` 可用**（官方文档未承诺，属实验确认）。

### CF-B-02 页面识别

- **等级**：L3
- **结果**：content script 上报 `kind=mock-chat`、URL、标题、`session={kind:"mock_body_dataset", value:"session-a"}`、消息计数 `{total:2, user:1, assistant:1}`。
- **证据**：B-02/B-02b/B-02c。
- **局限**：真实 ChatGPT 的 session 提取逻辑（URL 正则）**未在真实页面验证**。

### CF-B-03 导航检测

- **等级**：L3
- **结果**：pushState 导航产生 1 条 `webNavigation.onHistoryStateUpdated`；`onCommitted` 未对 session-b 触发；content script 跟随到 session-b。
- **证据**：B-03a/B-03b/B-03c、`nav_events`。
- **结论**：**不能只依赖 `onCommitted`**；必须订阅 `onHistoryStateUpdated` + `onReferenceFragmentUpdated` + `tabs.onUpdated` + 页面内 MutationObserver。

### CF-B-04 隔离世界与 MAIN world

- **等级**：L3
- **结果**：
  - 隔离世界读 `window.__mockChat` → `undefined`（B-04a 通过）；
  - 隔离世界 patch `history.pushState` 后，页面自身 pushState 调用计数 = 0（B-04c 通过）；
  - 内容脚本注入内联 `<script>` → **被 CSP 拦截**，`document.documentElement.dataset.cfInlineProbe` 未设置（B-04d 通过；页面控制台报 `Executing inline script violates ... Content Security Policy`）；
  - `chrome.scripting.executeScript({world:"MAIN"})` → 成功读到 `{sessionId:"session-a", state:"idle", messageCount:2}`（B-04b 通过）。
- **证据**：B-04a~B-04d；`screenshots/`。
- **结论**：**MAIN world 访问必须走 `chrome.scripting`，不要用内联 script 注入**（后者受页面 CSP 限制；本次 mock 页本身未下发 CSP，仍被浏览器/扩展侧策略拦截，具体来源未完全定位，记为待查）。
- **局限**：真实 ChatGPT 的 CSP 更严格，结论方向一致但需实测。

### CF-B-05 流式生成状态

- **等级**：L3
- **结果**：发送消息后 `body[data-app-state]` 由 `idle`→`generating`→`idle`；最后一条 assistant 消息 `data-status` 由 `streaming`→`complete`；消息数 +2。扩展在生成中与完成后均上报了正确状态。
- **证据**：B-05a/B-05b/B-05c、`screenshots/03-mock-after-reply.png`。
- **局限**：真实 ChatGPT 的“生成结束”信号需实测；模拟页用显式 `data-status`，真实页面没有这样友好的属性。

### CF-B-06 扩展内部通信（background → content）

- **等级**：L3
- **结果**：SW 调用 `chrome.tabs.sendMessage(tabId, {type:'cf:core_notice'})`，页面出现 `#cf-core-notice` 提示条。
- **证据**：B-06。
- **局限**：popup 与 content 的直接互发未单独测（popup 通过 background 中转已覆盖）。

### CF-B-07 Core → 扩展 → 页面 全链路

- **等级**：L3
- **结果**：扩展 SW 连上 `ws://127.0.0.1:8765/ws`；Core `/api/notify` 推送 `worker_completed`；页面出现含 `test-task-001` 的提示条。
- **证据**：B-07a~B-07d、`_artifacts/core-server.log`、`screenshots/04-core-notice.png`。

### CF-B-08 Popup

- **等级**：L3
- **结果**：`chrome-extension://<id>/popup.html` 渲染出 `ws=connected`、`kind=mock-chat`、隔离世界统计。
- **证据**：B-08、`screenshots/05-popup.png`。

### CF-B-09 Service Worker 生命周期

- **等级**：L3
- **方法**：通过 CDP `Target.closeTarget` 终止 SW，再用扩展页面唤醒。
- **结果**：SW 实例 id 变化（`7f01211a…` → `e02db5f3…`）；内存 `navEvents` 由 14 降为 7（重启后重新累计）；`chrome.storage.session` 中的哨兵值保留。
- **证据**：B-09/B-09b/B-09c。
- **结论**：**SW 会被终止，内存状态不可依赖**；所有会话状态必须落 `chrome.storage.session/local`，入口处重新水合。
- **局限**：未测 30 秒空闲自然终止（官方文档已确认 30s 规则，本次用强制终止模拟）。

### CF-B-10 侧边栏（sidePanel）

- **状态：未验证**
- **已完成**：manifest 声明 `side_panel` + `sidePanel` 权限，扩展加载成功（说明 Edge 156 接受该配置）。
- **未完成**：`chrome.sidePanel.open()` 需要用户手势，自动化下无法真实打开并观察。
- **需要用户**：手动加载扩展 → 点击工具栏图标 → “Open side panel” → 评价布局与体验。

---

## 任务 C：网页回复与交互状态

### CF-C-01 本地模拟页的 DOM 观察

- **等级**：L3
- **结果**：可区分 user/assistant（`data-role`）、生成中/完成（`data-status`、`body[data-app-state]`）、消息边界（`data-message-id`）；可提取代码块与表格文本（E-04b）。
- **证据**：B-02/B-05、E-04a/E-04b。
- **局限**：模拟页是**为可观察性设计的**，真实 ChatGPT 没有这些属性；真实页面的消息边界、流式结束、多模态内容结构**全部未验证**。

### CF-C-02 真实 ChatGPT 回复读取 / 发送

- **状态：未执行（合规前置未满足）**
- **原因**：任务要求“必须先核实平台规则与官方支持情况”；本次未找到允许程序化发送/读取的官方依据，且无登录态可用。**未实施任何未获支持的自动化。**

---

## 任务 D：Edge 扩展与本地 Python Core 通信

服务端：`experiments/local-core-bridge/core_server.py`（FastAPI + WebSocket，绑定 127.0.0.1:8765）。
协议测试：`experiments/local-core-bridge/bridge_test.py`（无需浏览器，模拟扩展客户端 + 模拟网页）。

### CF-D-01 扩展 → Core 事件

- **等级**：L3（协议层）/ L3（浏览器侧见 B-07）
- **结果**：WS 发送 `{type:"session_changed", session_ref:"local-test-session-a"}`，Core `/api/events` 可查；浏览器侧 149 条事件落盘。
- **证据**：`bridge-test-results.json` D-01a/D-01b；`core-events.jsonl`。

### CF-D-02 Core → 扩展通知

- **等级**：L3
- **结果**：`POST /api/notify` → 在线客户端 `delivered=1`；payload 形状正确；浏览器侧全链路见 B-07d。
- **证据**：D-02a/D-02b。

### CF-D-03 异常处理

- **等级**：L3
- **结果**（13/13 全部通过）：
  - 非法 JSON / 缺 `type` 的消息被忽略，连接不断；
  - 重复消息被接受（v1 无去重，**记为设计缺口**）；
  - 扩展离线时通知进入 pending 队列，重连后带 `replayed:true` 补发；
  - 错误 token → WS 拒绝（4401）；
  - `https://evil.example` Origin → HTTP 403；
  - `chrome-extension://` Origin → 接受；
  - 缺 token → 401。
- **证据**：`bridge-test-results.json`。
- **局限**：Core 重启后内存事件丢失（未做持久化）；未测真实网络中断/半开连接。

### CF-D-04 Native Messaging

- **状态：未执行（文档核查完成）**
- **原因**：需写注册表 `HKCU\SOFTWARE\Microsoft\Edge\NativeMessagingHosts\<name>` + host manifest + 扩展 `nativeMessaging` 权限并重载；本轮时间用于更高优先级实验。
- **文档结论**：stdio + 4 字节长度前缀 JSON；host→browser 单条上限 1 MB；`connectNative` 保持进程直到 port 销毁；Windows 注册表路径已确认（见 `references/official-docs.md`）。
- **建议**：作为 D 的补充实验，预计 20 分钟可完成。

---

## 任务 E：Playwright 备选方案

驱动脚本：`experiments/playwright-probe/playwright_generic.py`。

| 编号 | 目的 | 结果 | 等级 |
|---|---|---|---|
| CF-E-01 | 启动本机 Edge（不下载 Chromium） | `channel="msedge"` 成功，版本 156.0.4314.58 | L3 |
| CF-E-02 | 独立 user-data-dir | 成功创建并启动 | L3 |
| CF-E-03 | 打开指定会话 URL、输入、等待流式完成 | 成功，流式耗时 0.83s | L3 |
| CF-E-04 | 读取完成后的结构化回复 | 成功，代码块/表格文本保留 | L3 |
| CF-E-05 | 切换会话 + 刷新后重开 URL | 成功恢复到 session-b | L3 |
| CF-E-06 | 复用日常 Edge Profile | 真实 profile **2176.2 MB**；仅复制 `Local State`+`Default/Preferences` 的骨架可启动；**未**对真实 profile 直接启动（避免损坏/凭据风险） | L3（部分） |
| CF-E-07 | `connect_over_cdp` 连接外部启动的 Edge | 成功（`--remote-debugging-port=9333`） | L3 |
| CF-E-08 | 多标签页管理 | 成功 | L3 |
| CF-E-09 | 人与自动化同时操作同一页面 | 最后写入者获胜，无锁/无冲突检测 | L3 |

- **证据**：`_artifacts/playwright-generic-evidence.json`。
- **结论**：Playwright 技术上完全可行，适合**一次性探测与自动化测试**；作为长期 Adapter 的代价是登录态（需复制/独立 profile）、反自动化风险、与用户手动操作冲突。
- **局限**：未在真实 ChatGPT 页面执行任何自动化（合规前置未满足）。

---

## 任务 F：Electron 与前端集成

### CF-F-00 Electron 环境适配（重要发现）

- **现象**：Electron 45.2.2 首次启动即崩溃（`0x80000003` / `0xC0000005`），即使 `--version` 也崩溃；`--disable-crash-reporter --noerrdialogs` 后 `--version` 正常，但应用仍崩溃。
- **根因（已定位）**：默认 `userData` 目录 `%APPDATA%\Electron` 被本机安全软件拒绝访问（日志 `Failed to create cache directory ... 拒绝访问 (0x5)`），导致 Chromium network sandbox 初始化失败。改为 `app.setPath("userData", <workspace>\_artifacts\electron-userdata)` 后正常。
- **证据**：`experiments/electron-probe/err.log`、`min.err.log`、`electron-run.log`。
- **意义**：**Electron 在本机的部署存在真实的环境风险**（安全软件/权限），不是“装上就能跑”。

### CF-F-01 ~ F-09 最小双栏应用

- **等级**：L3
- **实现**：`experiments/electron-probe/main.js`（`WebContentsView` 左远程页 / 右本地面板，`contextIsolation:true`、`nodeIntegration:false`、`sandbox:true`，preload 只暴露 `onCoreEvent`/`getState`）。
- **结果**（9/9）：
  - 左栏加载 mock 会话页，右栏加载本地面板，`setBounds` 实现分栏（969px / 457px）；
  - 主进程可 `executeJavaScript` 驱动远程页输入并等待生成完成；
  - 主进程可读取远程页 DOM（消息数、最后一条 assistant 文本）；
  - 主进程 → 面板 IPC 事件送达并渲染；
  - 远程页与面板均**无 Node 全局**（`require/process/module` 均 undefined）；
  - **WebContentsView 顶层加载 chatgpt.com 成功**（真实标题）。
- **证据**：`_artifacts/electron-evidence.json`。
- **局限**：截图失败（`Current display surface not available for capture`，与 `--disable-gpu`/窗口状态有关）；未验证登录态、未验证与 Python Core 的真实连接（IPC/HTTP 机制已具备）。

### CF-F1-01 ~ F1-05 iframe 嵌入

- **等级**：L3
- **结果**：本地页面 iframe 嵌入 `https://chatgpt.com/` → frame 变为 `chrome-error://chromewebdata/`（被拒绝）；响应头 `X-Frame-Options: SAMEORIGIN`；未观察到 CSP `frame-ancestors`（拦截机制是 XFO）。
- **证据**：`_artifacts/iframe-evidence.json`、`screenshots/07-iframe-host.png`。
- **结论**：**普通 iframe 方案不可行**；Electron 能加载是因为 WebContentsView 是顶层 webContents，不受 XFO 约束。

---

## 任务 G：官方支持的替代集成路径

- **状态：文档核查（L5），未实测**
- **方法**：检索并核对 OpenAI 官方文档（Apps SDK / Plugins / custom MCP / Secure MCP Tunnel / MCP Events / Conversations API / Projects help）。
- **关键结论**：
  1. ChatGPT 网页端**不能直连本地 MCP server**（官方 FAQ 原文 "Not directly"）；本地服务需公网 HTTPS 或官方 **Secure MCP Tunnel**（`tunnel-client`，出站连接，可托管 stdio 本地进程）。
  2. 本机 MCP app 仅**桌面端**可用，不会让工具在网页端可用。
  3. 完整自定义 MCP 连接器仅 **Business/Enterprise/Edu**；Pro 仅 read/fetch；Plus 未说明。
  4. 官方**没有**向任意已有网页会话注入消息的机制；MCP Events 只能投递到“已订阅的 Work chat”，需公网 HTTPS 回调，且不支持轮询/流式。
  5. API 的 Conversations 是 API 侧对象，与网页会话不共享状态。
  6. 工具调用只在模型回合内、由用户消息触发；tunnel 单请求绝对预算 **120 秒**。
- **证据**：`references/official-docs.md`。
- **局限**：`developers.openai.com` 对直接抓取返回 403，部分引文经 JS 渲染桥获取（已在参考文件中标注）；未在真实账号下验证套餐可用性。

---

## 任务 H：外部评审与下一轮（CF-EXP-002 起步）

### CF-H-01 向真实 ChatGPT 会话投递调研结果并取回评审

- **等级**：L4（真实页面、真实账号、用户明确授权）
- **目的**：验证“外部程序把调研结果投递到指定 ChatGPT 会话、并取回其回复”的完整链路；同时获取架构评审意见。
- **方法**：BrowserSkill 会话 → 打开指定会话 URL → 系统剪贴板写入消息 → 聚焦输入框 → `Ctrl+V` → 点击发送按钮 → 轮询 `[data-talvt-turn-state]` 直到完成 → 点击助手消息“复制” → 读取剪贴板。
- **结果**：
  - 消息成功发送（发送键为用户自定义 `Ctrl+、`；单独 Enter 是换行；本次用“聚焦发送按钮 + Enter”成功发送）；
  - 生成完成检测成功（停止按钮消失）；
  - 回复通过“复制”按钮取回，**6042 字符 Markdown**，保存于 `_artifacts/chatgpt-reply.md`；
  - 评审给出 8 项风险、3 个下一轮实验（002A/002B/002C）与“用户确认式剪贴板”交互设计。
- **证据**：`_artifacts/chatgpt-reply.md`、`reports/review-and-next-round.md`。
- **结论**：**“投递 → 等待 → 取回”链路在真实页面可行**，但依赖用户自定义发送键、页面 DOM 与剪贴板，属于“用户确认式”而非无人值守自动化。
- **局限**：未验证多标签页并发、错误 Session 路由、长文本/多模态回复；ToS 合规判断仍需用户决策。

### CF-EXP-002C-1 用户确认式剪贴板交接最小原型

- **等级**：L3（本地模拟页 + 真实扩展 + 本地 Core）
- **目的**：验证评审建议的“待处理 → 已复制 → 已确认/已取消”四态交接闭环，确认扩展可以**只写剪贴板、不自动发送**。
- **实现**：`experiments/edge-extension-probe/sidepanel.html/js` 新增“待交接的 Worker 汇报”面板；按钮“复制汇报”调用 `navigator.clipboard.writeText` 写入格式化 Markdown；状态机 pending → copied → confirmed / cancelled；测试钩子 `window.__cfHandoff`。
- **结果**：**7/7 通过**
  - Core → 扩展 WS 推送 `worker_completed`；
  - 侧边栏出现“待处理”汇报；
  - 点击“复制汇报”→ 剪贴板得到格式化 Markdown（含 Task/Summary/时间/来源）；
  - 点击“已确认交接”→ 状态变为 confirmed；
  - 全程未打开/触碰任何 ChatGPT 页面，**无自动发送**。
- **证据**：`_artifacts/handoff-evidence.json`、`experiments/playwright-probe/handoff_test.py`。
- **结论**：评审建议的“用户确认式交接”在扩展侧**可实现且可测试**；剪贴板写入不需要 `debugger` 权限，只需 `clipboardWrite`。
- **局限**：未在真实 ChatGPT 页面完成“用户粘贴并发送”的端到端闭环（需用户手动操作）；未实现消息去重与目标 Session 校验。
