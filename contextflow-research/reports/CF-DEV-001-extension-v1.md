# CF-DEV-001 开发报告：ContextFlow ChatGPT Adapter 扩展 v1

- 日期：2026-11-05（v0.1.1 同日修订）
- 扩展目录：`contextflow-extension/`（manifest.json + content.js + background.js + README.md）
- 自动化证据：`_artifacts/cfdev001-evidence.json`（20/20 通过）
- 真实页面验证：由用户在真实登录态 ChatGPT 页面手动执行（见第 4 节清单）

## v0.1.1 修订（真实页面实测发现）

用户实测暴露一个真 bug：**旧会话 ID 污染**。切到项目页（`/g/<id>/project`）或
新草稿（URL 为 `/c/local-chatgpt%3A<uuid>`）时，URL 中没有合法的服务端会话 ID，
旧版回退读取 DOM 属性，但该属性保留**上一个会话的 ID**，导致短暂的错误归属。

修复：
1. 删除 DOM 回退——URL 无法给出服务端会话 ID 时返回 `session_id: null, kind: "draft"`（宁可 unknown 不猜测）；
2. SPA 导航检测只比较 `location.pathname`，URL 查询参数抖动不再误报；
3. DOM 会话属性降级为 `candidates` 诊断字段。

真实页面复验：项目会话 `session_id` 与 URL 一致（`6aec1f0a-...`，source=url:project_chat），`__cfAdapter.snapshot()` 在 top 上下文可正常调用。

**运维注记**：`__cfAdapter` 只存在于主页面。ChatGPT 有多个 iframe（runner.html 等），
DevTools Console 需把执行上下文切到 **top** 才能访问。

## 1. 扩展现在能自动完成哪些操作

| 操作 | 实现 | 说明 |
|---|---|---|
| 页面注入后自动识别会话 | ✅ | URL `/c/<id>` 为准，`/g/.../c/` 识别 project |
| 自动读取已渲染消息 | ✅ | 回合容器 → 角色/ID/正文 |
| Markdown 结构还原 | ✅ | title/blockquote/table/code_block 按语义属性 |
| 生成状态观察 | ✅ | `data-talvt-turn-state` + 停止按钮 |
| MutationObserver 增量上报 | ✅ | 400ms 防抖 + 10s 心跳 + 签名去重 |
| SPA 导航跟随 | ✅ | 1s 轮询 URL 变化，切换后强制重新上报 |
| per-tab 状态汇总 | ✅ | background 按 tabId 存储并持久化到 `chrome.storage.session` |
| 刷新/Service Worker 重启恢复 | ✅ | SW 从 storage.session 恢复标签页状态 |

## 2. 已验证能力分级

