/* ContextFlow ChatGPT Adapter v0.6.0 — background service worker
 *
 * v0.5.0 (CF-DEV-002): adds
 *   - cf:read_snapshot forwarding to the tab whose live session_id matches,
 *     with strict routing (no fallback to the active tab) and timeouts
 *   - list_sessions / get_snapshot request handling (from WS bridge client)
 *   - request-id correlation and typed error codes
 * v0.6.0 (CF-ADAPTER-001): adds
 *   - cf:submit_envelope: paper-plane envelope -> bridge page -> external receiver
 *   - cf:resolve_endpoint: native session -> CF-CORE-001 Endpoint ID (read-only)
 *   - send_message_to_endpoint: external receiver -> strict session routing ->
 *     prepare + send + snapshot verification inside the content script
 * Read path is ALWAYS: bridge -> bg -> content script (fresh DOM read) -> bg -> bridge.
 */
"use strict";

const VERSION = "0.6.0";
const LOG = (...args) => console.log("[ContextFlow:bg]", ...args);
const WARN = (...args) => console.warn("[ContextFlow:bg]", ...args);

const state = {
  startedAt: new Date().toISOString(),
  tabs: {},        // tabId -> { tabId, at, reason, url, session_id, session_kind, message_count, generation, messages }
  lastError: null,
};

async function persist() {
  try {
    await chrome.storage.session.set({ cfTabs: state.tabs });
  } catch (err) {
    state.lastError = String(err);
    WARN("persist failed:", state.lastError);
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return false;

  if (msg.type === "cf:state") {
    const tabId = sender.tab && sender.tab.id;
    if (tabId == null) {
      sendResponse({ ok: false, error: "no sender tab" });
      return true;
    }
    const snap = msg.snapshot || {};
    state.tabs[tabId] = {
      tabId,
      at: new Date().toISOString(),
      reason: msg.reason || null,
      url: (sender.tab && sender.tab.url) || (snap.page && snap.page.url) || null,
      session_id: (snap.session && snap.session.session_id) || null,
      session_kind: (snap.session && snap.session.kind) || "unknown",
      message_count: snap.messages ? snap.messages.length : 0,
      generation: (snap.generation && snap.generation.state) || "unknown",
      messages: snap.messages || [], // last snapshot kept for debugging only
    };
    persist();
    LOG(
      `tab ${tabId} reason=${msg.reason} session=${state.tabs[tabId].session_id || "none"} ` +
      `kind=${state.tabs[tabId].session_kind} messages=${state.tabs[tabId].message_count} ` +
      `generation=${state.tabs[tabId].generation}`
    );
    sendResponse({ ok: true });
    return true;
  }

  if (msg.type === "cf:get_state") {
    sendResponse({ ok: true, data: state.tabs });
    return true;
  }

  if (msg.type === "cf:ping") {
    sendResponse({ ok: true, data: { version: VERSION, at: new Date().toISOString() } });
    return true;
  }

  return false;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (state.tabs[tabId]) {
    delete state.tabs[tabId];
    persist();
    LOG(`tab ${tabId} removed`);
  }
});

// Restore after a service-worker restart.
chrome.storage.session.get("cfTabs").then((r) => {
  if (r && r.cfTabs) {
    state.tabs = r.cfTabs;
    LOG("restored tabs:", Object.keys(state.tabs).length);
  }
}).catch((err) => WARN("restore failed:", String(err)));

// Debug hook for the service-worker DevTools console.
globalThis.__cfBg = {
  version: VERSION,
  getState: () => JSON.parse(JSON.stringify(state)),
};

LOG(`background v${VERSION} ready`);

/* ----------------- CF-DEV-002: realtime read API ------------------------- */

const READ_TIMEOUT_MS = 8000;
// Mock pages on 127.0.0.1 (any port) emulate the ChatGPT DOM for automated
// tests; they are treated as ChatGPT candidates too (localhost only, token-gated bridge).
const CHATGPT_URL_RE = /^(https:\/\/(chatgpt\.com|chat\.openai\.com)\/|http:\/\/127\.0\.0\.1(:\d+)?\/)/;

function fail(requestId, code, message) {
  return { id: requestId, type: "result", success: false, error: { code, message } };
}

function okResult(requestId, data) {
  return { id: requestId, type: "result", success: true, data };
}

