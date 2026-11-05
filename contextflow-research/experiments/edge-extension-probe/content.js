/* ContextFlow Edge Probe - content script (isolated world).
 *
 * Reports a *structural* page state to the background worker:
 *   - page kind (chatgpt / mock-chat / other)
 *   - URL, title, tab-visible session reference
 *   - generation state (idle / generating) when the page exposes it
 *   - message counts by role, last message id/status
 *   - navigation signals observed in-page (MutationObserver, title changes)
 *
 * It also performs two deliberate isolation experiments:
 *   1. reads window.__mockChat directly  -> expected undefined (isolated world)
 *   2. injects a MAIN-world <script> that reads window.__mockChat and relays it
 *      through a DOM CustomEvent -> expected to work (main-world bridge pattern)
 * and counts pushState calls made by the page after patching history.pushState
 * in the isolated world -> expected 0 (the page calls its own wrapper).
 */

(() => {
  "use strict";

  const REPORT_DEBOUNCE_MS = 150;
  const HEARTBEAT_MS = 5000;

  const isolatedPushStateCalls = { count: 0 };
  const mainWorldProbe = { available: false, data: null, error: null, at: null, via: null };
  const inlineInjection = { attempted: false, ran: false, note: "inline <script> from content script" };
  let reportTimer = null;
  let lastSignature = "";

  // ---- Experiment 2a: patch history in the ISOLATED world -------------------
  try {
    const origPush = history.pushState.bind(history);
    history.pushState = function patchedPushState(...args) {
      isolatedPushStateCalls.count += 1;
      return origPush(...args);
    };
  } catch (err) {
    isolatedPushStateCalls.error = String(err);
  }

  // ---- Experiment 2b: MAIN-world bridge -------------------------------------
  // (a) Inline <script> injection: the classic trick. It is subject to the page
  //     CSP and was observed to be BLOCKED in Edge 156 even though the mock page
  //     serves no CSP header (a browser/extension-imposed CSP applied). We still
  //     attempt it once so the experiment can record the outcome.
  function tryInlineInjection() {
    inlineInjection.attempted = true;
    try {
      const script = document.createElement("script");
      script.textContent = `document.documentElement.dataset.cfInlineProbe = "ran";`;
      (document.head || document.documentElement).appendChild(script);
      script.remove();
    } catch (err) {
      inlineInjection.error = String(err);
    }
    setTimeout(() => {
      inlineInjection.ran = document.documentElement.dataset.cfInlineProbe === "ran";
      scheduleReport("inline-injection-check");
    }, 600);
  }

  // (b) Official path: ask the service worker to run chrome.scripting.executeScript
  //     with world:"MAIN". Not subject to the page CSP.
  async function probeMainWorld() {
    try {
      const r = await chrome.runtime.sendMessage({ type: "cf:main_world_probe" });
      if (r && r.ok && r.result) {
        mainWorldProbe.available = !!r.result.available;
        mainWorldProbe.data = r.result.data || null;
        mainWorldProbe.error = r.result.error || null;
        mainWorldProbe.via = "chrome.scripting.executeScript(world:MAIN)";
      } else {
        mainWorldProbe.error = r ? r.error : "no response from background";
      }
    } catch (err) {
      mainWorldProbe.error = String(err);
    }
    mainWorldProbe.at = new Date().toISOString();
    scheduleReport("main-world-probe");
  }

  // ---- Page classification --------------------------------------------------
  function pageKind() {
    const h = location.hostname;
    if (h === "chatgpt.com" || h === "chat.openai.com") return "chatgpt";
    if (h === "127.0.0.1" || h === "localhost") return "mock-chat";
    return "other";
  }

  // URL-shape based session reference. For ChatGPT this is a *hypothesis* about
  // the route, not an official stable id; the experiment records the raw shape.
  function sessionRefFromUrl() {
    const p = location.pathname;
    const chat = p.match(/\/c\/([0-9a-fA-F-]{8,})/);
    if (chat) return { kind: "chat_path", value: chat[1] };
    const projChat = p.match(/\/g\/([^/]+)\/c\/([0-9a-fA-F-]{8,})/);
    if (projChat) return { kind: "project_chat_path", project: projChat[1], value: projChat[2] };
    const proj = p.match(/\/g\/([^/]+)\/project/);
    if (proj) return { kind: "project_path", value: proj[1] };
    return { kind: "none", value: null };
  }

  function mockSession() {
    const body = document.body;
    if (!body) return null;
    const sid = body.dataset ? body.dataset.sessionId : null;
    if (sid) return { kind: "mock_body_dataset", value: sid };
    const conv = document.getElementById("conversation");
    if (conv && conv.dataset.conversationId) return { kind: "mock_conversation_dataset", value: conv.dataset.conversationId };
    return null;
  }

  function readPageState() {
    const kind = pageKind();
    const messages = Array.from(document.querySelectorAll(".message"));
    const byRole = { user: 0, assistant: 0, other: 0 };
    for (const m of messages) {
      const r = m.dataset.role;
      if (r === "user") byRole.user += 1;
      else if (r === "assistant") byRole.assistant += 1;
      else byRole.other += 1;
    }
    const last = messages[messages.length - 1] || null;
    const appState = document.body ? document.body.dataset.appState || null : null;
    return {
      kind,
      url: location.href,
      title: document.title,
      readyState: document.readyState,
      hasMainRegion: !!(document.querySelector("main") || document.getElementById("conversation")),
      session: kind === "mock-chat" ? mockSession() : sessionRefFromUrl(),
      appState,
      generating: appState === "generating",
      messages: {
        total: messages.length,
        byRole,
        lastId: last ? last.dataset.messageId || null : null,
        lastRole: last ? last.dataset.role || null : null,
        lastStatus: last ? last.dataset.status || null : null,
      },
      isolation: {
        isolatedWorldSeesPageVar: typeof window.__mockChat !== "undefined",
        isolatedPushStateCalls: isolatedPushStateCalls.count,
        inlineInjection,
        mainWorldProbe: { available: mainWorldProbe.available, data: mainWorldProbe.data, error: mainWorldProbe.error, at: mainWorldProbe.at, via: mainWorldProbe.via },
      },
      at: new Date().toISOString(),
    };
  }

  function signature(s) {
    return [s.kind, s.url, s.title, s.appState, s.messages.total, s.messages.lastId, s.messages.lastStatus, s.isolation.mainWorldProbe.available].join("|");
  }

  function scheduleReport(reason) {
    if (reportTimer) return;
    reportTimer = setTimeout(() => {
      reportTimer = null;
      const state = readPageState();
      state.reason = reason;
      const sig = signature(state);
      if (sig === lastSignature && reason !== "heartbeat" && reason !== "ping") return;
      lastSignature = sig;
      try {
        chrome.runtime.sendMessage({ type: "cf:page_state", payload: state }).catch(() => {});
      } catch (_) { /* extension context invalidated */ }
    }, REPORT_DEBOUNCE_MS);
  }

  // ---- Observers ------------------------------------------------------------
  const observer = new MutationObserver((mutations) => {
    // Ignore our own injected <script> removal noise by only reacting to
    // attribute/childList changes inside the conversation region or body state.
    for (const m of mutations) {
      if (m.type === "attributes" && (m.attributeName === "data-app-state" || m.attributeName === "data-status" || m.attributeName === "data-session-id")) {
        scheduleReport(`attr:${m.attributeName}`);
        return;
      }
      if (m.type === "childList" && m.target && (m.target.id === "conversation" || (m.target.closest && m.target.closest("#conversation")))) {
        scheduleReport("conversation-mutation");
        return;
      }
    }
  });

  function startObservers() {
    if (!document.body) return;
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-app-state", "data-status", "data-session-id"] });
    window.addEventListener("popstate", () => scheduleReport("popstate"));
    window.addEventListener("hashchange", () => scheduleReport("hashchange"));
    document.addEventListener("mockchat:navigate", () => scheduleReport("mockchat:navigate"));
    document.addEventListener("mockchat:state", () => scheduleReport("mockchat:state"));
    document.addEventListener("mockchat:reply-complete", () => scheduleReport("mockchat:reply-complete"));
    // Title changes are a cheap SPA signal that does not depend on the app's DOM.
    let lastTitle = document.title;
    setInterval(() => {
      if (document.title !== lastTitle) { lastTitle = document.title; scheduleReport("title-change"); }
    }, 1000);
  }

  // ---- Messaging ------------------------------------------------------------
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || typeof msg.type !== "string") return false;
    if (msg.type === "cf:ping") {
      sendResponse({ ok: true, nonce: msg.nonce || null, state: readPageState(), at: new Date().toISOString() });
      return true;
    }
    if (msg.type === "cf:fake_paste") {
      // "Fake clipboard": dispatch a synthetic paste event carrying a DataTransfer.
      // No OS clipboard read/write happens here.
      const ed = document.querySelector('.ProseMirror[contenteditable="true"]') || document.querySelector('[contenteditable="true"]');
      if (!ed) { sendResponse({ ok: false, error: "no contenteditable editor found" }); return true; }
      try {
        ed.focus();
        const dt = new DataTransfer();
        dt.setData("text/plain", String(msg.text || ""));
        const ev = new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true });
        const dispatched = ed.dispatchEvent(ev);
        sendResponse({
          ok: true,
          dispatched,
          defaultPrevented: ev.defaultPrevented,
          isTrusted: ev.isTrusted,
          text: ed.innerText,
          world: "isolated",
        });
      } catch (err) {
        sendResponse({ ok: false, error: String(err) });
      }
      return true;
    }
    if (msg.type === "cf:core_notice") {
      // Core -> background -> content -> DOM. Visible proof of the reverse path.
      document.querySelectorAll("#cf-core-notice").forEach((n) => n.remove());
      const banner = document.createElement("div");
      banner.id = "cf-core-notice";
      banner.dataset.payload = JSON.stringify(msg.payload || {});
      banner.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#123;color:#cfe;border:1px solid #4f8cff;border-radius:10px;padding:8px 12px;font:12px/1.4 monospace;max-width:420px;white-space:pre-wrap";
      banner.textContent = `[ContextFlow Core] ${JSON.stringify(msg.payload)}`;
      document.body.appendChild(banner);
      scheduleReport("core-notice");
      sendResponse({ ok: true });
      return true;
    }
    return false;
  });

  // ---- Boot -----------------------------------------------------------------
  function boot() {
    startObservers();
    tryInlineInjection();
    probeMainWorld();
    setInterval(probeMainWorld, 2000);
    scheduleReport("document_idle");
    setInterval(() => scheduleReport("heartbeat"), HEARTBEAT_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
