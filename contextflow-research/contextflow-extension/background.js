/* ContextFlow ChatGPT Adapter v0.4.0 — background service worker
 *
 * v0.4.0 (CF-DEV-002): adds
 *   - cf:read_snapshot forwarding to the tab whose live session_id matches,
 *     with strict routing (no fallback to the active tab) and timeouts
 *   - list_sessions / get_snapshot request handling (from WS bridge client)
 *   - request-id correlation and typed error codes
 * Read path is ALWAYS: bridge -> bg -> content script (fresh DOM read) -> bg -> bridge.
 */
"use strict";

const VERSION = "0.4.0";
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
  return chrome.tabs.query({ url: ["https://chatgpt.com/*", "https://chat.openai.com/*", "http://127.0.0.1/*"] });
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
      const probe = await probeSession(tab.id);
      if (!probe.ok) return { error: fail(requestId, probe.error.code, probe.error.message) };
      if (session_id && probe.session_id !== session_id) {
        return { error: fail(requestId, "SESSION_NOT_FOUND", "tab " + tab_id + " now shows session " + probe.session_id + ", not " + session_id) };
      }
      return { tabId: tab.id, session_id: session_id || probe.session_id };
    }
    // Route strictly by session_id across candidate tabs. No active-tab fallback.
    const tabs = await candidateTabs();
    const matches = [];
    for (const tab of tabs) {
      const probe = await probeSession(tab.id);
      if (probe.ok && probe.session_id === session_id) matches.push(tab.id);
    }
    if (matches.length === 0) return { error: fail(requestId, "SESSION_NOT_FOUND", "no open ChatGPT tab shows session " + session_id) };
    if (matches.length > 1) return { error: fail(requestId, "AMBIGUOUS_SESSION", "session " + session_id + " is open in tabs " + matches.join(",") + " — pass tab_id") };
    return { tabId: matches[0], session_id };
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
  return sendToContent(target.tabId, {
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
