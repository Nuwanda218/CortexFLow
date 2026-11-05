# Critical Code Excerpts

> 所有片段均来自 `source/` 下的真实源码（与仓库同路径），行号对应原始文件。
> 实验用 token 已脱敏为 `dev-token-***MASKED***`。

## 扩展权限与 host_permissions

`experiments/edge-extension-probe/manifest.json` 第 7–22 行

```json
   7 |   "permissions": [
   8 |     "tabs",
   9 |     "webNavigation",
  10 |     "storage",
  11 |     "scripting",
  12 |     "alarms",
  13 |     "sidePanel",
  14 |     "clipboardWrite",
  15 |     "clipboardRead"
  16 |   ],
  17 |   "host_permissions": [
  18 |     "http://127.0.0.1/*",
  19 |     "http://localhost/*",
  20 |     "https://chatgpt.com/*",
  21 |     "https://chat.openai.com/*"
  22 |   ],
```

## Core 地址与实验 token（已脱敏）

`experiments/edge-extension-probe/background.js` 第 15–18 行

```javascript
  15 | const CORE_WS_URL = "ws://127.0.0.1:8765/ws";
  16 | const CORE_TOKEN = "dev-token-***MASKED***"; // experiment-only shared secret
  17 | const MAX_EVENTS = 200;
  18 | const INSTANCE_ID = (globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now());
```

## WebSocket 连接、重连退避、发送

`experiments/edge-extension-probe/background.js` 第 56–103 行

```javascript
  56 | function connectCore() {
  57 |   if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) return;
  58 |   setWsStatus("connecting");
  59 |   state.wsAttempts += 1;
  60 |   let ws;
  61 |   try {
  62 |     ws = new WebSocket(`${CORE_WS_URL}?token=${encodeURIComponent(CORE_TOKEN)}`);
  63 |   } catch (err) {
  64 |     state.lastError = String(err);
  65 |     setWsStatus("disconnected");
  66 |     return;
  67 |   }
  68 |   state.ws = ws;
  69 |   ws.addEventListener("open", () => {
  70 |     setWsStatus("connected");
  71 |     state.lastError = null;
  72 |     ws.send(JSON.stringify({ type: "hello", source: "edge_extension", version: chrome.runtime.getManifest().version }));
  73 |   });
  74 |   ws.addEventListener("message", (ev) => {
  75 |     let msg;
  76 |     try { msg = JSON.parse(ev.data); } catch (_) { return; }
  77 |     state.coreCommands.push({ at: nowIso(), msg });
  78 |     if (state.coreCommands.length > MAX_EVENTS) state.coreCommands.shift();
  79 |     handleCoreCommand(msg);
  80 |   });
  81 |   ws.addEventListener("close", () => {
  82 |     setWsStatus("disconnected");
  83 |     scheduleReconnect();
  84 |   });
  85 |   ws.addEventListener("error", () => {
  86 |     state.lastError = "websocket error";
  87 |     // close event follows; reconnect is scheduled there.
  88 |   });
  89 | }
  90 | 
  91 | let reconnectTimer = null;
  92 | function scheduleReconnect() {
  93 |   if (reconnectTimer) return;
  94 |   const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(state.wsAttempts, 5)));
  95 |   reconnectTimer = setTimeout(() => { reconnectTimer = null; connectCore(); }, delay);
  96 | }
  97 | 
  98 | function sendToCore(msg) {
  99 |   if (state.ws && state.ws.readyState === WebSocket.OPEN) {
 100 |     try { state.ws.send(JSON.stringify(msg)); return true; } catch (_) { return false; }
 101 |   }
 102 |   return false;
 103 | }
```

## Core 命令处理与 session_ref 路由（含字段不匹配 bug）

`experiments/edge-extension-probe/background.js` 第 105–125 行

```javascript
 105 | async function handleCoreCommand(msg) {
 106 |   if (!msg || typeof msg.type !== "string") return;
 107 |   if (msg.type === "worker_completed" || msg.type === "notify") {
 108 |     // Route to the tab bound to the target session, else the active tab.
 109 |     const tabs = await chrome.tabs.query({});
 110 |     let target = null;
 111 |     if (msg.session_ref) {
 112 |       target = tabs.find((t) => {
 113 |         const ps = state.lastPageStateByTab[t.id];
 114 |         return ps && ps.session_ref === msg.session_ref;
 115 |       });
 116 |     }
 117 |     if (!target) target = tabs.find((t) => t.active);
 118 |     if (target) {
 119 |       chrome.tabs.sendMessage(target.id, { type: "cf:core_notice", payload: msg }).catch(() => {});
 120 |     }
 121 |     broadcast({ type: "cf:core_notice", payload: msg });
 122 |   } else if (msg.type === "ping") {
 123 |     sendToCore({ type: "pong", at: nowIso() });
 124 |   }
 125 | }
```

