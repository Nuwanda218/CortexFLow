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

importScripts("runtime_config.js", "dispatch_protocol.js");

const VERSION = "0.7.0";
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
    scheduleSupervisorScan(tabId, snap);
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
  // Legacy read-only endpoint lookup remains for reverse-send compatibility.
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

/* ----------------- C-01: Supervisor binding control ---------------------- */

const SUPERVISOR_BINDING_KEY = "cfSupervisorBindingV1";
const SUPERVISOR_SUBMISSIONS_KEY = "cfSupervisorSubmissionsV1";
const LEGACY_SUBMISSIONS_KEY = "cfSubmissionRecords";
const SUPERVISOR_SCAN_KEY = "cfSupervisorScanStateV1";
const SUPERVISOR_RECORD_LIMIT = 500;
const SUPERVISOR_WORKER_INSTANCE_KEY = "cfSupervisorWorkerInstanceV1";
const supervisorWorkerInstanceId = `worker_${crypto.randomUUID ? crypto.randomUUID() : Date.now() + "_" + Math.random()}`;
const SUPERVISOR_SUBMISSION_TYPES = new Set([
  "cf:supervisor_submit", "cf:supervisor_submission_state",
]);
const supervisorScanTails = new Map();
const supervisorSubmissionTails = new Map();
let supervisorRecordTail = Promise.resolve();
const fullOverrideConfirmations = new Map();
const SUPERVISOR_MANAGEMENT_TYPES = new Set([
  "cf:supervisor_get_state", "cf:supervisor_bind", "cf:supervisor_pause",
  "cf:supervisor_resume", "cf:supervisor_unbind",
]);
let supervisorStateRequest = null;
let supervisorMutationTail = Promise.resolve();

function isTrustedExtensionPage(sender) {
  const root = chrome.runtime.getURL("");
  return !!sender && sender.id === chrome.runtime.id && !sender.tab &&
    typeof sender.url === "string" && sender.url.startsWith(root);
}

function supervisorError(code, message, details) {
  const error = { code, message };
  if (details && typeof details === "object") error.details = details;
  return { ok: false, error };
}

function validControlText(value, pattern) {
  return typeof value === "string" && value.length > 0 && value.length <= 256 &&
    value === value.trim() && !/[\p{C}]/u.test(value) &&
    (!pattern || pattern.test(value));
}

function validUtc(value) {
  if (!validControlText(value) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)) return false;
  const epoch = Date.parse(value);
  return Number.isFinite(epoch) && new Date(epoch).toISOString() === value;
}

function validOptionalDisplayName(value) {
  return value === null || (typeof value === "string" && value.length > 0 && value.length <= 200 &&
    value === value.trim() && !/[\p{C}]/u.test(value));
}

function bindingFields(version) {
  const common = ["schema_version", "native_session_id", "endpoint_id", "session_kind", "state", "bound_at", "updated_at", "anchor"];
  return version === 2 ? [...common, "project_name", "conversation_name"] : common;
}

function validBinding(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (![1, 2].includes(value.schema_version)) return null;
  const expected = bindingFields(value.schema_version);
  if (Object.keys(value).sort().join("|") !== expected.sort().join("|")) return null;
  if (!["active", "paused"].includes(value.state)) return null;
  if (!["chat", "project"].includes(value.session_kind)) return null;
  if (!validControlText(value.native_session_id) ||
      !validControlText(value.endpoint_id, /^cfep_[A-Za-z0-9_-]{8,}$/) ||
      (value.schema_version === 2 && (!validOptionalDisplayName(value.project_name) ||
        !validOptionalDisplayName(value.conversation_name))) ||
      !validUtc(value.bound_at) || !validUtc(value.updated_at)) return null;
  const anchor = value.anchor;
  if (!anchor || typeof anchor !== "object" || Array.isArray(anchor) ||
      Object.keys(anchor).sort().join("|") !== "message_id|reason|updated_at" ||
      !["bind", "resume", "observed"].includes(anchor.reason) ||
      (anchor.message_id !== null && !validControlText(anchor.message_id)) ||
      !validUtc(anchor.updated_at)) return null;
  return value;
}

async function readSupervisorBindingState() {
  const data = await chrome.storage.local.get(SUPERVISOR_BINDING_KEY);
  if (!data || !(SUPERVISOR_BINDING_KEY in data)) return { kind: "absent", binding: null };
  const binding = validBinding(data[SUPERVISOR_BINDING_KEY]);
  return binding ? { kind: "valid", binding } : { kind: "invalid", binding: null };
}

async function readSupervisorBinding() {
  const state = await readSupervisorBindingState();
  if (state.kind === "invalid") throw Object.assign(new Error("stored Supervisor binding is invalid"), { code: "BINDING_STORAGE_INVALID" });
  return state.binding;
}

async function writeSupervisorBinding(binding) {
  if (!validBinding(binding)) throw new Error("invalid Supervisor binding document");
  await chrome.storage.local.set({ [SUPERVISOR_BINDING_KEY]: binding });
}

function configuredMockOrigins() {
  const config = globalThis.ContextFlowRuntimeConfig;
  const values = config && Array.isArray(config.allowed_mock_origins) ? config.allowed_mock_origins : [];
  return new Set(values.filter((value) => typeof value === "string"));
}

function allowedSupervisorTab(tab) {
  if (!tab || tab.id == null || typeof tab.url !== "string") return false;
  try {
    const url = new URL(tab.url);
    if (url.username || url.password) return false;
    if (url.origin === "https://chatgpt.com" || url.origin === "https://chat.openai.com") return true;
    return configuredMockOrigins().has(url.origin) && !url.pathname.includes("/bridge_client.html");
  } catch (_err) {
    return false;
  }
}

async function activeSupervisorTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs.length && allowedSupervisorTab(tabs[0]) ? tabs[0] : null;
}

function probeSupervisorTab(tab, expectedSessionId) {
  return new Promise((resolve) => {
    const guard = setTimeout(() => resolve(supervisorError("READ_TIMEOUT", "页面身份探测超时")), READ_TIMEOUT_MS);
    try {
      chrome.tabs.sendMessage(tab.id, {
        type: "cf:supervisor_probe",
        expected_session_id: expectedSessionId || null,
      }, (response) => {
        clearTimeout(guard);
        void chrome.runtime.lastError;
        if (!response || response.ok !== true) {
          resolve(response && response.error ? { ok: false, error: response.error } :
            supervisorError("CONTENT_SCRIPT_UNAVAILABLE", "ChatGPT 页面尚未连接扩展"));
          return;
        }
        resolve(response);
      });
    } catch (_err) {
      clearTimeout(guard);
      resolve(supervisorError("CONTENT_SCRIPT_UNAVAILABLE", "ChatGPT 页面尚未连接扩展"));
    }
  });
}

