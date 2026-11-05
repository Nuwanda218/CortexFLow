# ContextFlow Coordinator Adapter 技术可行性报告（CF-EXP-001）

- 任务编号：CF-EXP-001
- 执行日期：2026-11-04（Asia/Shanghai）
- 执行环境：Windows 11 Build 26200 / Microsoft Edge 156.0.4314.58 / Python 3.10.11 / Node 24.15.0 / Playwright 1.64.1 / Electron 45.2.2
- 证据目录：`contextflow-research/_artifacts/`（原始 JSON、日志、截图）
- 关联未决问题：`docs/02-requirements/open-questions.md` 的 **Q02（ChatGPT 网页如何接入工作台）**

---

## 执行摘要（≤500 字）

本次用真实实验验证 Coordinator Adapter 的浏览器侧与通信侧机制，并核查官方集成路径。

已实测：Edge 156 可加载 MV3 扩展，识别页面身份、会话引用、SPA 导航与流式生成状态；隔离世界读不到页面 JS，但 `chrome.scripting(world:MAIN)` 可以；扩展与本地 Python Core 的 WebSocket 双向通信、离线排队、鉴权校验全部通过；Electron 可双栏并顶层加载 chatgpt.com，iframe 则被 X-Frame-Options 拒绝。

关键限制：真实登录态下的会话 URL 稳定性、DOM 结构与消息发送均未实测（无授权；无头访问 chatgpt.com 返回 403）。官方路径需公网 HTTPS MCP 或 Secure MCP Tunnel，完整工具调用限企业版，且无向已有会话注入消息的机制；ToS 禁止程序化提取输出，后台自动读取有合规风险。

结论：第一版最值得采用 Edge 扩展（会话观察 + URL 手动绑定 + 用户显式确认的抓取/输入）＋ 本地 Python Core（WebSocket），默认不自动提取/发送；Electron 为二期候选，官方 MCP 为长期方向。

---

## 一、关键发现

### 1.1 浏览器侧（已实测）

| 编号 | 发现 | 证据强度 |
|---|---|---|
| K1 | Edge 156 可在无头模式下加载未打包 MV3 扩展，Service Worker 正常启动 | 实测（B-01） |
| K2 | 内容脚本能读到 URL、标题、DOM 结构、消息数量与生成状态，能区分 user/assistant | 实测（B-02/B-05） |
| K3 | SPA pushState 导航由 `webNavigation.onHistoryStateUpdated` 捕获；`onCommitted` 不触发 | 实测（B-03） |
| K4 | 内容脚本处于隔离世界：读不到页面 JS 变量，且对 `history.pushState` 的 monkey-patch 看不到页面调用 | 实测（B-04a/B-04c） |
| K5 | 页面内联 `<script>` 注入被 CSP 拦截；`chrome.scripting.executeScript({world:"MAIN"})` 可正常读取页面 JS 状态 | 实测（B-04b/B-04d） |
| K6 | MV3 Service Worker 被终止后重启：内存状态丢失，`chrome.storage.session` 保留 | 实测（B-09） |
| K7 | 扩展 ↔ 本地 Core 的 WebSocket 双向链路完整可用（含 Core→SW→content→DOM 全链路） | 实测（B-07） |
| K8 | 本地 Core 的 token 鉴权、Origin 白名单、离线排队与重连补发、非法/重复消息处理均按预期工作 | 实测（D-01~D-03，13/13） |
| K9 | Playwright 可驱动本机 Edge（无需下载 Chromium）、独立 profile、读取结构化回复、CDP 连接外部 Edge | 实测（E-01~E-09，12/12） |
| K10 | Electron `WebContentsView` 可左右分栏、加载远程页面、主进程读写远程 DOM、IPC 到本地面板，且远程页无 Node 权限 | 实测（F-01~F-08，9/9） |
| K11 | Electron 以顶层页面加载 chatgpt.com **成功**（拿到真实标题），而 iframe 被 `X-Frame-Options: SAMEORIGIN` 拒绝 | 实测（F-09 / F1-01~F1-05） |

### 1.2 平台与官方路径（文档核查）

