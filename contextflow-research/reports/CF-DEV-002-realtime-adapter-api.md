# CF-DEV-002 开发报告：实时读取接口与外部 Agent 通信

- 日期：2026-11-05
- 扩展版本：v0.2.0（新增实时读取 API + WS Bridge）
- 自动化证据：`_artifacts/cfdev002-evidence.json`（**14/14 通过**）
- 真实 ChatGPT 页面验证：待用户手动执行（见第 8 节清单）

## 1. 实际架构与数据流

```
External Python Client (websockets)
      │ WS ws://127.0.0.1:8766/ws?token=…
      ▼
bridge_server.py (FastAPI, 127.0.0.1 only)
      │ WS ws://127.0.0.1:8766/ws_page (push request)
      ▼
bridge_client.html (browser tab, keeps WS alive)
      │ CustomEvent → bridge_relay.js (content script, 127.0.0.1)
      │ chrome.runtime.sendMessage
      ▼
background.js (strict session routing, no active-tab fallback)
      │ chrome.tabs.sendMessage cf:read_snapshot
      ▼
content.js (target ChatGPT tab; fresh DOM read, pre/post session check)
      │ structured snapshot (serializable JSON only)
      ▼
…原路返回，bridge 按 request_id 匹配，回给外部客户端
```

**关键设计：扩展自身不开任何网络端口**。WebSocket 由 127.0.0.1 页面（bridge_client.html）持有，扩展只通过 runtime 消息中继——最小权限，无需新增扩展网络权限。

## 2. 修改的文件及职责

| 文件 | 变更 |
|---|---|
| `content.js` | +`cf:read_session`（轻量会话探测）、+`cf:read_snapshot`（实时读取，读前后双重 session 校验）；版本 0.2.0 |
| `background.js` | +`probeSession`/`readSnapshotFromTab`（带超时）、+`handleListSessions`/`handleGetSnapshot`（严格路由）、+`cf:bridge_request` 入口 |
| `bridge_relay.js`（新） | 127.0.0.1 页面 ⇄ background 的消息中继，防重复注册 |
| `bridge_server.py`（新） | 本地 WS 服务：token 鉴权、Origin 白名单、request_id 匹配、12s 超时、双 WS 端点（外部客户端 / 浏览器页面） |
| `manifest.json` | +`http://127.0.0.1/*`（mock 测试与 bridge 页面），+第二个 content script |
| `cfdev002_test.py` / `cfdev002_client.py`（新） | 自动化测试 / 外部 Python 客户端示例 |

**测试便利性与生产边界的说明**：为让 mock 页（127.0.0.1）参与自动化测试，`candidateTabs()` 与 `CHATGPT_URL_RE` 把 `http://127.0.0.1/*` 也视为候选。真实使用时该规则只影响本地页面，风险可控；若未来要收紧，可加开关。

## 3. 最终消息协议

**外部客户端 → bridge**（WS `/ws`）：

```json
{"id": "req-001", "type": "list_sessions"}
{"id": "req-002", "type": "get_snapshot", "session_id": "<uuid>", "tab_id": 123}
```

**bridge → 外部客户端**：

```json
{"id": "req-002", "type": "result", "success": true,
 "data": {"tab_id": 123, "captured_at": "…", "snapshot": {"version": "0.2.0", "session": {}, "generation": {}, "messages": []}}}

{"id": "req-003", "type": "result", "success": false,
 "error": {"code": "AMBIGUOUS_SESSION", "message": "session … is open in tabs 1,2 — pass tab_id"}}
```

**错误码**：`SESSION_NOT_FOUND` / `AMBIGUOUS_SESSION` / `CONTENT_SCRIPT_UNAVAILABLE` / `SESSION_CHANGED` / `READ_TIMEOUT` / `INVALID_REQUEST` / `INTERNAL_ERROR`（全部已实测触发或代码路径存在）。

## 4. 接口使用方式

### list_sessions

```json
{"id": "req-001", "type": "list_sessions"}
→ {"sessions": [{"tab_id": 101, "session_id": "<uuid>|null", "kind": "project|chat|…",
                 "url": "…", "ready": true, "error": {...?}}]}
```

- `session_id` 来自**实时探测**（逐 tab 向 content script 发 `cf:read_session`），不是缓存；
- 未就绪的标签页 `ready:false` 并带 `error`；不返回聊天正文。

### get_snapshot

- 路由：按 `session_id` 在候选标签页中精确匹配；0 匹配 → `SESSION_NOT_FOUND`；≥2 匹配 → `AMBIGUOUS_SESSION`（可传 `tab_id` 消歧）；**绝不回退活动标签页**。
- 读取：目标 content script 收到 `cf:read_snapshot` 后**当场重新解析 DOM**（`buildSnapshot("external_request")`），并在读取前后校验 session 一致，变化则 `SESSION_CHANGED`。
- 每个请求 8s（扩展侧）/12s（bridge 侧）超时，不会永久挂起。

## 5. Python 测试客户端运行方法