## SPA 导航事件监听

`experiments/edge-extension-probe/background.js` 第 143–151 行

```javascript
 143 | chrome.webNavigation.onHistoryStateUpdated.addListener((d) => {
 144 |   if (d.frameId !== 0) return;
 145 |   recordNav({ kind: "webNavigation.onHistoryStateUpdated", tabId: d.tabId, url: d.url });
 146 | });
 147 | 
 148 | chrome.webNavigation.onReferenceFragmentUpdated.addListener((d) => {
 149 |   if (d.frameId !== 0) return;
 150 |   recordNav({ kind: "webNavigation.onReferenceFragmentUpdated", tabId: d.tabId, url: d.url });
 151 | });
```

## 页面状态上报与缓存

`experiments/edge-extension-probe/background.js` 第 162–170 行

```javascript
 162 |   if (msg.type === "cf:page_state") {
 163 |     const tabId = sender.tab ? sender.tab.id : null;
 164 |     if (tabId != null) state.lastPageStateByTab[tabId] = { ...msg.payload, tabId, at: nowIso() };
 165 |     chrome.storage.session.set({ lastPageStateByTab: state.lastPageStateByTab }).catch(() => {});
 166 |     sendToCore({ type: "page_state", source: "edge_extension", tabId, ...msg.payload });
 167 |     broadcast({ type: "cf:page_state", tabId, payload: msg.payload });
 168 |     sendResponse({ ok: true });
 169 |     return true;
 170 |   }
```

## chrome.scripting MAIN world 探测

`experiments/edge-extension-probe/background.js` 第 193–217 行

```javascript
 193 |   if (msg.type === "cf:main_world_probe") {
 194 |     // Official MV3 way to run code in the page's MAIN world: chrome.scripting.
 195 |     // It is not subject to the page CSP (unlike an injected inline <script>).
 196 |     const tabId = msg.tabId != null ? msg.tabId : (sender.tab ? sender.tab.id : null);
 197 |     if (tabId == null) { sendResponse({ ok: false, error: "no tabId" }); return true; }
 198 |     chrome.scripting.executeScript({
 199 |       target: { tabId },
 200 |       world: "MAIN",
 201 |       func: () => {
 202 |         try {
 203 |           const mc = window.__mockChat;
 204 |           if (!mc) return { available: false, error: "window.__mockChat is undefined in MAIN world" };
 205 |           return {
 206 |             available: true,
 207 |             data: { version: mc.version, sessionId: mc.sessionId(), state: mc.state(), messageCount: mc.messageCount(), pushStateCalls: mc.pushStateCalls },
 208 |             error: null,
 209 |           };
 210 |         } catch (err) {
 211 |           return { available: false, error: String(err) };
 212 |         }
 213 |       },
 214 |     }).then((results) => sendResponse({ ok: true, result: results && results[0] ? results[0].result : null }))
 215 |       .catch((err) => sendResponse({ ok: false, error: String(err) }));
 216 |     return true;
 217 |   }
```

## 页面分类、URL 会话引用、mock 会话

`experiments/edge-extension-probe/content.js` 第 83–111 行

```javascript
  83 |   function pageKind() {
  84 |     const h = location.hostname;
  85 |     if (h === "chatgpt.com" || h === "chat.openai.com") return "chatgpt";
  86 |     if (h === "127.0.0.1" || h === "localhost") return "mock-chat";
  87 |     return "other";
  88 |   }
  89 | 
  90 |   // URL-shape based session reference. For ChatGPT this is a *hypothesis* about
  91 |   // the route, not an official stable id; the experiment records the raw shape.
  92 |   function sessionRefFromUrl() {
  93 |     const p = location.pathname;
  94 |     const chat = p.match(/\/c\/([0-9a-fA-F-]{8,})/);
  95 |     if (chat) return { kind: "chat_path", value: chat[1] };
  96 |     const projChat = p.match(/\/g\/([^/]+)\/c\/([0-9a-fA-F-]{8,})/);
  97 |     if (projChat) return { kind: "project_chat_path", project: projChat[1], value: projChat[2] };
  98 |     const proj = p.match(/\/g\/([^/]+)\/project/);
  99 |     if (proj) return { kind: "project_path", value: proj[1] };
 100 |     return { kind: "none", value: null };
 101 |   }
 102 | 
 103 |   function mockSession() {
 104 |     const body = document.body;
 105 |     if (!body) return null;
 106 |     const sid = body.dataset ? body.dataset.sessionId : null;
 107 |     if (sid) return { kind: "mock_body_dataset", value: sid };
 108 |     const conv = document.getElementById("conversation");
 109 |     if (conv && conv.dataset.conversationId) return { kind: "mock_conversation_dataset", value: conv.dataset.conversationId };
 110 |     return null;
 111 |   }
```