| 编号 | 发现 | 证据强度 |
|---|---|---|
| K12 | 官方明确：ChatGPT 不能直接连接本地 MCP server（"Not directly"）；本地服务需经公网 HTTPS 或官方 Secure MCP Tunnel | 官方文档 |
| K13 | 本机 MCP app 仅桌面端可用，不会让工具在网页端可用 | 官方文档 |
| K14 | 完整自定义 MCP 连接器仅 Business/Enterprise/Edu；Pro 仅 read/fetch；Plus 未说明 | 官方文档 |
| K15 | 官方无“向任意已有网页会话注入消息”的机制；MCP Events 只能投递到已订阅的 Work chat，且需公网 HTTPS 回调 | 官方文档 |
| K16 | API 的 Conversations 是 API 侧对象，与网页会话不共享状态（无端点接受 chatgpt.com 会话 id，且无“列出全部会话”端点） | 官方文档 + 证据缺失 |
| K17 | 工具调用只在模型回合内发生，由用户消息触发；tunnel 单请求绝对预算 120 秒 | 官方文档 |
| K18 | 单会话 URL 形态 `https://chatgpt.com/c/<id>` 有官方间接确认（开发者文档以 `conversation_url` 字段返回该形态）；`/share/<id>` 是唯一官方文档化的可分享形态 | 官方文档 |
| K19 | 项目 URL `/g/g-p-<id>-<slug>/project` 与项目内会话 `/g/g-p-<id>-<slug>/c/<id>` 仅见第三方观察，官方未发布 URL 语法；会话移入项目后 id 保留但地址栏规范 URL 变化 | 第三方观察 |
| K20 | OpenAI Terms of Use（2026-01-01 生效）禁止 "Automatically or programmatically extract data or Output" 及绕过保护措施 | 官方文档（**对架构影响最大**） |

### 1.3 任务 A 结论：会话 URL 能否作为第一版外部引用？

- **能，但只能作为“用户手动绑定 + 弱引用”。**
- 可以保证：`/c/<id>` 是真实聊天链接形态（官方开发者文档以 `conversation_url` 返回该形态）；同一会话在刷新/新标签页打开时通常恢复同一会话；id 在移入项目后保留。
- 不能保证：官方**从未**把 `/c/<id>` 承诺为永久 permalink；项目 URL 语法未官方发布；移入项目后地址栏规范 URL 会变化（`/c/<id>` → `/g/g-p-…/c/<id>`）；标题可能被自动改写；`/c/<id>` 只在会话所有者登录态下可解析，**不能作为跨用户/跨设备的外部句柄**；程序化获取该 URL/内容可能触犯 ToS 的“程序化提取”条款。
- 因此 Core 应把 URL 当作“用户提供的定位提示”，配合标签页 ID、标题、最近消息摘要做一致性校验，并在失配时要求用户重新绑定。

### 1.4 真实页面补充实测（2026-11-04 22:20–22:40，用户已登录 Plus 账号）

> 详细证据见 [`real-chatgpt-probe-evidence.md`](real-chatgpt-probe-evidence.md)。方法：BrowserSkill 只读观察 + DOM 落盘本地分析；**未发送任何消息（流式测试由用户手动发送）、未修改会话、未读取 Cookie/Token**；落盘 HTML 分析后已删除。

| 编号 | 发现 | 证据强度 |
|---|---|---|
| K21 | 项目内会话 URL 实测为 `/g/g-p-<project-id>-<slug>/c/<chat-id>`；项目页为 `/g/g-p-<project-id>/project`（**不带 slug**）；新聊天为 `https://chatgpt.com/`（首条消息前无 `/c/<id>`） | 实测（L4） |
| K22 | 刷新后 URL 完全不变并恢复同一会话；新标签页打开同一 URL 恢复同一会话；点击项目/会话/新聊天均为 **SPA**（URL+标题变化，无整页文档请求） | 实测（L4） |
| K23 | 2026-11 版 DOM：角色 `h4[data-conversation-role]`；回合状态 `[data-talvt-turn-state]`；消息 id `[data-chatgpt-selection-message-id]`；**会话 id 可直接从 DOM 读**（`data-chatgpt-selection-conversation-id` / `data-above-composer-conversation-id`） | 实测（L4） |
| K24 | **生成/完成信号实测**：生成中 `data-talvt-turn-state="in_progress"` + `aria-label="停止"` 停止按钮；完成后 `"complete"` + "重新生成回复"；`aria-busy` 始终 false，不可用 | 实测（L4） |
| K25 | 历史消息懒加载：不同快照中 DOM 内消息数不同（3/3 → 2/2）。**但用户说明这是其自用优化脚本造成，非原生行为**，禁用脚本后应完整渲染（待复测） | 实测（L4，含用户澄清） |
| K26 | 输入框是 **ProseMirror contenteditable**；直接 `fill` 两次失败（`fill target changed or lost focus`），但**系统剪贴板 + Ctrl+V 粘贴成功**（输入框 `[filled]`、发送按钮出现） | 实测（L4） |
| K27 | 回复含代码块（`<pre><code>` + `CodeBlock-*` 类）、`复制代码`/`复制`/`复制消息` 按钮、`data-markdown-copy="code-block"` 等 Markdown 语义标记 | 实测（L4） |
| K28 | **点击助手消息"复制"→ 剪贴板为纯 Markdown**（6229 字符/416 行，含围栏代码块、标题、表格；无 HTML）→ 这是最合规的结构化提取路径 | 实测（L4） |