// Candidate ChatGPT tabs (localhost on any port included for automated mock testing).
async function candidateTabs() {
  const tabs = await chrome.tabs.query({ url: ["https://chatgpt.com/*", "https://chat.openai.com/*", "http://127.0.0.1/*"] });
  // Keep real ChatGPT tabs out of Edge's sleeping/discarded state. A discarded
  // tab has no content script, so session probing would time out even though
  // the tab is still open.
  for (const tab of tabs) {
    if (CHATGPT_URL_RE.test(tab.url || "") && tab.autoDiscardable !== false) {
      chrome.tabs.update(tab.id, { autoDiscardable: false }).catch(() => {});
    }
  }
  return tabs;
}

// The session id is part of the ChatGPT URL (/c/<session_id>). URL matching
// lets us find the right tab even when its content script is asleep.
function sessionIdFromUrl(url) {
  if (!url) return null;
  const match = String(url).match(/\/c\/([0-9a-fA-F-]{8,})/);
  if (match) return match[1];
  try {
    const parsed = new URL(url);
    return parsed.searchParams.get("session_id") || parsed.searchParams.get("session") || null;
  } catch (err) {
    return null;
  }
}

async function currentActiveTabId() {
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    return tabs.length ? tabs[0].id : null;
  } catch (err) {
    return null;
  }
}

// Wake a sleeping tab without requiring the user to foreground it manually.
// The caller restores the previous active tab after the operation.
async function wakeTab(tabId) {
  try {
    await chrome.tabs.update(tabId, { active: true });
    await new Promise((resolve) => setTimeout(resolve, 1200));
    return true;
  } catch (err) {
    return false;
  }
}

async function probeSessionRetry(tabId, attempts = 4, delayMs = 600) {
  let last = { ok: false, error: { code: "CONTENT_SCRIPT_UNAVAILABLE", message: "no session response" } };
  for (let i = 0; i < attempts; i++) {
    last = await probeSession(tabId);
    if (last.ok) return last;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return last;
}

// Ask a tab's content script for its CURRENT session id (fresh, not cache).
function probeSession(tabId, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ ok: false, error: { code: "READ_TIMEOUT", message: "session probe timeout" } }), timeoutMs);
    try {
      chrome.tabs.sendMessage(tabId, { type: "cf:read_session" }, (resp) => {
        clearTimeout(timer);
        void chrome.runtime.lastError;
        if (!resp || !resp.ok) resolve({ ok: false, error: { code: "CONTENT_SCRIPT_UNAVAILABLE", message: "no session response" } });
        else resolve({ ok: true, session_id: resp.session_id, kind: resp.kind });
      });
    } catch (err) {
      clearTimeout(timer);
      resolve({ ok: false, error: { code: "CONTENT_SCRIPT_UNAVAILABLE", message: String(err) } });
    }
  });
}

// Full realtime read from a specific tab, with session pre/post check + timeout.
function readSnapshotFromTab(tabId, expectedSessionId, requestId) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fail(requestId, "READ_TIMEOUT", "content script did not answer within " + READ_TIMEOUT_MS + "ms")), READ_TIMEOUT_MS);
    try {
      chrome.tabs.sendMessage(
        tabId,
        { type: "cf:read_snapshot", request_id: requestId, expected_session_id: expectedSessionId },
        (resp) => {
          clearTimeout(timer);
          void chrome.runtime.lastError;
          if (!resp) {
            resolve(fail(requestId, "CONTENT_SCRIPT_UNAVAILABLE", "no response from content script"));
          } else if (!resp.ok) {
            resolve({ id: requestId, type: "result", success: false, error: resp.error || { code: "INTERNAL_ERROR", message: "unknown content error" } });
          } else {
            resolve(okResult(requestId, { tab_id: tabId, captured_at: resp.snapshot && resp.snapshot.at, snapshot: resp.snapshot }));
          }
        }
      );
    } catch (err) {
      clearTimeout(timer);
      resolve(fail(requestId, "INTERNAL_ERROR", String(err)));
    }
  });
}