async function currentSupervisorProbe(expectedSessionId) {
  const tab = await activeSupervisorTab();
  if (!tab) return supervisorError("PAGE_UNAVAILABLE", "当前标签页不是可绑定的 ChatGPT 会话");
  const result = await probeSupervisorTab(tab, expectedSessionId);
  if (!result.ok) return result;
  const data = result.data;
  const session = data && data.session;
  if (!session || typeof session.session_id !== "string" || !session.session_id || !["chat", "project"].includes(session.kind)) {
    return supervisorError("SESSION_ID_UNAVAILABLE", "当前会话尚未建立稳定 Session ID");
  }
  const assistants = Array.isArray(data.assistants) ? data.assistants : [];
  if (assistants.length && (!data.latest_assistant_id || typeof data.latest_assistant_id !== "string")) {
    return supervisorError("MESSAGE_ID_UNAVAILABLE", "最新 Assistant 消息缺少稳定 ID");
  }
  return { ok: true, data };
}

async function callSupervisorControl(action, params) {
  return callBridgePage({ type: "cf:bridge_control", action, params }, 22000);
}

async function coreAvailability() {
  const bridgeTab = await findBridgeTab();
  if (!bridgeTab) return { bridge_status: "unavailable", core_status: "unavailable" };
  const result = await callSupervisorControl("ping_core", {});
  if (result && result.ok === true && result.data && result.data.available === true) {
    return { bridge_status: "connected", core_status: "available" };
  }
  const code = result && result.error && result.error.code;
  if (code === "CORE_UNAVAILABLE" || code === "CORE_CONTROL_UNAVAILABLE" || code === "INVALID_CONTROL_REQUEST" || code === "CORE_CONTROL_ERROR") {
    return { bridge_status: "connected", core_status: "unavailable", connection_error: result.error };
  }
  return { bridge_status: "unavailable", core_status: "unavailable", connection_error: result && result.error };
}

async function buildSupervisorView() {
  const [bindingState, connection, tab] = await Promise.all([
    readSupervisorBindingState(), coreAvailability(), activeSupervisorTab(),
  ]);
  const binding = bindingState.binding;
  let probe = null;
  if (tab) probe = await probeSupervisorTab(tab, null);
  const currentData = probe && probe.ok ? probe.data : null;
  const session = currentData && currentData.session || {};
  const current = {
    session_id: session.session_id || null,
    kind: session.kind || (tab ? "unknown" : "unavailable"),
    project_name: session.project_name || null,
    conversation_name: session.conversation_name || null,
    bindable: !!session.session_id && ["chat", "project"].includes(session.kind) &&
      (!currentData.assistants.length || !!currentData.latest_assistant_id),
    latest_assistant_id: currentData && currentData.latest_assistant_id || null,
  };
  let status;
  if (bindingState.kind === "invalid") status = "binding_invalid";
  else if (!binding) status = current.bindable ? "unbound" : (tab ? "identity_unavailable" : "unavailable");
  else if (!current.session_id) status = "identity_unavailable";
  else if (current.session_id !== binding.native_session_id) status = "mismatch";
  else status = binding.state;
  const viewBinding = binding && current.session_id === binding.native_session_id ? {
    ...binding,
    project_name: current.project_name || binding.project_name || null,
    conversation_name: current.conversation_name || binding.conversation_name || null,
  } : binding;
  return {
    binding: viewBinding, current, status,
    bridge_status: connection.bridge_status,
    core_status: connection.core_status,
    error: bindingState.kind === "invalid"
      ? { code: "BINDING_STORAGE_INVALID", message: "本地 Supervisor 绑定记录无效，请先解除绑定" }
      : (probe && !probe.ok && probe.error) || connection.connection_error || null,
  };
}

async function getSupervisorView() {
  if (!supervisorStateRequest) {
    supervisorStateRequest = buildSupervisorView().finally(() => { supervisorStateRequest = null; });
  }
  return supervisorStateRequest;
}

function anchorFor(probe, reason, at) {
  return { message_id: probe.latest_assistant_id || null, updated_at: at, reason };
}

function emptySupervisorScanState(sessionId, diagnostic = null) {
  return {
    schema_version: 1,
    session_id: sessionId,
    armed_message_id: null,
    diagnostic,
    anchor_message_id: null,
    anchor_fingerprint: null,
    anchor_disposition: null,
    updated_at: new Date().toISOString(),
  };
}

function validSupervisorScanState(value, sessionId) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const fields = ["schema_version", "session_id", "armed_message_id", "diagnostic",
    "anchor_message_id", "anchor_fingerprint", "anchor_disposition", "updated_at"];
  if (Object.keys(value).sort().join("|") !== fields.sort().join("|") ||
      value.schema_version !== 1 || value.session_id !== sessionId || !validControlText(value.session_id) ||
      (value.armed_message_id !== null && !validControlText(value.armed_message_id)) ||
      (value.anchor_message_id !== null && !validControlText(value.anchor_message_id)) ||
      (value.anchor_fingerprint !== null && !/^sub_[0-9a-f]{64}$/.test(value.anchor_fingerprint)) ||
      ![null, "ordinary", "invalid", "submitted"].includes(value.anchor_disposition) ||
      ![null, "ANCHOR_NOT_FOUND", "SCAN_STATE_INVALID"].includes(value.diagnostic) ||
      !validUtc(value.updated_at)) return false;
  return (value.anchor_fingerprint === null) === (value.anchor_message_id === null);
}

async function resetSupervisorScanState(sessionId) {
  if (!sessionId) {
    await chrome.storage.local.remove(SUPERVISOR_SCAN_KEY);
    return;
  }
  await chrome.storage.local.set({ [SUPERVISOR_SCAN_KEY]: emptySupervisorScanState(sessionId) });
}

async function readSupervisorScanState(sessionId) {
  const data = await chrome.storage.local.get(SUPERVISOR_SCAN_KEY);
  if (!data || !(SUPERVISOR_SCAN_KEY in data)) return { state: emptySupervisorScanState(sessionId), invalid: false };
  const value = data[SUPERVISOR_SCAN_KEY];
  if (!validSupervisorScanState(value, sessionId)) {
    const safe = emptySupervisorScanState(sessionId, "SCAN_STATE_INVALID");
    await chrome.storage.local.set({ [SUPERVISOR_SCAN_KEY]: safe });
    return { state: safe, invalid: true };
  }
  return { state: value, invalid: false };
}

