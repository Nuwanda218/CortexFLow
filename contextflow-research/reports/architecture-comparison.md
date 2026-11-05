# 候选方案对比（CF-EXP-001）

> 评分说明：每项给出 **1–5 分**（5 最好）与**证据来源**。`[实测]` = 本次实验；`[官方]` = 官方文档；`[推断]` = 基于机制推理；`[未知]` = 无证据，按最保守估计打分。

## 1. 方案定义

| 方案 | 构成 |
|---|---|
| **A** | Edge 扩展 + 本地 Python Core（保留用户日常浏览器与原生 ChatGPT 会话） |
| **B** | Playwright + 本地 Python Core（自动化浏览器驱动会话） |
| **C** | Electron + Python Core（桌面容器承载 ChatGPT 网页 + 工作台） |
| **D** | ChatGPT 官方集成机制（Apps/Plugins/MCP/Tunnel）+ Core |
| **E** | 混合：A 的界面与会话观察 + D 的官方工具调用 + Core 调度 Worker |

## 2. 维度对比

| 维度 | A Edge 扩展 | B Playwright | C Electron | D 官方 MCP | E 混合 |
|---|---|---|---|---|---|
| 原生体验 | **5** 用户原浏览器、原标签页、原登录态 `[实测 B-01..B-08]` | 2 自动化窗口/独立 profile，用户与脚本争用同一页面 `[实测 E-09]` | 3 独立窗口，体验接近但不是用户日常浏览器 `[实测 F-01..F-09]` | **5** 完全在 ChatGPT 内 `[官方]` | **5** 界面同 A |
| Session 连续性 | 4 URL 绑定 + 标签页观察；URL 稳定性未官方承诺 `[实测 B-02/B-03][官方 K18][第三方 K19]` | 3 需复制/独立 profile，登录态迁移成本高 `[实测 E-06]` | 2 独立 cookie jar，需重新登录；Google SSO 可能被阻断 `[官方 Electron/Google]` | **5** 原生会话内 `[官方]` | 4 |
| Project 支持 | 3 URL 形态可识别（`/g/g-p-…`），但官方未发布语法 `[第三方 K19]` | 3 同左 | 3 同左 | **5** 原生 Projects `[官方]` | 4 |
| 外部消息输入 | 3 需 DOM 注入；ToS 对程序化操作有限制，须用户确认 `[实测 B-06][官方 K20]` | 3 同左，且更易触发反自动化 `[推断]` | 3 同左 `[实测 F-03]` | 2 仅工具调用回合内、由用户消息触发 `[官方 K17]` | 3 官方工具调用 + 用户确认注入 |
| 回复读取 | 3 DOM 观察，页面改版即失效；**ToS 限制程序化提取** `[实测 B-05][官方 K20]` | 3 同左 `[实测 E-04]` | 3 同左 `[实测 F-04]` | **5** 工具返回值结构化 `[官方]` | 4 用户触发抓取 + 工具返回 |
| 双向通信 | **5** WebSocket 实测双向/重连/排队 `[实测 D-01..D-03, B-07]` | 4 进程内/HTTP 可用 `[实测]` | **5** IPC + HTTP/WS `[实测 F-05]` | 4 公网 MCP/Tunnel，120s 预算 `[官方 K17]` | 5 |
| 平台支持 | 3 扩展机制官方支持；对 chatgpt.com 的自动化无官方背书 `[官方]` | 2 无官方背书，反自动化风险最高 `[推断]` | 3 容器机制官方支持；嵌入第三方站点有 Google OAuth 限制 `[官方]` | **5** 官方支持 `[官方]` | 4 部分官方 |
| 稳定性 | 2 依赖页面结构 `[实测]` | 2 依赖页面结构 + 浏览器版本 `[实测]` | 2 依赖页面结构 + Electron 版本 + 本机安全软件 `[实测 F-00]` | **4** 依赖官方 API 版本 `[官方]` | 3 |
| 安全性 | 4 最小权限 + token + Origin 校验；不碰凭据 `[实测 D-03]` | 2 需保管登录态/复制 profile，风险最高 `[实测 E-06]` | 3 需严格隔离远程内容；Electron 默认自动批准权限请求是坑 `[官方]` | 4 官方鉴权 `[官方]` | 4 |
| 实现复杂度（越低越高分） | 4 扩展 + WS 已跑通 `[实测]` | 3 | 2 需打包/更新/安全加固 `[实测 F-00]` | 2 需公网/隧道 + 套餐 + 审核 `[官方]` | 2 |
| 长期维护 | 2 页面改版即失效 | 2 同左，且更易被反自动化 | 2 同左 + Electron 升级 | 4 官方兼容性较好 | 3 |