// CF-DEV-003: lightweight generation-state read (no message parsing).
function readGenerationFromTab(tabId, expectedSessionId, requestId) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fail(requestId, "READ_TIMEOUT", "content script did not answer within " + READ_TIMEOUT_MS + "ms")), READ_TIMEOUT_MS);
    try {
      chrome.tabs.sendMessage(
        tabId,
        { type: "cf:read_generation", request_id: requestId, expected_session_id: expectedSessionId },
        (resp) => {
          clearTimeout(timer);
          void chrome.runtime.lastError;
          if (!resp) resolve(fail(requestId, "CONTENT_SCRIPT_UNAVAILABLE", "no response from content script"));
          else if (!resp.ok) resolve({ id: requestId, type: "result", success: false, error: resp.error || { code: "INTERNAL_ERROR", message: "unknown content error" } });
          else resolve(okResult(requestId, { tab_id: tabId, ...resp.data }));
        }
      );
    } catch (err) {
      clearTimeout(timer);
      resolve(fail(requestId, "INTERNAL_ERROR", String(err)));
    }
  });
}

async function handleListSessions(requestId) {
  try {
    const tabs = await candidateTabs();
    const sessions = [];
    for (const tab of tabs) {
      const probe = await probeSession(tab.id);
      sessions.push({
        tab_id: tab.id,
        session_id: probe.ok ? probe.session_id : null,
        kind: probe.ok ? probe.kind : null,
        url: tab.url,
        ready: probe.ok,
        error: probe.ok ? undefined : probe.error,
      });
    }
    return okResult(requestId, { sessions });
  } catch (err) {
    return fail(requestId, "INTERNAL_ERROR", String(err));
  }
}

// Shared strict routing: session_id -> unique tab (or explicit tab_id).
// Returns { tabId, session_id } or { error: <result envelope> }.
async function resolveTargetTab(requestId, params) {
  if (!params || typeof params !== "object") return { error: fail(requestId, "INVALID_REQUEST", "missing params") };
  const { session_id, tab_id } = params;
  if (!session_id && tab_id == null) {
    return { error: fail(requestId, "INVALID_REQUEST", "session_id or tab_id is required") };
  }
  try {
    if (tab_id != null) {
      // Explicit tab wins; still verify the tab currently shows that session.
      const tab = await chrome.tabs.get(Number(tab_id)).catch(() => null);
      if (!tab || !CHATGPT_URL_RE.test(tab.url || "")) {
        return { error: fail(requestId, "SESSION_NOT_FOUND", "tab " + tab_id + " is not a ChatGPT page") };
      }
      let probe = await probeSession(tab.id);
      let woke = false;
      if (!probe.ok) {
        const previousActiveTabId = await currentActiveTabId();
        if (await wakeTab(tab.id)) {
          woke = true;
          probe = await probeSessionRetry(tab.id);
          if (!probe.ok) {
            if (previousActiveTabId != null) chrome.tabs.update(previousActiveTabId, { active: true }).catch(() => {});
            return { error: fail(requestId, probe.error.code, probe.error.message) };
          }
          if (session_id && probe.session_id !== session_id) {
            if (previousActiveTabId != null) chrome.tabs.update(previousActiveTabId, { active: true }).catch(() => {});
            return { error: fail(requestId, "SESSION_NOT_FOUND", "tab " + tab_id + " now shows session " + probe.session_id + ", not " + session_id) };
          }
          return { tabId: tab.id, session_id: session_id || probe.session_id, woke: true, previousActiveTabId };
        }
        return { error: fail(requestId, probe.error.code, probe.error.message) };
      }
      if (session_id && probe.session_id !== session_id) {
        return { error: fail(requestId, "SESSION_NOT_FOUND", "tab " + tab_id + " now shows session " + probe.session_id + ", not " + session_id) };
      }
      return { tabId: tab.id, session_id: session_id || probe.session_id, woke };
    }
    // Route strictly by session_id across candidate tabs. No active-tab fallback.
    const tabs = await candidateTabs();
    const matches = [];
    const urlMatches = [];
    for (const tab of tabs) {
      const probe = await probeSession(tab.id);
      if (probe.ok && probe.session_id === session_id) matches.push(tab.id);
      else if (sessionIdFromUrl(tab.url) === session_id) urlMatches.push(tab.id);
    }
    if (matches.length > 1) return { error: fail(requestId, "AMBIGUOUS_SESSION", "session " + session_id + " is open in tabs " + matches.join(",") + " — pass tab_id") };
    if (matches.length === 1) return { tabId: matches[0], session_id, woke: false };

    // No responsive content script. The tab may be asleep/discarded; find it by
    // URL, wake it, then probe again. The caller restores the previous tab.
    if (urlMatches.length > 1) {
      return { error: fail(requestId, "AMBIGUOUS_SESSION", "session " + session_id + " is open in tabs " + urlMatches.join(",") + " — pass tab_id") };
    }
    if (urlMatches.length === 1) {
      const tabId = urlMatches[0];
      const previousActiveTabId = await currentActiveTabId();
      if (await wakeTab(tabId)) {
        const probe = await probeSessionRetry(tabId);
        if (probe.ok && probe.session_id === session_id) {
          return { tabId, session_id, woke: true, previousActiveTabId };
        }
        if (previousActiveTabId != null) chrome.tabs.update(previousActiveTabId, { active: true }).catch(() => {});
        return { error: fail(requestId, "CONTENT_SCRIPT_UNAVAILABLE", "tab " + tabId + " is asleep and did not wake; reload it once") };
      }
    }
    return { error: fail(requestId, "SESSION_NOT_FOUND", "no open ChatGPT tab shows session " + session_id) };
  } catch (err) {
    return { error: fail(requestId, "INTERNAL_ERROR", String(err)) };
  }
}