async function writeSupervisorScanState(value) {
  if (!validSupervisorScanState(value, value && value.session_id)) throw new Error("invalid Supervisor scan state");
  await chrome.storage.local.set({ [SUPERVISOR_SCAN_KEY]: value });
}

async function scanPayloadFingerprint(binding, item) {
  return ContextFlowDispatch.submissionIdFor({
    endpointId: binding.endpoint_id,
    sessionId: binding.native_session_id,
    messageId: item.message_id,
    normalizedText: ContextFlowDispatch.normalizeFullMessage(item.content || "") || "<empty>",
  });
}

function scanDisposition(item) {
  const parsed = ContextFlowDispatch.parse(item.content || "");
  if (parsed.status === "valid") return "submitted";
  return parsed.status === "invalid" ? "invalid" : "ordinary";
}

function supervisorMessageRead(tabId, sessionId, messageId) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(supervisorError("READ_TIMEOUT", "读取 Assistant 消息超时")), READ_TIMEOUT_MS);
    try {
      chrome.tabs.sendMessage(tabId, {
        type: "cf:supervisor_read_message",
        expected_session_id: sessionId,
        message_id: messageId,
      }, (response) => {
        clearTimeout(timer);
        void chrome.runtime.lastError;
        resolve(response && response.ok === true ? response :
          (response && response.error ? response : supervisorError("CONTENT_SCRIPT_UNAVAILABLE", "无法读取 Assistant 消息")));
      });
    } catch (_err) {
      clearTimeout(timer);
      resolve(supervisorError("CONTENT_SCRIPT_UNAVAILABLE", "无法读取 Assistant 消息"));
    }
  });
}

function validLegacyEnvelope(record, binding, message, text) {
  const envelope = record && record.envelope;
  if (!record || typeof record !== "object" || Array.isArray(record) ||
      !["accepted", "unknown", "failed", "submitting"].includes(record.status) ||
      !validControlText(record.submission_id) || !envelope || typeof envelope !== "object" || Array.isArray(envelope) ||
      Object.keys(envelope).sort().join("|") !== "content|schema_version|source|submission_id" ||
      envelope.schema_version !== 1 || envelope.submission_id !== record.submission_id ||
      !/^sub_[A-Za-z0-9_-]{8,}$/.test(envelope.submission_id)) return false;
  const source = envelope.source;
  const content = envelope.content;
  const sourceFields = "adapter|endpoint_id|message_id|native_session_id|turn_group_id|user_message_id";
  if (!source || typeof source !== "object" || Array.isArray(source) ||
      Object.keys(source).sort().join("|") !== sourceFields ||
      !content || typeof content !== "object" || Array.isArray(content) ||
      Object.keys(content).sort().join("|") !== "format|text" ||
      source.adapter !== "chatgpt_web" || !validControlText(source.endpoint_id, /^cfep_[A-Za-z0-9_-]{8,}$/) ||
      !validControlText(source.native_session_id) || !validControlText(source.message_id) ||
      (source.user_message_id !== null && !validControlText(source.user_message_id)) ||
      (source.turn_group_id !== null && !validControlText(source.turn_group_id)) ||
      content.format !== "markdown" || typeof content.text !== "string" || !content.text.trim() ||
      content.text.length > 2_000_000 || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(content.text)) return false;
  return source.endpoint_id === binding.endpoint_id && source.native_session_id === binding.native_session_id &&
    source.message_id === message.message_id && content.text === text;
}

async function reconcileSupervisorRecordsForWorker() {
  const local = await chrome.storage.local.get([SUPERVISOR_WORKER_INSTANCE_KEY, SUPERVISOR_SUBMISSIONS_KEY]);
  const previous = local && local[SUPERVISOR_WORKER_INSTANCE_KEY];
  const document = local && local[SUPERVISOR_SUBMISSIONS_KEY];
  if (previous && previous !== supervisorWorkerInstanceId && document && document.schema_version === 1 && document.records) {
    let changed = false;
    for (const record of Object.values(document.records)) {
      if (record && record.status === "submitting" && record.envelope && record.submission_id) {
        record.status = "unknown";
        record.error = { code: "UNKNOWN_AFTER_RESTART", message: "扩展后台重启，提交结果待确认" };
        record.updated_at = new Date().toISOString();
        changed = true;
      }
    }
    if (changed) await writeSupervisorSubmissionDocument(document);
  }
  await chrome.storage.local.set({ [SUPERVISOR_WORKER_INSTANCE_KEY]: supervisorWorkerInstanceId });
}

const supervisorWorkerReady = reconcileSupervisorRecordsForWorker().catch((err) => {
  WARN("Supervisor submission reconciliation failed:", String(err));
});

function validPublicRecordError(value) {
  if (value === null) return true;
  return !!value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join("|") === "code|message" && validControlText(value.code) &&
    typeof value.message === "string" && value.message.length > 0 && value.message.length <= 512 &&
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(value.message);
}

function validStoredEnvelope(envelope) {
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope) ||
      Object.keys(envelope).sort().join("|") !== "content|schema_version|source|submission_id" ||
      envelope.schema_version !== 1 || !/^sub_[A-Za-z0-9_-]{8,}$/.test(envelope.submission_id || "")) return false;
  const source = envelope.source;
  const content = envelope.content;
  return !!source && typeof source === "object" && !Array.isArray(source) &&
    Object.keys(source).sort().join("|") === "adapter|endpoint_id|message_id|native_session_id|turn_group_id|user_message_id" &&
    source.adapter === "chatgpt_web" && validControlText(source.endpoint_id, /^cfep_[A-Za-z0-9_-]{8,}$/) &&
    validControlText(source.native_session_id) && validControlText(source.message_id) &&
    (source.user_message_id === null || validControlText(source.user_message_id)) &&
    (source.turn_group_id === null || validControlText(source.turn_group_id)) &&
    !!content && typeof content === "object" && !Array.isArray(content) &&
    Object.keys(content).sort().join("|") === "format|text" && content.format === "markdown" &&
    typeof content.text === "string" && content.text.trim().length > 0 && content.text.length <= 2_000_000 &&
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(content.text);
}