**VERIFIED（真实页面）**：会话识别（project 会话 `/c/6aeb4db5-...`）、user/assistant 角色交替、消息 ID（去重）、标题/引用/表格/代码块 Markdown 还原（` ```python `）、生成状态 `in_progress→complete` 时间线（停止按钮早 ~1.9s）。

**VERIFIED（自动化，mock ChatGPT DOM）**：见第 4 节 20 项清单。

**IMPLEMENTED_UNVERIFIED**：SW 重启后 storage.session 恢复（代码存在，本次未复现）、`cf:get_state`/`cf:ping` 消息（监听器存在，未端到端验证）。

**NOT_IMPLEMENTED**：消息发送、外部 Agent 通信（WebSocket/request_id）、会话严格路由。

## 3. 消息读取是否准确？

- 角色：`h4[data-conversation-role]` 优先，turn key 后缀兜底 —— 8 条真实消息全部正确。
- 消息 ID：`data-chatgpt-search-message-ids` 空格分隔去重 + `data-dil-message-id` 交叉校验。
- 正文：按 `data-d-component` / `data-code-block` 语义属性重建 Markdown；按钮文本（"复制"等）通过 `data-markdown-copy="exclude"` 排除，测试确认未泄漏。
- 顺序：按 DOM 出现顺序 `order` 编号；turn key 的中段数字不可靠（观察到 `:2:` 跳号），不用于排序。
- 会话归属：每条消息带 `session_id`，切换会话后旧消息不会混入（E4-02/03）。

**保真度说明**：标题层级（h1-h3）、引用、表格、代码块（含语言标签）保真；嵌套列表/混合结构可能退化为纯文本；流式中的部分内容会随 `in_progress` 状态更新。

## 4. 自动化测试结果（20/20）

| 组 | 检查项 | 结果 |
|---|---|---|
| E1 | MV3 扩展加载、SW 存在 | PASS |
| E2 | content script 自动运行 | PASS |
| E3 | 会话识别（不伪造 ID）、消息读取、角色、ID 去重、代码块+语言、表格、按钮文本不泄漏、生成状态、status 语义 | 9/9 PASS |
| E4 | SPA 导航跟随、旧会话不混淆、session_id 传播 | 3/3 PASS |
| E5 | background 钩子存活；真实链路留待真实页手动验证 | 2/2 PASS |
| E6 | 流式 generating→completed、最后一条 status 跟随 | 3/3 PASS |
| E7 | 空白页不崩溃、不伪造 session | PASS |

注：mock 测试通过 `add_init_script` 注入 content.js（模拟页无 `chrome.runtime` 上下文，`cf:state` 上报链路需真实页面验证——这正是用户手动测试的部分）。

## 5. 是否能持续观察新消息？

是。`MutationObserver`（childList+subtree+characterData）触发防抖上报；签名（session+generation+每条消息 order/role/length/status）去重，避免重复日志；10s 心跳兜底。真实页面流式时间线（停止按钮 8.27s 出现、`in_progress` 10.17s、`complete` 11.56s）证明观察粒度足够。

## 6. 会话切换处理

URL 轮询检测 SPA 导航 → 清签名 → 立即重新快照；快照内所有消息携带当前 `session_id`。自动化验证：切换后旧会话消息不残留（E4）。双标签页由 background 按 `sender.tab.id` 分桶，不会互相覆盖。

## 7. 是否需要本地程序？

**不需要。** v1 仅依赖浏览器；background 不发起任何外部连接。

## 8. 下一步实现 Agent 双向通信还缺少什么

1. background → 本地 WebSocket（复用 CF-EXP-001 的 core bridge 设计）；
2. 请求-响应协议：`request_id`、超时、错误回包；
3. 会话严格路由：`session_ref` 必须唯一匹配 tab，失败报错**不回退**（修复旧审计发现的字段 bug）；
4. 读消息 API：`read_messages { session_ref } → { request_id, messages }`；
5. （更远）发送链路：假剪贴板注入 + 用户确认 + 发送后校验。

## 9. 已知限制

- 只读当前已渲染消息；长会话历史懒加载。
- Selector 为网页内部实现，改版会失效；异常时返回 `unknown` 不猜测。
- 用户自己的优化脚本（userscript）会改 DOM，属环境变量，排查时先禁用对照。
- `selection` DOM 属性是 `local-chatgpt:<uuid>`，不是服务端会话 ID（已在代码中只作候选）。

## 10. 手动验证清单（用户执行）— 实测记录

| 验证项 | 结果（v0.1.1 实测） |
|---|---|
| 扩展加载、content 注入 | ✅ `content v0.1.1 ready` |
| 消息增量上报 | ✅ `msgs=2→4` 随 DOM 变化 |
| 生成状态 | ✅ `gen=generating` → `gen=completed` |
| SPA 会话切换 | ✅ `SPA navigation` + session 跟随 |
| 项目页/草稿页 | ✅ v0.1.1 修复后不再带旧会话 ID |
| `__cfAdapter.snapshot()` | ✅ 结构完整（注意 Console 上下文须选 top） |
| 刷新恢复 / 双标签 | 待用户复测 |