### 1.5 仍然未能验证（必须保留的不确定性）

- **程序化发送消息 / 自动读取完整回复**未实施（平台规则未确认，任务明确要求先核实；ToS 禁止程序化提取）。
- **禁用用户优化脚本后的原生 DOM 行为**（历史消息是否完整渲染）未复测。
- **会话重命名 / 移入移出 Project 后 URL 是否变化**未验证（需用户操作）。
- **Native Messaging** 仅完成文档核查，未做端到端实测。
- **Edge 侧边栏（sidePanel）实际打开效果**未验证（manifest 与权限被接受，扩展正常加载）。
- **扩展 content script 在真实 chatgpt.com 上的注入与稳定性**未验证（需加载探针扩展）。
- **扩展写剪贴板 + 提示用户 Ctrl+V 的端到端链路**未验证（需扩展原型）。

---

## 二、实验结果与证据

完整逐条记录见 [`experiment-log.md`](experiment-log.md)。证据文件：

| 实验组 | 证据文件 | 结果 |
|---|---|---|
| A 会话寻址 | `_artifacts/playwright-evidence.json`（A-05）、`_artifacts/screenshots/06-chatgpt-public.png` | 公开页 403 challenge；无登录态无法观察会话 URL |
| B Edge 扩展 | `_artifacts/playwright-evidence.json`、`screenshots/01~05` | **25/25 通过** |
| C 页面状态/流式 | 同上（B-05 系列）+ 本地模拟页 | 生成中/完成状态可区分；结构化内容可提取 |
| D 本地通信 | `_artifacts/bridge-test-results.json`、`core-events.jsonl`（149 条事件）、`core-server.log` | **13/13 通过** |
| E Playwright | `_artifacts/playwright-generic-evidence.json` | **12/12 通过** |
| F Electron/iframe | `_artifacts/electron-evidence.json`、`_artifacts/iframe-evidence.json` | **9/9、4/4 通过** |
| G 官方路径 | `references/official-docs.md` | 文档核查（非实测） |

复现方式见 [`../README.md`](../README.md)。

---

## 三、候选方案比较（摘要）

完整对比见 [`architecture-comparison.md`](architecture-comparison.md)。

| 维度 | A Edge 扩展 + Core | B Playwright + Core | C Electron + Core | D 官方 MCP + Core | E 混合（A+D） |
|---|---|---|---|---|---|
| 原生体验 | **高**（用户原浏览器、原登录态） | 低（自动化窗口/独立 profile） | 中高（独立窗口，非用户日常浏览器） | **最高**（完全原生） | **高** |
| Session 连续性 | 高（URL 绑定 + 标签页观察） | 中（需 profile/登录态） | 中（独立 cookie jar，需重新登录） | 高（原生会话内） | 高 |
| Project 支持 | 依赖 URL/侧栏，未实测 | 同左 | 同左 | 高（原生 Projects） | 高 |
| 外部消息输入 | 需 DOM 注入（规则未确认） | 需 DOM 注入 | 需 DOM 注入 | 仅工具调用回合内 | 官方工具调用 + 人工确认注入 |
| 回复读取 | DOM 观察（脆弱） | DOM 观察（脆弱） | DOM 观察（脆弱） | 工具返回值（结构化） | 混合 |
| 双向通信 | WebSocket 实测可用 | 进程内/HTTP 可用 | IPC + HTTP 可用 | 公网 MCP/Tunnel | 可用 |
| 平台支持 | 浏览器扩展机制官方支持；对 chatgpt.com 的自动化未获官方背书 | 同左 | 同左 | **官方支持** | 部分官方 |
| 稳定性 | 依赖页面结构 | 依赖页面结构 + 浏览器版本 | 依赖页面结构 + Electron 版本 | 依赖官方 API 版本 | 中 |
| 安全性 | 需最小权限、token、Origin 校验 | 需保管登录态，风险更高 | 需隔离远程内容 | 官方鉴权 | 需分层 |
| 实现复杂度 | 中 | 中高 | 高 | 高（需公网/隧道 + 套餐） | 高 |
| 长期维护 | 页面改版即失效 | 同左，且更易被反自动化 | 同左 | 官方兼容性较好 | 中 |