function validSupervisorSubmissionRecord(key, record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) return false;
  const fields = ["schema_version", "submission_id", "envelope", "native_session_id", "native_message_id",
    "payload_mode", "first_trigger", "current_trigger", "status", "duplicate", "error", "created_at",
    "updated_at", "accepted_at", "parser", "legacy"];
  // Pre-legacy-marker records written by this release are accepted only when
  // every other field is exact; they are normalized with legacy=false on read.
  const actual = Object.keys(record).sort();
  const withoutLegacy = fields.filter((field) => field !== "legacy").sort();
  if (actual.join("|") !== fields.sort().join("|") && actual.join("|") !== withoutLegacy.join("|")) return false;
  if (!("legacy" in record)) record.legacy = !/^sub_[0-9a-f]{64}$/.test(record.submission_id || "");
  if (record.schema_version !== 1 || key !== record.submission_id || !validStoredEnvelope(record.envelope) ||
      record.envelope.submission_id !== record.submission_id || record.native_session_id !== record.envelope.source.native_session_id ||
      record.native_message_id !== record.envelope.source.message_id || !["dispatch_blocks", "full_message"].includes(record.payload_mode) ||
      !["auto", "manual", "manual_full_override"].includes(record.first_trigger) ||
      !["auto", "manual", "manual_full_override"].includes(record.current_trigger) ||
      !["new", "submitting", "accepted", "unknown", "failed"].includes(record.status) ||
      typeof record.duplicate !== "boolean" || !validPublicRecordError(record.error) ||
      !validUtc(record.created_at) || !validUtc(record.updated_at) ||
      (record.accepted_at !== null && !validUtc(record.accepted_at)) || record.parser !== null || typeof record.legacy !== "boolean") return false;
  if (record.status === "accepted" && !record.accepted_at) return false;
  if (record.status === "failed" || record.status === "unknown") {
    if (record.error === null) return false;
  } else if (record.error !== null) return false;
  if (record.legacy) {
    // Legacy provenance is established only by the explicit v0.6 migrator.
    // A deterministic C-01 namespace can never opt itself out of digest checks.
    return /^sub_[A-Za-z0-9_-]{8,}$/.test(record.submission_id) &&
      !/^sub_[0-9a-f]{64}$/.test(record.submission_id) && record.first_trigger === "manual";
  }
  return /^sub_[0-9a-f]{64}$/.test(record.submission_id);
}

async function legacyProvenanceMatches(record) {
  if (!record.legacy) return true;
  const data = await chrome.storage.local.get(LEGACY_SUBMISSIONS_KEY);
  const records = data && data[LEGACY_SUBMISSIONS_KEY];
  if (!records || typeof records !== "object" || Array.isArray(records)) return false;
  const expectedKey = `${record.native_session_id}:${record.native_message_id}`;
  const original = records[expectedKey];
  if (!original || original.status !== "accepted" || original.submission_id !== record.submission_id ||
      !original.envelope || JSON.stringify(original.envelope) !== JSON.stringify(record.envelope)) return false;
  return true;
}

async function validateDeterministicRecord(record) {
  if (record.legacy) return legacyProvenanceMatches(record);
  const expected = await ContextFlowDispatch.submissionIdFor({
    endpointId: record.envelope.source.endpoint_id,
    sessionId: record.native_session_id,
    messageId: record.native_message_id,
    normalizedText: record.envelope.content.text,
  });
  return expected === record.submission_id;
}

async function readSupervisorSubmissionDocument() {
  await supervisorWorkerReady;
  const data = await chrome.storage.local.get(SUPERVISOR_SUBMISSIONS_KEY);
  const value = data && data[SUPERVISOR_SUBMISSIONS_KEY];
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).sort().join("|") !== "records|schema_version" || value.schema_version !== 1 ||
      !value.records || typeof value.records !== "object" || Array.isArray(value.records)) {
    return { schema_version: 1, records: {} };
  }
  const records = {};
  for (const [key, original] of Object.entries(value.records)) {
    const record = original && JSON.parse(JSON.stringify(original));
    if (!validSupervisorSubmissionRecord(key, record) || !(await validateDeterministicRecord(record))) continue;
    records[key] = record;
  }
  return { schema_version: 1, records };
}

async function writeSupervisorSubmissionDocument(document) {
  const entries = Object.entries(document.records)
    .sort((a, b) => String(b[1].updated_at || "").localeCompare(String(a[1].updated_at || "")))
    .slice(0, SUPERVISOR_RECORD_LIMIT);
  await chrome.storage.local.set({ [SUPERVISOR_SUBMISSIONS_KEY]: { schema_version: 1, records: Object.fromEntries(entries) } });
}

async function matchingLegacyRecord(binding, message, text) {
  const data = await chrome.storage.local.get(LEGACY_SUBMISSIONS_KEY);
  const records = data && data[LEGACY_SUBMISSIONS_KEY];
  if (!records || typeof records !== "object") return null;
  for (const record of Object.values(records)) {
    if (!record || !validLegacyEnvelope(record, binding, message, text)) continue;
    return record;
  }
  return null;
}

function publicSubmissionRecord(record) {
  if (!record) return null;
  return {
    submission_id: record.submission_id,
    native_message_id: record.native_message_id,
    payload_mode: record.payload_mode,
    status: record.status,
    duplicate: record.duplicate === true,
    error: record.error || null,
    updated_at: record.updated_at,
  };
}

async function submissionSelectionForMessage(binding, message) {
  const parsed = ContextFlowDispatch.parse(message.content);
  if (parsed.status === "valid") return { status: "valid", payload_mode: "dispatch_blocks", text: parsed.content };
  if (parsed.status === "none") return { status: "none", payload_mode: "full_message",
    text: ContextFlowDispatch.normalizeFullMessage(message.content) };
  return { status: "invalid", error: parsed.error };
}

