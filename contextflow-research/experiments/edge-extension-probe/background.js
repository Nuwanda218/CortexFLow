/* ContextFlow Edge Probe - background service worker (MV3).
 *
 * Responsibilities
 *  1. Observe tab-level navigation: tabs.onUpdated, webNavigation.onCommitted,
 *     onHistoryStateUpdated, onReferenceFragmentUpdated.
 *  2. Receive page-state reports from content.js and keep the latest per tab.
 *  3. Bridge to the local ContextFlow Core over WebSocket (ws://127.0.0.1:8765/ws),
 *     with reconnect + alarm keepalive, and route Core commands to the right tab.
 *  4. Answer popup / side-panel queries.
 *
 * Test hook: globalThis.__cf exposes read-only-ish helpers so an automated
 * harness can drive the worker through CDP/Playwright without the popup.
 */

const CORE_WS_URL = "ws://127.0.0.1:8765/ws";
const CORE_TOKEN = "dev-token-cf-exp-001"; // experiment-only shared secret
const MAX_EVENTS = 200;
const INSTANCE_ID = (globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now());

const state = {
  lastPageStateByTab: {},   // tabId -> page state reported by content.js
  navEvents: [],            // ring buffer of navigation events
  ws: null,
  wsStatus: "disconnected", // disconnected | connecting | connected
  wsAttempts: 0,
  lastError: null,
  coreCommands: [],         // commands received from Core (for inspection)
};

function nowIso() { return new Date().toISOString(); }

function recordNav(event) {
  const entry = { at: nowIso(), ...event };
  state.navEvents.push(entry);
  if (state.navEvents.length > MAX_EVENTS) state.navEvents.splice(0, state.navEvents.length - MAX_EVENTS);
  chrome.storage.session.set({ navEvents: state.navEvents }).catch(() => {});
  broadcast({ type: "cf:nav_event", event: entry });
  sendToCore({ type: "nav_event", source: "edge_extension", ...entry });
  return entry;
}

function broadcast(msg) {
  // Extension pages (popup / side panel) may not be open; ignore errors.
  chrome.runtime.sendMessage(msg).catch(() => {});
}

/* ------------------------------- Core bridge ------------------------------ */

function setWsStatus(s) {
  state.wsStatus = s;
  chrome.action.setBadgeText({ text: s === "connected" ? "ON" : s === "connecting" ? "…" : "" });
  chrome.action.setBadgeBackgroundColor({ color: s === "connected" ? "#1f7a4d" : "#8a5a00" });
  broadcast({ type: "cf:ws_status", status: s });
}

function connectCore() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) return;
  setWsStatus("connecting");
  state.wsAttempts += 1;
  let ws;
  try {
    ws = new WebSocket(`${CORE_WS_URL}?token=${encodeURIComponent(CORE_TOKEN)}`);
  } catch (err) {
    state.lastError = String(err);
    setWsStatus("disconnected");
    return;
  }
  state.ws = ws;
  ws.addEventListener("open", () => {
    setWsStatus("connected");
    state.lastError = null;
    ws.send(JSON.stringify({ type: "hello", source: "edge_extension", version: chrome.runtime.getManifest().version }));
  });
  ws.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    state.coreCommands.push({ at: nowIso(), msg });
    if (state.coreCommands.length > MAX_EVENTS) state.coreCommands.shift();
    handleCoreCommand(msg);
  });
  ws.addEventListener("close", () => {
    setWsStatus("disconnected");
    scheduleReconnect();
  });
  ws.addEventListener("error", () => {
    state.lastError = "websocket error";
    // close event follows; reconnect is scheduled there.
  });
}

let reconnectTimer = null;
function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(state.wsAttempts, 5)));
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connectCore(); }, delay);
}

function sendToCore(msg) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    try { state.ws.send(JSON.stringify(msg)); return true; } catch (_) { return false; }
  }
  return false;
}

async function handleCoreCommand(msg) {
  if (!msg || typeof msg.type !== "string") return;
  if (msg.type === "worker_completed" || msg.type === "notify") {
    // Route to the tab bound to the target session, else the active tab.
    const tabs = await chrome.tabs.query({});
    let target = null;
    if (msg.session_ref) {
      target = tabs.find((t) => {
        const ps = state.lastPageStateByTab[t.id];
        return ps && ps.session_ref === msg.session_ref;
      });
    }
    if (!target) target = tabs.find((t) => t.active);
    if (target) {
      chrome.tabs.sendMessage(target.id, { type: "cf:core_notice", payload: msg }).catch(() => {});
    }
    broadcast({ type: "cf:core_notice", payload: msg });
  } else if (msg.type === "ping") {
    sendToCore({ type: "pong", at: nowIso() });
  }
}

/* ----------------------------- Navigation events -------------------------- */

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url) {
    recordNav({ kind: "tabs.onUpdated.url", tabId, url: changeInfo.url, title: tab.title || "" });
  }
  if (changeInfo.status === "complete") {
    recordNav({ kind: "tabs.onUpdated.status_complete", tabId, url: tab.url || "", title: tab.title || "" });
  }
});

chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId !== 0) return;
  recordNav({ kind: "webNavigation.onCommitted", tabId: d.tabId, url: d.url, transitionType: d.transitionType, transitionQualifiers: d.transitionQualifiers });
});

chrome.webNavigation.onHistoryStateUpdated.addListener((d) => {
  if (d.frameId !== 0) return;
  recordNav({ kind: "webNavigation.onHistoryStateUpdated", tabId: d.tabId, url: d.url });
});

chrome.webNavigation.onReferenceFragmentUpdated.addListener((d) => {
  if (d.frameId !== 0) return;
  recordNav({ kind: "webNavigation.onReferenceFragmentUpdated", tabId: d.tabId, url: d.url });
});

chrome.tabs.onActivated.addListener((info) => {
  recordNav({ kind: "tabs.onActivated", tabId: info.tabId, windowId: info.windowId });
});

/* ------------------------------ Runtime messages -------------------------- */

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.type !== "string") return false;

  if (msg.type === "cf:page_state") {
    const tabId = sender.tab ? sender.tab.id : null;
    if (tabId != null) state.lastPageStateByTab[tabId] = { ...msg.payload, tabId, at: nowIso() };
    chrome.storage.session.set({ lastPageStateByTab: state.lastPageStateByTab }).catch(() => {});
    sendToCore({ type: "page_state", source: "edge_extension", tabId, ...msg.payload });
    broadcast({ type: "cf:page_state", tabId, payload: msg.payload });
    sendResponse({ ok: true });
    return true;
  }

  if (msg.type === "cf:get_state") {
    sendResponse({
      ok: true,
      wsStatus: state.wsStatus,
      wsAttempts: state.wsAttempts,
      lastError: state.lastError,
      navEvents: state.navEvents.slice(-40),
      coreCommands: state.coreCommands.slice(-20),
      tabs: state.lastPageStateByTab,
    });
    return true;
  }

  if (msg.type === "cf_ping_content") {
    const tabId = msg.tabId;
    chrome.tabs.sendMessage(tabId, { type: "cf:ping", nonce: msg.nonce || nowIso() })
      .then((r) => sendResponse({ ok: true, reply: r }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (msg.type === "cf:main_world_probe") {
    // Official MV3 way to run code in the page's MAIN world: chrome.scripting.
    // It is not subject to the page CSP (unlike an injected inline <script>).
    const tabId = msg.tabId != null ? msg.tabId : (sender.tab ? sender.tab.id : null);
    if (tabId == null) { sendResponse({ ok: false, error: "no tabId" }); return true; }
    chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: () => {
        try {
          const mc = window.__mockChat;
          if (!mc) return { available: false, error: "window.__mockChat is undefined in MAIN world" };
          return {
            available: true,
            data: { version: mc.version, sessionId: mc.sessionId(), state: mc.state(), messageCount: mc.messageCount(), pushStateCalls: mc.pushStateCalls },
            error: null,
          };
        } catch (err) {
          return { available: false, error: String(err) };
        }
      },
    }).then((results) => sendResponse({ ok: true, result: results && results[0] ? results[0].result : null }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (msg.type === "cf_connect_core") {
    connectCore();
    sendResponse({ ok: true, wsStatus: state.wsStatus });
    return true;
  }

  if (msg.type === "cf_send_core") {
    const sent = sendToCore(msg.payload);
    sendResponse({ ok: sent });
    return true;
  }

  return false;
});

/* --------------------------------- Lifecycle ------------------------------ */

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("cf-keepalive", { periodInMinutes: 0.5 });
  connectCore();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("cf-keepalive", { periodInMinutes: 0.5 });
  connectCore();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "cf-keepalive") {
    // Touch storage (resets idle timer) and make sure the bridge is up.
    chrome.storage.session.set({ lastKeepalive: nowIso() }).catch(() => {});
    if (state.wsStatus !== "connected") connectCore();
  }
});

// Side panel: open when the toolbar icon is clicked with a modifier is not
// possible; expose it through the popup button instead.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "cf_open_side_panel" && chrome.sidePanel && chrome.sidePanel.open) {
    chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
      if (tab) chrome.sidePanel.open({ tabId: tab.id }).catch(() => {});
    });
  }
  return false;
});

// Kick the connection as soon as the worker starts (not only on install).
connectCore();

/* ------------------------------- Test hook -------------------------------- */
globalThis.__cf = {
  getState: () => JSON.parse(JSON.stringify({
    instanceId: INSTANCE_ID,
    wsStatus: state.wsStatus,
    wsAttempts: state.wsAttempts,
    lastError: state.lastError,
    navEvents: state.navEvents,
    coreCommands: state.coreCommands,
    tabs: state.lastPageStateByTab,
  })),
  connectCore,
  sendToCore,
  recordNav,
  manifest: () => chrome.runtime.getManifest(),
};