**加权总分（原生体验/连续性/通信/合规/维护 各权重 1）**

| 方案 | 总分（满分 55） | 结论 |
|---|---|---|
| A | **40** | 第一版首选 |
| E | 38 | 演进目标（A 的界面 + D 的语义通道） |
| C | 30 | 二期容器候选 |
| D | 37（但受套餐/部署限制，个人账号可能不可用） | 长期方向 |
| B | 27 | 仅探测/测试工具 |

## 3. 逐方案结论

### 方案 A：Edge 扩展 + Core —— **第一版推荐**

- 优势：唯一同时满足“原生体验 + 复用登录态 + 双向通信已实测 + 权限边界可控”。
- 代价：依赖 ChatGPT 页面结构；ToS 对程序化提取有限制，必须设计为用户显式触发。
- 最小实现路径（已验证）：
  1. MV3 扩展（`tabs`/`webNavigation`/`storage`/`scripting`/`alarms`/`sidePanel`，host 仅 `chatgpt.com` + `127.0.0.1`）；
  2. content script 上报页面状态（隔离世界读 DOM；需要页面 JS 状态时用 `chrome.scripting.executeScript({world:"MAIN"})`）；
  3. SW 维护 WebSocket 到 `ws://127.0.0.1:8765/ws`，断线指数退避 + `chrome.alarms` 保活；
  4. Core 侧 FastAPI/uvicorn：`/ws` 上行、`/api/notify` 下行、离线队列、token + Origin 白名单；
  5. 用户手势触发“捕获本轮/填入输入框”。

### 方案 B：Playwright + Core —— **不推荐作为长期 Adapter**

- 技术可行（12/12 实测），但：登录态需复制/独立 profile（真实 profile 2.1 GB，且复制凭据有风险）；与用户手动操作冲突（last-writer-wins）；反自动化风险最高；ToS 风险同样存在。
- 保留用途：一次性技术探测、回归测试、对本地 mock 页的自动化验证。

### 方案 C：Electron + Core —— **二期候选，需先确认登录方式**

- 实测：`WebContentsView` 可双栏、可顶层加载 chatgpt.com（iframe 被 XFO 拒绝）、远程页无 Node 权限、IPC 正常。
- 风险：独立 cookie jar 需重新登录；**Google SSO 在嵌入式 webview 中被 Google 政策阻断**；本机安全软件导致 Electron 默认 userData 目录不可写（已定位并绕过）；安装/更新负担。
- 适用：需要独立工作台窗口、多分栏、全局快捷键、托盘等桌面能力时。

### 方案 D：官方 MCP + Core —— **长期方向，第一版不可作为唯一通道**

- 官方支持，但：本地 Core 需公网 HTTPS 或 Secure MCP Tunnel；完整工具调用仅 Business/Enterprise/Edu（Pro 仅 read/fetch，Plus 未说明）；工具调用只在用户消息触发的回合内发生；无“向已有会话注入消息”的机制；120 秒请求预算。
- 适合“Coordinator 主动查询 Core”的拉取式协作（create-task + 后续 status 查询），不适合“Core 主动推送进会话”。

### 方案 E：混合 —— **演进目标**

- 形态：A 的扩展负责界面与会话观察；D 的官方工具调用负责 Coordinator ↔ Core 的语义通信；Core 负责 Worker 调度；必要时用户主动交接结果。
- 前提：用户套餐支持自定义 MCP（或接受 Tunnel），且接受“拉取式”而非“推送式”协作。

## 4. 证据来源索引

- 实测：`_artifacts/playwright-evidence.json`、`bridge-test-results.json`、`playwright-generic-evidence.json`、`electron-evidence.json`、`iframe-evidence.json`
- 官方：`references/official-docs.md`
- 第三方观察（URL 语法）：`references/official-docs.md` 第 3 节