async function exactRecordForMessage(tabId, sessionId, messageId) {
  await supervisorWorkerReady;
  const stored = await readSupervisorBindingState();
  if (stored.kind !== "valid" || !stored.binding || stored.binding.native_session_id !== sessionId) return null;
  const fresh = await supervisorMessageRead(tabId, sessionId, messageId);
  if (!fresh.ok || fresh.data.status !== "complete") return null;
  const selected = await submissionSelectionForMessage(stored.binding, fresh.data);
  if (selected.status === "invalid") {
    const fullText = ContextFlowDispatch.normalizeFullMessage(fresh.data.content);
    if (!fullText) return { invalid: true, error: selected.error || null };
    const overrideId = await ContextFlowDispatch.submissionIdFor({
      endpointId: stored.binding.endpoint_id, sessionId, messageId, normalizedText: fullText,
    });
    const overrideDocument = await readSupervisorSubmissionDocument();
    const override = overrideDocument.records[overrideId];
    if (override && override.payload_mode === "full_message" && override.status === "accepted") return override;
    // Recovery fallback: a previously accepted full-message override is still
    // authoritative when the current DOM extractor yields an equivalent invalid
    // representation after reload (e.g. editor whitespace normalization).
    const acceptedOverrides = Object.values(overrideDocument.records).filter((record) =>
      record && record.native_session_id === sessionId && record.native_message_id === messageId &&
      record.payload_mode === "full_message" && record.status === "accepted");
    if (acceptedOverrides.length === 1) return acceptedOverrides[0];
    const legacyOverride = await matchingLegacyRecord(stored.binding, fresh.data, fullText);
    if (legacyOverride && legacyOverride.status === "accepted") return overrideDocument.records[legacyOverride.submission_id] || legacyOverride;
    return { invalid: true, error: selected.error || null };
  }
  if (!selected.text) return null;
  const deterministic = await ContextFlowDispatch.submissionIdFor({
    endpointId: stored.binding.endpoint_id, sessionId, messageId, normalizedText: selected.text,
  });
  const document = await readSupervisorSubmissionDocument();
  let record = document.records[deterministic] || null;
  if (!record) {
    const legacy = await matchingLegacyRecord(stored.binding, fresh.data, selected.text);
    if (legacy) record = document.records[legacy.submission_id] || legacy;
  }
  return record;
}

async function latestRecordForMessage(sessionId, messageId) {
  const document = await readSupervisorSubmissionDocument();
  return Object.values(document.records)
    .filter((record) => record && record.native_session_id === sessionId && record.native_message_id === messageId)
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0] || null;
}

async function mutateSupervisorSubmissionDocument(operation) {
  const run = supervisorRecordTail.then(async () => {
    const document = await readSupervisorSubmissionDocument();
    const knownKeys = new Set(Object.keys(document.records));
    const result = await operation(document);
    // Never preserve unvalidated records that appeared in storage while this
    // worker was alive. Mutations may add only the operation's validated result.
    for (const key of Object.keys(document.records)) {
      if (!knownKeys.has(key) && (!result || result.submission_id !== key)) delete document.records[key];
    }
    await writeSupervisorSubmissionDocument(document);
    return result;
  });
  supervisorRecordTail = run.catch(() => undefined);
  return run;
}

async function persistSubmissionRecord(record) {
  return mutateSupervisorSubmissionDocument((document) => {
    const snapshot = JSON.parse(JSON.stringify(record));
    document.records[snapshot.submission_id] = snapshot;
    return snapshot;
  });
}

function submissionMode(parseResult, requestedMode) {
  if (parseResult.status === "valid") return { ok: true, payload_mode: "dispatch_blocks", text: parseResult.content };
  if (parseResult.status === "none" && requestedMode !== "auto") {
    return { ok: true, payload_mode: "full_message", text: null };
  }
  if (parseResult.status === "none") return { ok: false, disposition: "ordinary" };
  return { ok: false, disposition: "invalid", error: parseResult.error };
}

async function constructSupervisorSubmission(binding, message, requestedMode, confirmationToken) {
  const parsed = ContextFlowDispatch.parse(message.content);
  const selected = submissionMode(parsed, requestedMode);
  if (!selected.ok) {
    if (selected.disposition === "ordinary") return { ok: true, disposition: "ordinary" };
    if (requestedMode === "auto") return { ok: true, disposition: "invalid", error: selected.error };
    const fullText = ContextFlowDispatch.normalizeFullMessage(message.content);
    const tokenId = await ContextFlowDispatch.submissionIdFor({
      endpointId: binding.endpoint_id, sessionId: binding.native_session_id,
      messageId: message.message_id, normalizedText: fullText,
    });
    const token = `override_${tokenId.slice(4)}`;
    const key = `${binding.native_session_id}:${message.message_id}`;
    if (requestedMode !== "manual_full_override" || confirmationToken !== token ||
        fullOverrideConfirmations.get(key) !== token) {
      fullOverrideConfirmations.set(key, token);
      return supervisorError("MARKER_INVALID_CONFIRMATION_REQUIRED", selected.error.message,
        { confirmation_token: token, marker_error: selected.error.code });
    }
    fullOverrideConfirmations.delete(key);
    selected.ok = true;
    selected.payload_mode = "full_message";
    selected.text = fullText;
  }
  if (selected.text === null) selected.text = ContextFlowDispatch.normalizeFullMessage(message.content);
  if (!selected.text) return supervisorError("CONTENT_EMPTY", "Assistant 消息正文为空");
  const deterministicId = await ContextFlowDispatch.submissionIdFor({
    endpointId: binding.endpoint_id, sessionId: binding.native_session_id,
    messageId: message.message_id, normalizedText: selected.text,
  });
  const legacy = await matchingLegacyRecord(binding, message, selected.text);
  const envelope = legacy ? legacy.envelope : {
    schema_version: 1,
    submission_id: deterministicId,
    source: {
      adapter: "chatgpt_web", endpoint_id: binding.endpoint_id,
      native_session_id: binding.native_session_id, message_id: message.message_id,
      user_message_id: message.user_message_id || null, turn_group_id: message.turn_group_id || null,
    },
    content: { format: "markdown", text: selected.text },
  };
    return { ok: true, disposition: "submit", envelope, payload_mode: selected.payload_mode,
    legacy_status: legacy && legacy.status };
}

function safeSubmissionError(value, fallbackCode, fallbackMessage) {
  const code = value && validControlText(value.code) ? value.code : fallbackCode;
  const message = value && typeof value.message === "string" && value.message.length > 0
    ? value.message.slice(0, 512) : fallbackMessage;
  return { code, message };
}

async function deliverSupervisorRecord(record, automatic) {
  if (record.status === "accepted" || record.status === "submitting") return record;
  if (automatic && record.status === "unknown") return record;
  record.status = "submitting";
  record.error = null;
  record.updated_at = new Date().toISOString();
  await persistSubmissionRecord(record);
  const result = await handleSubmitEnvelope(record.envelope);
  if (result && result.ok === true && result.status === "accepted") {
    record.status = "accepted";
    record.duplicate = result.duplicate === true;
    record.accepted_at = new Date().toISOString();
    record.error = null;
  } else if (result && ["failed", "rejected", "conflict"].includes(result.status)) {
    record.status = "failed";
    record.error = safeSubmissionError(result.error, "SUBMIT_FAILED", "提交被拒绝");
  } else {
    record.status = "unknown";
    record.error = safeSubmissionError(result && result.error, "UNKNOWN", "提交结果未知");
  }
  record.updated_at = new Date().toISOString();
  await persistSubmissionRecord(record);
  return record;
}