async function handleGetSnapshot(requestId, params) {
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return readSnapshotFromTab(target.tabId, target.session_id || null, requestId);
}

async function handleGetGeneration(requestId, params) {
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return readGenerationFromTab(target.tabId, target.session_id || null, requestId);
}

// Generic content-script call with timeout (CF-DEV-004).
function sendToContent(tabId, payload, requestId, timeoutMs = READ_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fail(requestId, "READ_TIMEOUT", "content script did not answer within " + timeoutMs + "ms")), timeoutMs);
    try {
      chrome.tabs.sendMessage(tabId, { ...payload, request_id: requestId }, (resp) => {
        clearTimeout(timer);
        void chrome.runtime.lastError;
        if (!resp) resolve(fail(requestId, "CONTENT_SCRIPT_UNAVAILABLE", "no response from content script"));
        else if (!resp.ok) resolve({ id: requestId, type: "result", success: false, error: resp.error || { code: "INTERNAL_ERROR", message: "unknown content error" }, data: resp.data });
        else resolve(okResult(requestId, { tab_id: tabId, ...(resp.data || {}) }));
      });
    } catch (err) {
      clearTimeout(timer);
      resolve(fail(requestId, "INTERNAL_ERROR", String(err)));
    }
  });
}

// Send to a resolved target and, if we had to wake a sleeping tab, restore the
// tab the user was on before the operation.
async function sendToTargetAndRestore(target, payload, requestId, timeoutMs) {
  try {
    return await sendToContent(target.tabId, payload, requestId, timeoutMs);
  } finally {
    if (target.woke && target.previousActiveTabId != null) {
      try { await chrome.tabs.update(target.previousActiveTabId, { active: true }); } catch (err) { /* ignore */ }
    }
  }
}

/* ----------------- CF-ADAPTER-001: bridge page relay --------------------- */

// The bridge page is a normal 127.0.0.1 tab that holds the WebSocket. The
// extension never opens sockets itself; it only talks to that page's relay
// content script (bridge_relay.js).
//
// After an extension reload, old bridge pages keep their WebSocket but lose the
// relay content script. Probe every candidate and use the first one that
// actually answers, so a stale page can never swallow a submission.
function pingBridgeTab(tabId, timeoutMs = 1200) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    try {
      chrome.tabs.sendMessage(tabId, { type: "cf:bridge_ping" }, (resp) => {
        clearTimeout(timer);
        void chrome.runtime.lastError;
        resolve(!!(resp && resp.ok));
      });
    } catch (err) {
      clearTimeout(timer);
      resolve(false);
    }
  });
}

async function findBridgeTab() {
  try {
    const tabs = await chrome.tabs.query({ url: ["http://127.0.0.1/*"] });
    const candidates = tabs.filter((tab) => (tab.url || "").includes("/bridge_client.html") && tab.id != null);
    for (const tab of candidates) {
      if (await pingBridgeTab(tab.id)) return tab;
    }
    return null;
  } catch (err) {
    WARN("bridge tab query failed:", String(err));
    return null;
  }
}