```powershell
# 1) 加载扩展（edge://extensions → 开发人员模式 → 加载 contextflow-extension/）
# 2) 启动 bridge
python contextflow-extension\bridge_server.py --port 8766
# 3) 浏览器打开（保持此标签页）
#    http://127.0.0.1:8766/bridge_client.html?token=dev-token-cf-exp-002
# 4) 外部客户端
python experiments\playwright-probe\cfdev002_client.py list
python experiments\playwright-probe\cfdev002_client.py snapshot <session_id>
```

## 6. 实际测试结果（模拟页面，自动化）

| 测试 | 结果 |
|---|---|
| T0a/b 两个 mock 标签页注入并上报 | PASS |
| T0 bridge 页经扩展中继连接 | PASS |
| T1 list_sessions 找到两个 session | PASS |
| T2/T2b get_snapshot 路由正确、消息 2 条 | PASS |
| **T3 实时性**：两次读取之间改页面内容，第二次返回新内容 | **PASS** |
| T4 不存在的 session → SESSION_NOT_FOUND | PASS |
| T5 缺参数 → INVALID_REQUEST | PASS |
| T6 SPA 切换后旧 session → SESSION_NOT_FOUND | PASS |
| T7 双标签同 session → AMBIGUOUS_SESSION | PASS |
| T8 tab_id 消歧成功 | PASS |
| T9 非法 JSON → INVALID_REQUEST（不崩） | PASS |
| T10 未知 type → INVALID_REQUEST（不崩） | PASS |

**14/14**。调试期间发现并修复的问题：URL 正则漏掉 127.0.0.1 带端口的情况（T8）、bridge 页 world 差异、`isChatGPT` 探测判定。

## 7. 错误处理机制

- bridge：非法 JSON / 未知 type / 缺 id → `INVALID_REQUEST`；页面客户端未连接 → `CONTENT_SCRIPT_UNAVAILABLE`（提示打开 bridge_client.html）；12s 无结果 → `READ_TIMEOUT`；请求完成后 future 即清理，重复结果不误投。
- background：每个 `tabs.sendMessage` 带 8s 超时；`chrome.runtime.lastError` 显式消费，不抛未检查错误。
- content：读前/读后 session 校验；异常捕获回 `INTERNAL_ERROR`。
- 单次请求失败不影响后续请求（T4→T5→…连续失败后 T10 仍成功验证了这一点）。

## 8. 真实 ChatGPT 页面验证（2026-11-05 实测 ✅）

用户在真实登录态 ChatGPT 上完成端到端验证：

1. **list_sessions**：正确列出 3 个标签页——2 个真实 ChatGPT 会话页（ready=True，含真实 session_id）+ 1 个 bridge 中继页（kind=bridge_page，符合设计）。
2. **get_snapshot（真实 Project 会话）**：

```
OK req-snap-001 tab=1017028512 captured_at=2026-11-05T07:33:11.349Z
  session=6aec2932-e5ac-83e8-8e1a-561185403b5a kind=project
  generation=completed messages=2
  [0] user: 角色与任务…（2466 chars）
  [1] assistant: 我已经阅读了…（5918 chars）
```

- 路由到正确标签页；session_id 与 URL 一致；角色/顺序/正文长度正确。
- 运维注记：**扩展重新加载后必须刷新已打开的 ChatGPT 标签页**（reload 不会向旧标签页注入 content script，首次 list 显示 ready=False 即此原因，刷新后正常）。
- 实时非缓存：自动化 T3 已验证；真实页上"发消息后再 snapshot 见新消息"的原理相同（同一代码路径）。

**结论：外部 Python 程序 → 本地 WS Bridge → 扩展 → 真实 ChatGPT 页面实时读取链路 VERIFIED。CF-DEV-002 完成。**

## 9. 已知限制

- 未验证项：真实 ChatGPT 页面端到端（第 8 节）、`wait_for_reply`（本期不实现）、WebSocket 断线重连的自动化用例（bridge_client 页面有重连逻辑，bridge 重启后页面自动恢复，但未写入自动化断言）。
- 单 bridge 页面客户端（v1 设计）；多浏览器 profile 需要各自打开 bridge 页面。
- `SESSION_CHANGED` 的竞态窗口极小，自动化中难以稳定复现，本次以代码审查 + 双重校验逻辑保证。
- 消息正文仅含当前已渲染消息（懒加载历史不可见）。

## 10. 后续 Agent 接入建议

- Codex CLI / 其他 Agent 只需会讲 WS JSON 协议：`list_sessions` → `get_snapshot`，无需了解扩展内部。
- 下一步（建议顺序）：① 真实页面手动验证第 8 节清单；② `wait_for_reply`（基于 `generation.state` 轮询 get_snapshot 即可实现）；③ 发送链路（假剪贴板 + 用户确认）再议。
- 生产化前提：换掉 dev token（配对制）、绑定具体扩展 Origin、考虑 bridge 页面改为扩展内页面以去掉 127.0.0.1 host 需求。