async function submitSupervisorMessage(tabId, sessionId, messageId, requestedMode, confirmationToken) {
  await supervisorWorkerReady;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!allowedSupervisorTab(tab)) return supervisorError("ORIGIN_NOT_ALLOWED", "页面 Origin 未获 Supervisor 授权");
  const stored = await readSupervisorBindingState();
  if (stored.kind !== "valid" || !stored.binding) {
    return supervisorError(stored.kind === "invalid" ? "BINDING_STORAGE_INVALID" : "SUPERVISOR_NOT_BOUND", "Supervisor 绑定不可用");
  }
  const binding = stored.binding;
  if (binding.native_session_id !== sessionId) return supervisorError("SESSION_CHANGED", "页面与 Supervisor 绑定不一致");
  if (requestedMode === "auto" && binding.state !== "active") return supervisorError("SUPERVISOR_PAUSED", "Supervisor 已暂停");
  const fresh = await supervisorMessageRead(tabId, sessionId, messageId);
  if (!fresh.ok) return fresh;
  const message = fresh.data;
  if (message.status !== "complete") return supervisorError("MESSAGE_NOT_COMPLETE", "Assistant 回复尚未完成");
  const constructed = await constructSupervisorSubmission(binding, message, requestedMode, confirmationToken);
  if (!constructed.ok || constructed.disposition !== "submit") return constructed;
  const submissionId = constructed.envelope.submission_id;
  let record = await mutateSupervisorSubmissionDocument((document) => {
    let current = document.records[submissionId];
    if (!current) {
      const now = new Date().toISOString();
      current = {
        schema_version: 1, submission_id: submissionId, envelope: constructed.envelope,
        native_session_id: sessionId, native_message_id: messageId,
        payload_mode: constructed.payload_mode,
        first_trigger: constructed.legacy_status != null ? "manual" : requestedMode,
        current_trigger: requestedMode,
        status: constructed.legacy_status === "accepted" ? "accepted" : "new",
        duplicate: false, error: null, created_at: now, updated_at: now,
        accepted_at: constructed.legacy_status === "accepted" ? now : null,
        parser: null, legacy: constructed.legacy_status != null,
      };
      document.records[submissionId] = current;
    } else {
      current = JSON.parse(JSON.stringify(current));
      current.current_trigger = requestedMode;
      document.records[submissionId] = current;
    }
    return JSON.parse(JSON.stringify(current));
  });
  const previous = supervisorSubmissionTails.get(submissionId) || Promise.resolve();
  const run = previous.then(() => deliverSupervisorRecord(record, requestedMode === "auto"));
  supervisorSubmissionTails.set(submissionId, run.catch(() => undefined));
  const delivered = await run;
  return { ok: delivered.status === "accepted", status: delivered.status,
    data: { record: publicSubmissionRecord(delivered) }, error: delivered.error || null };
}

function submitDispositionSucceeded(result) {
  return !!result && (result.ok === true || result.disposition === "ordinary" || result.disposition === "invalid" ||
    result.status === "accepted" || result.status === "unknown" || result.status === "failed");
}

async function updateObservedAnchor(binding, item) {
  const state = await readSupervisorBindingState();
  if (state.kind !== "valid" || !state.binding || state.binding.native_session_id !== binding.native_session_id ||
      state.binding.state !== "active") return;
  const at = new Date().toISOString();
  await writeSupervisorBinding({ ...state.binding, updated_at: at,
    anchor: { message_id: item.message_id, updated_at: at, reason: "observed" } });
  const scanResult = await readSupervisorScanState(binding.native_session_id);
  if (!scanResult.invalid) {
    const fingerprint = await scanPayloadFingerprint(binding, item);
    await writeSupervisorScanState({ ...scanResult.state, anchor_message_id: item.message_id,
      anchor_fingerprint: fingerprint, anchor_disposition: scanDisposition(item),
      diagnostic: null, updated_at: at });
  }
}

async function observeExistingSubmissionAutomatically(binding, item) {
  const parsed = ContextFlowDispatch.parse(item.content || "");
  if (parsed.status !== "valid" || !parsed.content) return null;
  const submissionId = await ContextFlowDispatch.submissionIdFor({
    endpointId: binding.endpoint_id,
    sessionId: binding.native_session_id,
    messageId: item.message_id,
    normalizedText: parsed.content,
  });
  return mutateSupervisorSubmissionDocument((document) => {
    const current = document.records[submissionId];
    if (!current) return null;
    current.current_trigger = "auto";
    current.updated_at = new Date().toISOString();
    document.records[submissionId] = current;
    return JSON.parse(JSON.stringify(current));
  });
}