// Ask the bridge page to relay one payload. Returns a plain result object:
//   { ok, status, error?, ... }   (status: accepted | failed | unknown)
function callBridgePage(payload, timeoutMs) {
  return new Promise((resolve) => {
    findBridgeTab().then((tab) => {
      if (!tab || tab.id == null) {
        resolve({ ok: false, status: "failed", error: { code: "BRIDGE_UNAVAILABLE", message: "bridge client page is not open" } });
        return;
      }
      const timer = setTimeout(() => resolve({ ok: false, status: "unknown", error: { code: "BRIDGE_TIMEOUT", message: "bridge page did not answer within " + timeoutMs + "ms" } }), timeoutMs);
      try {
        chrome.tabs.sendMessage(tab.id, payload, (resp) => {
          clearTimeout(timer);
          void chrome.runtime.lastError;
          if (!resp) {
            resolve({ ok: false, status: "failed", error: { code: "CONTENT_SCRIPT_UNAVAILABLE", message: "bridge relay content script did not answer" } });
          } else {
            resolve(resp);
          }
        });
      } catch (err) {
        clearTimeout(timer);
        resolve({ ok: false, status: "failed", error: { code: "INTERNAL_ERROR", message: String(err) } });
      }
    });
  });
}

async function handleSubmitEnvelope(envelope) {
  if (!envelope || typeof envelope !== "object") {
    return { ok: false, status: "failed", error: { code: "INVALID_REQUEST", message: "envelope is required" } };
  }
  return callBridgePage({ type: "cf:bridge_submit", envelope }, 22000);
}

async function handleResolveEndpoint(sessionId) {
  if (typeof sessionId !== "string" || !sessionId) {
    return { ok: false, error: { code: "INVALID_REQUEST", message: "session_id is required" } };
  }
  const result = await callBridgePage({ type: "cf:bridge_endpoint_lookup", session_id: sessionId }, 8000);
  if (!result || result.ok !== true) {
    return {
      ok: false,
      error: (result && result.error) || { code: "BRIDGE_UNAVAILABLE", message: "endpoint lookup failed" },
    };
  }
  return {
    ok: true,
    endpoint_id: result.endpoint_id || null,
    found: result.found === true,
    native_session_id: result.native_session_id || sessionId,
  };
}

// CF-ADAPTER-001 reverse send: external receiver -> strict session routing ->
// content script (prepare + send + snapshot verification).
async function handleSendMessageToEndpoint(requestId, params) {
  if (!params || typeof params.text !== "string" || !params.text.length) {
    return fail(requestId, "INVALID_REQUEST", "text is required and must be non-empty");
  }
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return sendToTargetAndRestore(target, {
    type: "cf:send_to_endpoint",
    expected_session_id: target.session_id || null,
    text: params.text,
    force: params.force === true,
  }, requestId, 30000);
}

// Dev/debug: inspect the real Assistant action bar structure and injection state.
async function handleDebugActionBars(requestId, params) {
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return sendToContent(target.tabId, {
    type: "cf:debug_action_bars",
    expected_session_id: target.session_id || null,
  }, requestId);
}

// Dev/test: click the injected paper-plane button for one native message id.
// This exercises the exact same DOM click handler as a user click.
async function handleClickSubmitButton(requestId, params) {
  if (!params || typeof params.message_id !== "string" || !params.message_id) {
    return fail(requestId, "INVALID_REQUEST", "message_id is required");
  }
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return sendToContent(target.tabId, {
    type: "cf:click_submit_button",
    expected_session_id: target.session_id || null,
    message_id: params.message_id,
  }, requestId);
}

// Dev utility: reload one specific tab (avoids reloading every ChatGPT tab).
async function handleReloadTab(requestId, params) {
  const tabId = params && params.tab_id;
  if (tabId == null) return fail(requestId, "INVALID_REQUEST", "tab_id is required");
  try {
    await chrome.tabs.reload(Number(tabId));
    return okResult(requestId, { reloaded: Number(tabId) });
  } catch (err) {
    return fail(requestId, "INTERNAL_ERROR", String(err));
  }
}