---

## 四、架构影响

1. **“保留原生 ChatGPT 网页”与“外部可控”存在结构性张力，且合规边界比技术边界更紧。** 网页端没有官方消息注入/读取接口；OpenAI ToS 明确禁止“自动或程序化提取数据或输出”。因此任何 DOM 方案都必须把“读取回复”和“写入消息”设计为**用户显式触发的一次性动作**，而不是后台持续采集；自动注入应可关闭、默认关闭。
2. **会话引用可以用 URL，但只能作为“用户手动绑定”的弱引用。** URL 由用户提供/确认，Core 只保存与展示，不假设其永久稳定；同时用标签页 ID + 标题 + 最近消息摘要做一致性校验。
3. **扩展是“观察者 + 受控执行器”，Core 是“确定性状态机”。** 实测证明扩展适合做页面状态采集与受控 DOM 操作；语义决策仍应在 Coordinator。
4. **通信层选 WebSocket 而非 Native Messaging。** 实测 WS 已满足双向、重连、排队；Native Messaging 需注册表 + 每浏览器安装，且 host→browser 单条 1 MB 上限，收益不足以抵消部署成本（第一版）。
5. **Electron 不是“绕过限制”的手段，而是“换一个容器”。** 它能顶层加载 chatgpt.com（iframe 不行，实测 XFO=SAMEORIGIN），但登录态是独立 cookie jar，用户需在 Electron 内重新登录；若账号使用 Google SSO，“Continue with Google”在嵌入式 webview 中会被 Google 政策阻断（`disallowed_useragent`），可能是硬阻塞。**因此 Electron 路线必须先确认用户的 ChatGPT 登录方式。**
6. **官方 MCP 路径是长期正解，但当前受套餐与部署形态限制。** 若用户是 Plus/Pro 个人账号，完整工具调用不可用；且本地 Core 必须经公网 HTTPS 或官方 Tunnel。它更适合“Coordinator 主动查询 Core”的拉取式协作，而非“Core 主动推送进会话”。
7. **Electron 的安全默认值有坑。** 官方安全清单指出 Electron 默认**自动批准所有权限请求**，加载远程站点时必须显式设置 `setPermissionRequestHandler`；远程页不得启用 Node、必须 `contextIsolation:true`/`sandbox:true`。

---

## 五、未解决问题

见 [`unresolved-questions.md`](unresolved-questions.md)。按优先级：

1. 真实登录态下 ChatGPT 会话 URL 是否稳定、项目内会话 URL 形态（需用户授权后 10 分钟即可验证）。
2. ChatGPT 页面 DOM 的消息边界、流式完成信号、多模态内容结构（需登录态 + 只读观察）。
3. 平台规则是否允许扩展在用户确认下向输入框写入并发送消息（需查 Terms/Usage Policies 并做合规判断）。
4. Edge 侧边栏作为工作台的实际体验与限制（需人工加载扩展验证）。
5. 官方 MCP 路径在用户实际套餐下的可用性（需用户确认套餐类型）。
6. Native Messaging 端到端（未执行）。

---

## 六、推荐技术路线

**第一版（MVP）推荐：方案 E 的收敛形态 —— “Edge 扩展观察 + 用户手动绑定 + 显式确认注入 + 本地 Core WebSocket”**

- **Coordinator**：继续用用户日常 Edge 中的 ChatGPT 原生会话，不做任何隐藏自动化。
- **Edge 扩展**：
  - 只申请必要权限：`tabs`、`webNavigation`、`storage`、`scripting`、`alarms`、`sidePanel`，host 仅 `chatgpt.com` 与 `127.0.0.1`。
  - 负责：识别当前会话 URL/标题、观察导航与生成状态；**回复由用户点击“捕获本轮”触发一次结构化提取**；Core 消息由用户点击“填入输入框”后**由用户自行发送**（第一版不自动发送）。
  - 不默认自动发送、不后台持续提取、不读取完整聊天历史、不接触 Cookie/Token。