async function scanSupervisorSnapshot(tabId, snapshot) {
  const stored = await readSupervisorBindingState();
  if (stored.kind !== "valid" || !stored.binding || stored.binding.state !== "active") return;
  const binding = stored.binding;
  if (!snapshot || !snapshot.session || snapshot.session.session_id !== binding.native_session_id) return;
  const assistants = (snapshot.messages || []).filter((item) => item.role === "assistant" && item.message_id);
  if (!assistants.length) return;
  const scanResult = await readSupervisorScanState(binding.native_session_id);
  const scan = scanResult.state;
  if (scanResult.invalid) return;
  const anchorId = binding.anchor.message_id;
  const anchorIndex = anchorId === null ? -1 : assistants.findIndex((item) => item.message_id === anchorId);

  // The anchor itself may be rewritten in place by ChatGPT. Its stable ID is
  // not enough: compare persisted content identity and process a genuinely new
  // payload once, while identical rerenders remain inert.
  if (anchorIndex >= 0 && assistants[anchorIndex].status === "complete") {
    const anchorItem = assistants[anchorIndex];
    const fingerprint = await scanPayloadFingerprint(binding, anchorItem);
    if (scan.anchor_message_id === anchorId && scan.anchor_fingerprint !== null && scan.anchor_fingerprint !== fingerprint) {
      const result = await submitSupervisorMessage(tabId, binding.native_session_id, anchorId, "auto", null);
      if (!submitDispositionSucceeded(result)) return;
      await writeSupervisorScanState({ ...scan, anchor_fingerprint: fingerprint,
        anchor_disposition: scanDisposition(anchorItem), diagnostic: null, updated_at: new Date().toISOString() });
    } else if (scan.anchor_message_id !== anchorId || scan.anchor_fingerprint === null) {
      await writeSupervisorScanState({ ...scan, anchor_message_id: anchorId, anchor_fingerprint: fingerprint,
        anchor_disposition: scanDisposition(anchorItem), diagnostic: null, updated_at: new Date().toISOString() });
    }
  }

  if (anchorId === null) {
    const armed = scan.armed_message_id && assistants.find((item) => item.message_id === scan.armed_message_id);
    if (armed && armed.status === "complete") {
      const observed = await observeExistingSubmissionAutomatically(binding, armed);
      const result = observed ? { ok: true, status: observed.status } :
        await submitSupervisorMessage(tabId, binding.native_session_id, armed.message_id, "auto", null);
      if (!submitDispositionSucceeded(result)) return;
      await updateObservedAnchor(binding, armed);
      return;
    }
    const generating = assistants.find((item) => item.status === "in_progress");
    if (generating) {
      await writeSupervisorScanState({ ...scan, armed_message_id: generating.message_id, diagnostic: null, updated_at: new Date().toISOString() });
    } else {
      await updateObservedAnchor(binding, assistants[assistants.length - 1]);
    }
    return;
  }

  if (anchorIndex < 0) {
    const armed = scan.armed_message_id && assistants.find((item) => item.message_id === scan.armed_message_id);
    if (armed && armed.status === "complete") {
      const observed = await observeExistingSubmissionAutomatically(binding, armed);
      const result = observed ? { ok: true, status: observed.status } :
        await submitSupervisorMessage(tabId, binding.native_session_id, armed.message_id, "auto", null);
      if (!submitDispositionSucceeded(result)) return;
      await updateObservedAnchor(binding, armed);
      return;
    }
    const generating = assistants.find((item) => item.status === "in_progress");
    await writeSupervisorScanState({ ...scan, armed_message_id: generating ? generating.message_id : scan.armed_message_id,
      diagnostic: "ANCHOR_NOT_FOUND", updated_at: new Date().toISOString() });
    return;
  }

  for (const candidate of assistants.slice(anchorIndex + 1)) {
    if (candidate.status !== "complete") {
      await writeSupervisorScanState({ ...scan, armed_message_id: candidate.message_id, diagnostic: null,
        updated_at: new Date().toISOString() });
      return;
    }
    const observed = await observeExistingSubmissionAutomatically(binding, candidate);
    const result = observed ? { ok: true, status: observed.status } :
      await submitSupervisorMessage(tabId, binding.native_session_id, candidate.message_id, "auto", null);
    if (!submitDispositionSucceeded(result)) return;
    await updateObservedAnchor(binding, candidate);
  }
  const finalState = await readSupervisorScanState(binding.native_session_id);
  if (!finalState.invalid) {
    await writeSupervisorScanState({ ...finalState.state, armed_message_id: null, diagnostic: null,
      updated_at: new Date().toISOString() });
  }
}

function scheduleSupervisorScan(tabId, snapshot) {
  const sessionId = snapshot && snapshot.session && snapshot.session.session_id;
  if (!sessionId) return;
  const previous = supervisorScanTails.get(sessionId) || Promise.resolve();
  const run = previous.then(() => enqueueSupervisorMutation(() => scanSupervisorSnapshot(tabId, snapshot)));
  supervisorScanTails.set(sessionId, run.catch((err) => {
    const marker = String(err && (err.stack || err.message) || err);
    WARN("Supervisor scan failed:", marker);
    state.lastError = marker;
  }));
}

async function bindCurrentSupervisor(confirmReplace, expectedReplacementSessionId) {
  const current = await currentSupervisorProbe(null);
  if (!current.ok) return current;
  const existing = await readSupervisorBinding();
  const session = current.data.session;
  if (confirmReplace === true && expectedReplacementSessionId !== session.session_id) {
    return supervisorError(
      "REPLACEMENT_CONFIRMATION_REQUIRED",
      "替换目标已经变化，请重新确认",
      { existing_session_id: existing ? existing.native_session_id : null, target_session_id: session.session_id },
    );
  }
  if (existing && existing.native_session_id !== session.session_id) {
    const details = {
      existing_session_id: existing.native_session_id,
      target_session_id: session.session_id,
    };
    if (confirmReplace !== true || expectedReplacementSessionId !== session.session_id) {
      return supervisorError(
        "REPLACEMENT_CONFIRMATION_REQUIRED",
        "当前已有其他 Supervisor，会话替换需要再次确认",
        details,
      );
    }
  }
  const control = await callSupervisorControl("bind_supervisor", {
    native_session_id: session.session_id,
    session_kind: session.kind,
  });
  if (!control || control.ok !== true || !control.data || !control.data.endpoint_id) {
    return supervisorError((control && control.error && control.error.code) || "CORE_UNAVAILABLE",
      (control && control.error && control.error.message) || "Core 无法绑定当前会话");
  }
  const at = new Date().toISOString();
  const same = existing && existing.native_session_id === session.session_id;
  const binding = {
    schema_version: 2,
    native_session_id: session.session_id,
    endpoint_id: control.data.endpoint_id,
    session_kind: session.kind,
    project_name: session.project_name || null,
    conversation_name: session.conversation_name || null,
    state: "active",
    bound_at: same ? existing.bound_at : at,
    updated_at: at,
    anchor: anchorFor(current.data, "bind", at),
  };
  await writeSupervisorBinding(binding);
  await resetSupervisorScanState(binding.native_session_id);
  return { ok: true, data: { binding } };
}

async function pauseSupervisor() {
  const binding = await readSupervisorBinding();
  if (!binding) return supervisorError("SUPERVISOR_NOT_BOUND", "尚未绑定 Supervisor");
  const updated = { ...binding, state: "paused", updated_at: new Date().toISOString() };
  await writeSupervisorBinding(updated);
  return { ok: true, data: { binding: updated } };
}

async function resumeSupervisor() {
  const binding = await readSupervisorBinding();
  if (!binding) return supervisorError("SUPERVISOR_NOT_BOUND", "尚未绑定 Supervisor");
  const current = await currentSupervisorProbe(binding.native_session_id);
  if (!current.ok) return current;
  const control = await callSupervisorControl("bind_supervisor", {
    native_session_id: binding.native_session_id,
    session_kind: binding.session_kind,
  });
  if (!control || control.ok !== true || !control.data || !control.data.endpoint_id) {
    return supervisorError((control && control.error && control.error.code) || "CORE_UNAVAILABLE",
      (control && control.error && control.error.message) || "Core 无法核验 Supervisor");
  }
  const at = new Date().toISOString();
  const updated = {
    ...binding,
    schema_version: 2,
    endpoint_id: control.data.endpoint_id,
    project_name: current.data.session.project_name || binding.project_name || null,
    conversation_name: current.data.session.conversation_name || binding.conversation_name || null,
    state: "active",
    updated_at: at,
    anchor: anchorFor(current.data, "resume", at),
  };
  await writeSupervisorBinding(updated);
  await resetSupervisorScanState(updated.native_session_id);
  return { ok: true, data: { binding: updated } };
}