## readPageState：消息/生成状态解析（仅 mock 契约）

`experiments/edge-extension-probe/content.js` 第 113–149 行

```javascript
 113 |   function readPageState() {
 114 |     const kind = pageKind();
 115 |     const messages = Array.from(document.querySelectorAll(".message"));
 116 |     const byRole = { user: 0, assistant: 0, other: 0 };
 117 |     for (const m of messages) {
 118 |       const r = m.dataset.role;
 119 |       if (r === "user") byRole.user += 1;
 120 |       else if (r === "assistant") byRole.assistant += 1;
 121 |       else byRole.other += 1;
 122 |     }
 123 |     const last = messages[messages.length - 1] || null;
 124 |     const appState = document.body ? document.body.dataset.appState || null : null;
 125 |     return {
 126 |       kind,
 127 |       url: location.href,
 128 |       title: document.title,
 129 |       readyState: document.readyState,
 130 |       hasMainRegion: !!(document.querySelector("main") || document.getElementById("conversation")),
 131 |       session: kind === "mock-chat" ? mockSession() : sessionRefFromUrl(),
 132 |       appState,
 133 |       generating: appState === "generating",
 134 |       messages: {
 135 |         total: messages.length,
 136 |         byRole,
 137 |         lastId: last ? last.dataset.messageId || null : null,
 138 |         lastRole: last ? last.dataset.role || null : null,
 139 |         lastStatus: last ? last.dataset.status || null : null,
 140 |       },
 141 |       isolation: {
 142 |         isolatedWorldSeesPageVar: typeof window.__mockChat !== "undefined",
 143 |         isolatedPushStateCalls: isolatedPushStateCalls.count,
 144 |         inlineInjection,
 145 |         mainWorldProbe: { available: mainWorldProbe.available, data: mainWorldProbe.data, error: mainWorldProbe.error, at: mainWorldProbe.at, via: mainWorldProbe.via },
 146 |       },
 147 |       at: new Date().toISOString(),
 148 |     };
 149 |   }
```

## 假剪贴板：合成 paste 注入

`experiments/edge-extension-probe/content.js` 第 208–231 行

```javascript
 208 |     if (msg.type === "cf:fake_paste") {
 209 |       // "Fake clipboard": dispatch a synthetic paste event carrying a DataTransfer.
 210 |       // No OS clipboard read/write happens here.
 211 |       const ed = document.querySelector('.ProseMirror[contenteditable="true"]') || document.querySelector('[contenteditable="true"]');
 212 |       if (!ed) { sendResponse({ ok: false, error: "no contenteditable editor found" }); return true; }
 213 |       try {
 214 |         ed.focus();
 215 |         const dt = new DataTransfer();
 216 |         dt.setData("text/plain", String(msg.text || ""));
 217 |         const ev = new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true });
 218 |         const dispatched = ed.dispatchEvent(ev);
 219 |         sendResponse({
 220 |           ok: true,
 221 |           dispatched,
 222 |           defaultPrevented: ev.defaultPrevented,
 223 |           isTrusted: ev.isTrusted,
 224 |           text: ed.innerText,
 225 |           world: "isolated",
 226 |         });
 227 |       } catch (err) {
 228 |         sendResponse({ ok: false, error: String(err) });
 229 |       }
 230 |       return true;
 231 |     }
```

## Core 通知条渲染

`experiments/edge-extension-probe/content.js` 第 232–244 行

```javascript
 232 |     if (msg.type === "cf:core_notice") {
 233 |       // Core -> background -> content -> DOM. Visible proof of the reverse path.
 234 |       document.querySelectorAll("#cf-core-notice").forEach((n) => n.remove());
 235 |       const banner = document.createElement("div");
 236 |       banner.id = "cf-core-notice";
 237 |       banner.dataset.payload = JSON.stringify(msg.payload || {});
 238 |       banner.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#123;color:#cfe;border:1px solid #4f8cff;border-radius:10px;padding:8px 12px;font:12px/1.4 monospace;max-width:420px;white-space:pre-wrap";
 239 |       banner.textContent = `[ContextFlow Core] ${JSON.stringify(msg.payload)}`;
 240 |       document.body.appendChild(banner);
 241 |       scheduleReport("core-notice");
 242 |       sendResponse({ ok: true });
 243 |       return true;
 244 |     }
```

## Origin 白名单与 token 校验

`experiments/local-core-bridge/core_server.py` 第 62–73 行

```python
  62 | def origin_allowed(origin: str | None) -> bool:
  63 |     if origin is None or origin == "":
  64 |         return True  # native test clients / curl
  65 |     if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
  66 |         return True
  67 |     return False
  68 | 
  69 | 
  70 | def check_token(token: str | None) -> None:
  71 |     if token != DEV_TOKEN:
  72 |         state["counters"]["rejected"] += 1
  73 |         raise HTTPException(status_code=401, detail="invalid token")
```