- **Core（Python）**：`127.0.0.1` + WebSocket（事件上行、通知下行）+ 离线队列 + token/Origin 校验；SQLite 持久化事件（当前实验版为内存，已记录为限制）。
- **消息投递**：Core → 扩展 → 页面内提示条（实测可行）；“填入输入框”作为**用户手势触发**的能力，默认不自动发送，待平台规则确认后再评估。
- **Electron**：二期。当出现“需要独立工作台窗口 / 多分栏 / 全局快捷键 / 不依赖用户浏览器”的需求时再引入；届时需接受独立登录态。

**不推荐**：把 Playwright 作为长期 Coordinator Adapter（登录态、反自动化、与用户操作冲突风险最高），仅保留为一次性探测与自动化测试工具。

---

## 七、下一轮实验建议

| 优先级 | 实验 | 需要用户做什么 | 预计耗时 |
|---|---|---|---|
| P0 | 真实会话 URL 稳定性（A-01/A-02/A-03） | 在 Edge 登录 ChatGPT，手动打开 2 个测试会话与 1 个项目，授权扩展只读观察 | 15 分钟 |
| P0 | 真实 DOM 只读观察（C-01） | 同上；扩展只上报结构统计，不读内容 | 20 分钟 |
| P0 | 平台规则核查（G-01） | 确认账号套餐；共同阅读 Terms/Usage Policies 相关条款 | 15 分钟 |
| P1 | 侧边栏工作台原型（B-10） | 手动加载扩展、打开侧边栏、评价体验 | 15 分钟 |
| P1 | 用户确认式注入（C-02） | 在测试会话中由用户点击确认，验证“填入但不发送/发送”两条路径 | 20 分钟 |
| P2 | Native Messaging 端到端（D-04） | 无需（可自动完成，需写注册表） | 20 分钟 |
| P2 | 官方 MCP 可行性（G-02） | 确认套餐；如需，配置 Secure MCP Tunnel | 1–2 小时 |

---

## 八、最终报告 10 问回答

**Q1. 目前是否存在官方支持的方式，让 ChatGPT 网页端 Coordinator 与本地 ContextFlow Core 交互？**
有，但不是“本地直连”。官方要求 MCP server 是公网 HTTPS 端点，或经官方 **Secure MCP Tunnel**（出站隧道，可托管本地 stdio 进程）。完整自定义 MCP 连接器仅 Business/Enterprise/Edu；Pro 仅 read/fetch；Plus 未说明。官方**没有**“向已有网页会话注入消息”的机制。→ 个人账号下不能作为第一版主通道。`[官方 K12–K15]`

**Q2. Edge 扩展能否可靠识别用户当前所在的 ChatGPT Session？**
机制上可以：`tabs`/`webNavigation` 提供 URL 与导航事件，content script 可读 DOM 与标题，实测在模拟页上 25/25 通过。但**真实 ChatGPT 页面未实测**，且 URL 只能作为弱引用。`[实测 B-01~B-09；未达 L4]`

**Q3. Session URL 是否足以作为 ContextFlow 第一版的外部会话引用？**
可以作为**用户手动绑定的弱引用**：`/c/<id>` 形态有官方间接确认；刷新/新标签页通常恢复同一会话。但不能保证永久稳定（官方无 permalink 承诺）、项目内规范 URL 会变化、`/c/<id>` 仅在所有者登录态可解析、程序化获取受 ToS 限制。`[官方 K18；第三方 K19；官方 K20]`

**Q4. 通过网页 DOM 读取和发送消息有哪些技术与平台限制？**
技术：隔离世界（需 `chrome.scripting` 进 MAIN world）、CSP、页面结构不稳定、流式结束信号不可靠、虚拟滚动导致历史不在 DOM、多模态内容结构复杂。平台：ToS 禁止“自动或程序化提取数据或输出”、无官方支持、存在反自动化措施。`[实测 B-04/B-05；官方 K20]`

**Q5. 能否通过官方工具接口替代部分网页自动化？**
可以替代**语义通信**部分（Coordinator 通过工具调用创建任务、查询状态、取回结果），不能替代“Core 主动向已有会话推送消息”。且受套餐、公网端点/Tunnel、120 秒请求预算、工具快照冻结等限制。`[官方 K12–K17]`

**Q6. 本地 Python Core 与 Edge 扩展之间最简单、可靠的通信方案是什么？**
`127.0.0.1` 上的 **WebSocket**：实测双向、离线排队、重连补发、token/Origin 校验全部通过（13/13 + 浏览器全链路）；HTTP POST 作为兜底。Native Messaging 需注册表安装且 host→browser 单条 1 MB 上限，第一版不推荐。`[实测 D-01~D-03, B-07]`