function enqueueSupervisorMutation(operation) {
  const run = supervisorMutationTail.then(operation, operation);
  supervisorMutationTail = run.catch(() => undefined);
  supervisorStateRequest = null;
  return run;
}

async function waitForSupervisorMutations() {
  await supervisorMutationTail;
}

async function handleSupervisorManagement(msg, sender) {
  if (!isTrustedExtensionPage(sender)) return supervisorError("MANAGEMENT_FORBIDDEN", "管理操作只允许从扩展控制面板发起");
  if (msg.type === "cf:supervisor_get_state") {
    await waitForSupervisorMutations();
    return { ok: true, data: await getSupervisorView() };
  }
  if (msg.type !== "cf:supervisor_unbind") {
    const stored = await readSupervisorBindingState();
    if (stored.kind === "invalid") {
      return supervisorError("BINDING_STORAGE_INVALID", "本地 Supervisor 绑定记录无效，请先解除绑定");
    }
  }
  if (msg.type === "cf:supervisor_bind") {
    const fields = Object.keys(msg).sort().join("|");
    if (!["confirm_replace|type", "confirm_replace|expected_replacement_session_id|type"].includes(fields) ||
        typeof msg.confirm_replace !== "boolean" ||
        ("expected_replacement_session_id" in msg && typeof msg.expected_replacement_session_id !== "string")) {
      return supervisorError("INVALID_REQUEST", "绑定请求格式无效");
    }
    return bindCurrentSupervisor(msg.confirm_replace, msg.expected_replacement_session_id || null);
  }
  if (msg.type === "cf:supervisor_pause") return pauseSupervisor();
  if (msg.type === "cf:supervisor_resume") return resumeSupervisor();
  if (msg.type === "cf:supervisor_unbind") {
    await chrome.storage.local.remove([SUPERVISOR_BINDING_KEY, SUPERVISOR_SCAN_KEY]);
    fullOverrideConfirmations.clear();
    return { ok: true, data: { unbound: true } };
  }
  return supervisorError("INVALID_REQUEST", "未知 Supervisor 管理操作");
}

function dispatchSupervisorManagement(msg, sender, sendResponse) {
  if (!msg || !SUPERVISOR_MANAGEMENT_TYPES.has(msg.type)) return false;
  const mutation = msg.type !== "cf:supervisor_get_state";
  const operation = () => handleSupervisorManagement(msg, sender);
  const task = mutation ? enqueueSupervisorMutation(operation) : operation();
  task.then(sendResponse).catch(() =>
    sendResponse(supervisorError("INTERNAL_ERROR", "Supervisor 管理操作失败")));
  return true;
}

chrome.runtime.onMessage.addListener(dispatchSupervisorManagement);

function trustedSubmissionSender(sender) {
  return !!sender && sender.id === chrome.runtime.id && sender.tab && sender.tab.id != null &&
    allowedSupervisorTab(sender.tab);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !SUPERVISOR_SUBMISSION_TYPES.has(msg.type)) return false;
  if (!trustedSubmissionSender(sender)) {
    sendResponse(supervisorError("SUBMISSION_FORBIDDEN", "提交只允许来自已授权 ChatGPT 页面"));
    return false;
  }
  if (msg.type === "cf:supervisor_submission_state") {
    if (Object.keys(msg).sort().join("|") !== "message_id|session_id|type" ||
        !validControlText(msg.session_id) || !validControlText(msg.message_id) ||
        sender.tab.url && (() => { try { return new URL(sender.tab.url).pathname.includes("/bridge_client.html"); } catch (_) { return true; } })()) {
      sendResponse(supervisorError("INVALID_REQUEST", "提交状态请求格式无效"));
      return false;
    }
    exactRecordForMessage(sender.tab.id, msg.session_id, msg.message_id)
      .then((record) => sendResponse({ ok: true, data: {
        record: record && !record.invalid ? publicSubmissionRecord(record) : null,
        invalid: !!(record && record.invalid),
        error: record && record.invalid ? record.error : null,
      } }))
      .catch(() => sendResponse(supervisorError("INTERNAL_ERROR", "无法读取提交状态")));
    return true;
  }
  const fields = Object.keys(msg).sort().join("|");
  const allowedFields = ["confirmation_token|message_id|mode|session_id|type", "message_id|mode|session_id|type"];
  if (!allowedFields.includes(fields) || !["manual", "manual_full_override"].includes(msg.mode) ||
      !validControlText(msg.session_id) || !validControlText(msg.message_id) ||
      ("confirmation_token" in msg && !validControlText(msg.confirmation_token)) ||
      (sender.tab.url && (() => { try { return new URL(sender.tab.url).pathname.includes("/bridge_client.html"); } catch (_) { return true; } })())) {
    sendResponse(supervisorError("INVALID_REQUEST", "提交请求格式无效"));
    return false;
  }
  submitSupervisorMessage(sender.tab.id, msg.session_id, msg.message_id, msg.mode, msg.confirmation_token || null)
    .then(sendResponse).catch(() => sendResponse(supervisorError("INTERNAL_ERROR", "Supervisor 提交失败")));
  return true;
});

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

// Legacy Adapter runtime calls remain read/transport compatibility only. The
// C-01 content UI never supplies an Envelope or Endpoint identity through them.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "cf:submit_envelope") {
    // Reject webpage-originated Envelope injection. Only extension-owned pages
    // without a tab may use this compatibility path.
    if (!isTrustedExtensionPage(sender)) {
      sendResponse({ ok: false, status: "failed", error: { code: "SUBMISSION_FORBIDDEN", message: "Envelope submission is background-owned" } });
      return false;
    }
    handleSubmitEnvelope(msg.envelope).then(sendResponse);
    return true;
  }
  if (msg && msg.type === "cf:resolve_endpoint") {
    if (!isTrustedExtensionPage(sender)) {
      sendResponse({ ok: false, error: { code: "ENDPOINT_LOOKUP_FORBIDDEN", message: "Endpoint lookup is not available to webpages" } });
      return false;
    }
    handleResolveEndpoint(msg.session_id).then(sendResponse);
    return true;
  }
  return false;
});