async function handleGetComposer(requestId, params) {
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return sendToContent(target.tabId, { type: "cf:read_composer", expected_session_id: target.session_id || null }, requestId);
}

async function handlePrepareMessage(requestId, params) {
  if (!params || typeof params.text !== "string" || !params.text.length) {
    return fail(requestId, "INVALID_REQUEST", "text is required and must be non-empty");
  }
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return sendToTargetAndRestore(target, {
    type: "cf:prepare_message",
    expected_session_id: target.session_id || null,
    text: params.text,
    force: params.force === true,
  }, requestId);
}

async function handleClearComposer(requestId, params) {
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return sendToContent(target.tabId, { type: "cf:clear_composer", expected_session_id: target.session_id || null }, requestId);
}

// CF-DEV-004 experiment C: send whatever is in the composer. Non-idempotent —
// callers must not retry blindly on SEND_UNVERIFIED.
async function handleSendMessage(requestId, params) {
  const target = await resolveTargetTab(requestId, params);
  if (target.error) return target.error;
  return sendToTargetAndRestore(target, { type: "cf:send_message", expected_session_id: target.session_id || null }, requestId, 25000);
}

// Dev utility: reload this unpacked extension from disk (picks up new code).
async function handleReloadExtension(requestId) {
  setTimeout(() => { try { chrome.runtime.reload(); } catch (e) { /* ignore */ } }, 150);
  return okResult(requestId, { reloading: true });
}

// Dev utility: reload all candidate ChatGPT tabs so the new content script loads.
async function handleReloadTabs(requestId) {
  try {
    const tabs = await candidateTabs();
    const ids = [];
    for (const tab of tabs) {
      try { await chrome.tabs.reload(tab.id); ids.push(tab.id); } catch (e) { /* skip */ }
    }
    return okResult(requestId, { reloaded: ids });
  } catch (err) {
    return fail(requestId, "INTERNAL_ERROR", String(err));
  }
}

// Handle a request arriving from the WS bridge (via runtime message relay).
async function handleBridgeRequest(msg) {
  return new Promise((resolve) => {
    const id = msg && msg.id;
    if (!id || typeof id !== "string") {
      resolve({ type: "result", success: false, error: { code: "INVALID_REQUEST", message: "missing id" } });
      return;
    }
    if (msg.type === "list_sessions") { handleListSessions(id).then(resolve); return; }
    if (msg.type === "get_snapshot") { handleGetSnapshot(id, msg).then(resolve); return; }
    if (msg.type === "get_generation") { handleGetGeneration(id, msg).then(resolve); return; }
    if (msg.type === "get_composer") { handleGetComposer(id, msg).then(resolve); return; }
    if (msg.type === "prepare_message") { handlePrepareMessage(id, msg).then(resolve); return; }
    if (msg.type === "clear_composer") { handleClearComposer(id, msg).then(resolve); return; }
    if (msg.type === "send_message") { handleSendMessage(id, msg).then(resolve); return; }
    if (msg.type === "send_message_to_endpoint") { handleSendMessageToEndpoint(id, msg).then(resolve); return; }
    if (msg.type === "debug_action_bars") { handleDebugActionBars(id, msg).then(resolve); return; }
    if (msg.type === "click_submit_button") { handleClickSubmitButton(id, msg).then(resolve); return; }
    if (msg.type === "reload_tab") { handleReloadTab(id, msg).then(resolve); return; }
    if (msg.type === "reload_extension") { handleReloadExtension(id).then(resolve); return; }
    if (msg.type === "reload_tabs") { handleReloadTabs(id).then(resolve); return; }
    resolve(fail(id, "INVALID_REQUEST", "unknown type: " + msg.type));
  });
}

// Expose to the WS bridge content script (127.0.0.1 pages) via runtime messages.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "cf:bridge_request") {
    handleBridgeRequest(msg.request).then(sendResponse);
    return true; // async
  }
  return false;
});

// CF-ADAPTER-001: content script (ChatGPT tab) -> background -> bridge page.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "cf:submit_envelope") {
    handleSubmitEnvelope(msg.envelope).then(sendResponse);
    return true; // async
  }
  if (msg && msg.type === "cf:resolve_endpoint") {
    handleResolveEndpoint(msg.session_id).then(sendResponse);
    return true; // async
  }
  return false;
});