**Q7. 为了保留 ChatGPT 原生体验，第一版更适合 Edge 扩展还是 Electron？**
**Edge 扩展**。它复用用户日常浏览器的登录态与原标签页；Electron 是独立 cookie jar，需重新登录，且 Google SSO 在嵌入式 webview 中被 Google 政策阻断。Electron 的优势是窗口/分栏/全局快捷键，属于二期需求。`[实测 F-01~F-09；官方 Electron/Google]`

**Q8. 哪些能力已经实际验证，哪些只是理论可行？**
已实测（L3）：扩展加载与页面识别、SPA 导航检测、隔离世界/MAIN world、流式状态观察、扩展内部通信、Core 双向通信与异常路径、Playwright 全部能力、Electron 双栏与顶层加载 chatgpt.com、iframe 被拒。
仅理论/文档（L5 或推断）：真实 ChatGPT 会话 URL 稳定性、真实 DOM 结构、消息发送、官方 MCP 在用户套餐下的可用性、Native Messaging、侧边栏体验。

**Q9. 当前最大的架构性阻碍是什么？**
不是浏览器技术，而是**平台没有官方的网页会话读写接口，且 ToS 限制程序化提取**。这决定了“自动读取/自动发送”不能作为第一版默认能力；浏览器扩展只能做“用户显式触发”的受控助手。其次是页面结构脆弱性带来的长期维护成本。

**Q10. 下一轮最应该进行的实验是什么？**
**真实登录态下的只读观察**：用户在 Edge 登录 ChatGPT，打开 2 个测试会话 + 1 个 Project，授权扩展只读上报 URL 变化序列与 DOM 结构统计（不读内容）。预计 15–20 分钟，可一次性回答 U1/U2/U10 三个 P0 问题。

---

## 九、最终结论

**基于目前证据，ContextFlow 第一版最值得采用的 Coordinator 集成路径是：**

> **以用户日常 Edge 中的 ChatGPT 原生会话为 Coordinator 界面，用最小权限 MV3 扩展做“会话观察 + 用户手动绑定 + 显式确认后的受控输入”，扩展与本地 Python Core 之间用 `127.0.0.1` WebSocket 双向通信；不把自动发送/自动读取作为默认能力，不把 Playwright 作为长期方案，Electron 留作二期容器候选，官方 MCP/Tunnel 作为语义协作的长期演进方向。**

理由：这是唯一同时满足“保留原生体验、复用现有登录态、双向通信已被实测验证、权限与安全边界可控”的组合；其最大代价是依赖 ChatGPT 页面结构（需以适配层与降级策略管理），以及“自动注入”仍待平台规则确认（因此第一版必须设计为用户确认式）。

---

## 十、状态与下一轮（CF-EXP-002）

**CF-EXP-001 状态：技术探索完成，架构选型未完成。**

2026-11-04 23:00，DSH 通过浏览器自动化把本报告实测结果投递到用户真实 ChatGPT 会话「技术验证规划」，取回其评审意见（6042 字符 Markdown，见 `_artifacts/chatgpt-reply.md`）。评审**认可** Edge 扩展 + Python Core 方向，但提出关键调整：**不要把“剪贴板读取完整回复”确立为正式通信协议**，剪贴板应定位为用户主动触发的交接工具。

评审建议的下一轮三个实验（详见 [`review-and-next-round.md`](review-and-next-round.md)）：

| 编号 | 实验 | 优先级 | 状态 |
|---|---|---|---|
| CF-EXP-002A | 官方 MCP 集成能力验证 | 最高（架构决定性） | **受阻**：用户账号为 Plus，官方完整自定义 MCP 仅 Business/Enterprise/Edu；需先确认套餐/工作区 |
| CF-EXP-002B | 真实 Edge 多 Session 绑定与侧边栏体验 | 高（产品体验） | 待做（需加载扩展 + 用户操作） |
| CF-EXP-002C | 用户确认式消息交接闭环 | 高（最小可用工作流） | **部分完成**：CF-EXP-002C-1 已在扩展侧实现四态交接并通过 7/7 测试（`_artifacts/handoff-evidence.json`） |

**本轮已执行的落地工作**：在探针扩展中实现“待处理 → 已复制 → 已确认/已取消”四态交接面板，验证“扩展只写剪贴板、不自动发送”可行（7/7）。

**下一步建议**：先确认用户套餐（决定 002A 是否可做），再做 002B 的侧边栏体验验证，最后补 002C 的真实页面端到端闭环。