## notify 入队/广播

`experiments/local-core-bridge/core_server.py` 第 121–147 行

```python
 121 | @app.post("/api/notify")
 122 | async def notify(payload: dict[str, Any], x_cf_token: str | None = Header(default=None)) -> dict[str, Any]:
 123 |     """Test harness / Worker completion -> queue or push to the extension."""
 124 |     check_token(x_cf_token)
 125 |     msg = {"type": "worker_completed", "at": datetime.now(timezone.utc).isoformat(), **payload}
 126 |     delivered = await broadcast(msg)
 127 |     if not delivered:
 128 |         state["pending"].append(msg)
 129 |         log("notification_queued", pending=len(state["pending"]))
 130 |     return {"ok": True, "delivered": delivered, "queued": not delivered, "pending": len(state["pending"])}
 131 | 
 132 | 
 133 | async def broadcast(msg: dict[str, Any]) -> int:
 134 |     dead: list[str] = []
 135 |     sent = 0
 136 |     for cid, ws in list(state["clients"].items()):
 137 |         try:
 138 |             await ws.send_text(json.dumps(msg, ensure_ascii=False))
 139 |             sent += 1
 140 |         except Exception:  # noqa: BLE001
 141 |             dead.append(cid)
 142 |     for cid in dead:
 143 |         state["clients"].pop(cid, None)
 144 |     if sent:
 145 |         state["counters"]["notifications_sent"] += sent
 146 |         log("notification_sent", clients=sent, type=msg.get("type"))
 147 |     return sent
```

## WebSocket 端点：鉴权、welcome、pending 重放

`experiments/local-core-bridge/core_server.py` 第 150–193 行

```python
 150 | @app.websocket("/ws")
 151 | async def ws_endpoint(ws: WebSocket) -> None:
 152 |     origin = ws.headers.get("origin")
 153 |     token = ws.query_params.get("token")
 154 |     if not origin_allowed(origin):
 155 |         state["counters"]["rejected"] += 1
 156 |         log("ws_rejected_origin", origin=origin)
 157 |         await ws.close(code=4403)
 158 |         return
 159 |     if token != DEV_TOKEN:
 160 |         state["counters"]["rejected"] += 1
 161 |         log("ws_rejected_token")
 162 |         await ws.close(code=4401)
 163 |         return
 164 |     await ws.accept()
 165 |     client_id = f"client-{int(time.time() * 1000)}"
 166 |     state["clients"][client_id] = ws
 167 |     log("ws_connected", client_id=client_id, origin=origin, clients=len(state["clients"]))
 168 |     await ws.send_text(json.dumps({"type": "welcome", "client_id": client_id, "pending": len(state["pending"])}))
 169 |     # Flush notifications queued while the extension was offline.
 170 |     while state["pending"]:
 171 |         msg = state["pending"].popleft()
 172 |         await ws.send_text(json.dumps({**msg, "replayed": True}, ensure_ascii=False))
 173 |         log("notification_replayed", type=msg.get("type"))
 174 |     try:
 175 |         while True:
 176 |             raw = await ws.receive_text()
 177 |             try:
 178 |                 evt = json.loads(raw)
 179 |             except json.JSONDecodeError:
 180 |                 log("invalid_json", raw=raw[:200])
 181 |                 continue
 182 |             if not isinstance(evt, dict) or "type" not in evt:
 183 |                 log("invalid_message", raw=raw[:200])
 184 |                 continue
 185 |             record_event(evt, source="ws")
 186 |             log("event_ws", type=evt.get("type"), client_id=client_id)
 187 |             if evt.get("type") == "ping":
 188 |                 await ws.send_text(json.dumps({"type": "pong", "at": datetime.now(timezone.utc).isoformat()}))
 189 |     except WebSocketDisconnect:
 190 |         pass
 191 |     finally:
 192 |         state["clients"].pop(client_id, None)
 193 |         log("ws_disconnected", client_id=client_id, clients=len(state["clients"]))
```

## 用户确认式交接：写剪贴板

`experiments/edge-extension-probe/sidepanel.js` 第 62–77 行

```javascript
  62 | async function copyReport(idx) {
  63 |   const r = HANDOFF.reports[idx];
  64 |   if (!r) return false;
  65 |   const text = formatReport(r);
  66 |   try {
  67 |     await navigator.clipboard.writeText(text);
  68 |     r.status = "copied";
  69 |     HANDOFF.lastCopied = text;
  70 |     HANDOFF.lastCopiedAt = new Date().toISOString();
  71 |     renderReports();
  72 |     return true;
  73 |   } catch (err) {
  74 |     $("log").textContent = "clipboard write failed: " + String(err);
  75 |     return false;
  76 |   }
  77 | }
```
