/* ContextFlow ChatGPT Adapter v1 — content script (CF-DEV-001)
 *
 * Read-only adapter for the real ChatGPT page.
 * All selectors below were verified against the live page (2026-11-05):
 *   - turn container:  [data-chatgpt-search-unit-key] ending with :user / :assistant
 *   - role:            h4[data-conversation-role] (fallback: turn key suffix)
 *   - message ids:     data-chatgpt-search-message-ids (space separated, may repeat)
 *   - content root:    [data-dil-message-id] / [data-user-message-bubble="true"]
 *   - markdown comps:  [data-d-component] = text | title | blockquote | table | box
 *   - code blocks:     [data-code-block="true"], header [data-markdown-copy="exclude"]
 *   - generation:      [data-talvt-turn-state] = in_progress | complete + stop button
 *
 * The script never sends anything, never touches credentials, and never calls
 * ChatGPT internal APIs. It only reads the rendered DOM.
 */
(() => {
  "use strict";

  const VERSION = "0.2.0";
  const LOG = (...a) => console.log("[ContextFlow:cs]", ...a);
  const WARN = (...a) => console.warn("[ContextFlow:cs]", ...a);

  const REPORT_DEBOUNCE_MS = 400;   // batch bursts of streaming mutations
  const HEARTBEAT_MS = 10000;       // periodic re-report even without mutations

  let observer = null;
  let reportTimer = null;
  let heartbeatTimer = null;
  let lastSignature = "";
  let destroyed = false;

  /* ============ 1. session identification ============ */

  function getSession() {
    const url = location.href;
    const out = { session_id: null, kind: "unknown", url, source: null, candidates: {}, notes: [] };

    const sel = document.querySelector("[data-chatgpt-selection-conversation-id]");
    if (sel) out.candidates.selection = sel.getAttribute("data-chatgpt-selection-conversation-id");
    const above = document.querySelector("[data-above-composer-conversation-id]");
    if (above) out.candidates.above_composer = above.getAttribute("data-above-composer-conversation-id");

    let m = url.match(/\/g\/([^/]+)\/c\/([0-9a-fA-F-]{8,})/);
    if (m) { out.kind = "project"; out.session_id = m[2]; out.source = "url:project_chat"; }
    else if (/\/g\/[^/]+\/c\//.test(url)) {
      // e.g. /c/local-chatgpt%3A<uuid> — a draft id, NOT a server conversation id.
      out.kind = "draft"; out.source = "url:draft";
      out.notes.push("url_conversation_id_is_local_draft; session_id unknown until first reply");
    }
    else if ((m = url.match(/\/c\/([0-9a-fA-F-]{8,})/))) { out.kind = "chat"; out.session_id = m[1]; out.source = "url:chat"; }
    else if (/\/g\/([^/]+)\/project/.test(url)) { out.kind = "project"; out.source = "url:project_page"; }
    else if (/^https:\/\/chatgpt\.com\/?$/.test(url)) { out.kind = "new_chat"; out.source = "url:root"; }

    // v1.0.1: no DOM fallback for session_id. The above-composer/selection attrs
    // keep the PREVIOUS conversation id on project/draft pages, which produced
    // stale wrong sessions (observed live). Candidates stay for diagnostics only.
    return out;
  }

  /* ============ 2. turn location ============ */

  function findTurns() {
    return Array.from(document.querySelectorAll("[data-chatgpt-search-unit-key]")).filter((el) => {
      if (el.getClientRects().length === 0) return false;                 // hidden (e.g. search overlay)
      const key = el.getAttribute("data-chatgpt-search-unit-key") || "";
      if (!/:(user|assistant)$/.test(key)) return false;                  // turn containers only
      return !(el.parentElement && el.parentElement.closest("[data-chatgpt-search-unit-key]")); // de-nest
    });
  }

  function roleOf(turn) {
    const h = turn.querySelector("h4[data-conversation-role]");
    if (h) return h.getAttribute("data-conversation-role");
    const m = (turn.getAttribute("data-chatgpt-search-unit-key") || "").match(/:(user|assistant)$/);
    return m ? m[1] : null;
  }

  function idsOf(turn) {
    const ids = [];
    (turn.getAttribute("data-chatgpt-search-message-ids") || "")
      .split(/\s+/).filter(Boolean).forEach((x) => ids.push(x));
    const dil = turn.querySelector("[data-dil-message-id]");
    if (dil) ids.push(dil.getAttribute("data-dil-message-id"));
    return Array.from(new Set(ids));
  }

  /* ============ 3. content extraction (DOM -> Markdown) ============ */

  function contentRoot(turn) {
    return turn.querySelector("[data-dil-message-id]") ||
           turn.querySelector("[data-user-message-bubble='true']") || turn;
  }

  function handleCodeBlock(el, parts) {
    const header = el.querySelector('[data-markdown-copy="exclude"]');
    const lang = header ? header.innerText.trim().split("\n")[0].toLowerCase() : "";
    const pre = el.querySelector("pre");
    const codeEl = pre ? (pre.querySelector("code") || pre) : null;
    let code = codeEl ? codeEl.innerText : "";
    if (!code) {
      const clone = el.cloneNode(true);
      clone.querySelectorAll('[data-markdown-copy="exclude"]').forEach((n) => n.remove());
      code = clone.innerText;
    }
    parts.push("```" + lang + "\n" + code.replace(/\n$/, "") + "\n```");
  }

  function extractMarkdown(root) {
    const parts = [];

    function walk(el) {
      if (!(el instanceof Element)) return;

      if (el.getAttribute("data-code-block") === "true") { handleCodeBlock(el, parts); return; }

      const comp = el.getAttribute("data-d-component") ||
                   el.getAttribute("data-client-defined-widget") ||
                   el.getAttribute("data-d-client-defined-widget");
      if (comp === "code_block") { handleCodeBlock(el, parts); return; }
      if (comp === "text") { const t = el.innerText.trim(); if (t) parts.push(t); return; }
      if (comp === "title") {
        const tag = el.tagName.toLowerCase();
        const level = tag === "h1" ? 1 : tag === "h2" ? 2 : tag === "h3" ? 3 : 2;
        const t = el.innerText.trim(); if (t) parts.push("#".repeat(level) + " " + t); return;
      }
      if (comp === "blockquote") {
        const t = el.innerText.trim(); if (t) parts.push(t.split("\n").map((l) => "> " + l).join("\n")); return;
      }
      if (comp === "table") {
        const rows = Array.from(el.querySelectorAll("tr"));
        if (rows.length) {
          const lines = rows.map((tr, i) => {
            const cells = Array.from(tr.querySelectorAll("th,td")).map((c) => c.innerText.trim().replace(/\|/g, "\\|"));
            const line = "| " + cells.join(" | ") + " |";
            return i === 0 ? line + "\n|" + cells.map(() => "---").join("|") + "|" : line;
          });
          parts.push(lines.join("\n")); return;
        }
      }
      for (const child of el.children) walk(child);
    }

    walk(root);
    if (parts.length) return parts.join("\n\n").trim();
    // fallback (e.g. user bubble without component markers): drop role headers
    const clone = root.cloneNode(true);
    clone.querySelectorAll("h4[data-conversation-role]").forEach((n) => n.remove());
    return clone.innerText.trim();
  }

  /* ============ 4. generation state ============ */

  function getGenerationState() {
    const states = Array.from(document.querySelectorAll("[data-talvt-turn-state]"))
      .map((el) => el.getAttribute("data-talvt-turn-state"));
    const stopBtn = !!document.querySelector(
      'button[aria-label*="停止"], button[aria-label*="Stop"], [data-testid="stop-button"]'
    );
    const evidence = { turn_states: states, stop_button: stopBtn };
    if (stopBtn || states.includes("in_progress")) return { state: "generating", evidence };
    if (states.length && states[states.length - 1] === "complete") return { state: "completed", evidence };
    return { state: "unknown", evidence };
  }

  /* ============ 5. read messages ============ */

  function readMessages() {
    const session = getSession();
    return findTurns().map((turn, i) => {
      const markdown = extractMarkdown(contentRoot(turn));
      return {
        order: i,
        role: roleOf(turn),
        turn_key: turn.getAttribute("data-chatgpt-search-unit-key"),
        message_ids: idsOf(turn),
        session_id: session.session_id,
        content: markdown,
        text_length: markdown.length,
        status: getGenerationState().state, // turn-level approximation, refined below
      };
    });
  }

  /* ============ 6. snapshot + reporting ============ */

  function buildSnapshot(reason) {
    const session = getSession();
    const generation = getGenerationState();
    const messages = readMessages();
    // The LAST assistant turn's per-turn state attribute is authoritative for
    // "is the current reply finished"; do not mark older messages generating.
    let lastTurnState = null;
    const stateEls = document.querySelectorAll("[data-talvt-turn-state]");
    if (stateEls.length) lastTurnState = stateEls[stateEls.length - 1].getAttribute("data-talvt-turn-state");
    for (const m of messages) {
      if (m.role === "assistant" && m === messages[messages.length - 1]) {
        m.status = lastTurnState === "in_progress" ? "in_progress"
                 : lastTurnState === "complete" ? "complete"
                 : generation.state;
      } else if (m.role === "assistant") {
        m.status = "complete"; // older assistant turns are finalized once a newer turn exists
      } else {
        m.status = "final";    // user messages are immutable once rendered
      }
    }
    return { version: VERSION, at: new Date().toISOString(), reason, page: { url: location.href }, session, generation, messages };
  }

  function signatureOf(snap) {
    return JSON.stringify([
      snap.session.session_id,
      snap.generation.state,
      snap.messages.map((m) => [m.order, m.role, m.text_length, m.status]),
    ]);
  }

  function report(reason) {
    if (destroyed) return;
    let snap;
    try {
      snap = buildSnapshot(reason);
    } catch (err) {
      WARN("snapshot failed:", String(err));
      return;
    }
    const sig = signatureOf(snap);
    if (sig === lastSignature) return; // nothing meaningful changed
    lastSignature = sig;
    try {
      chrome.runtime.sendMessage({ type: "cf:state", reason, snapshot: snap }, () => {
        void chrome.runtime.lastError; // SW may be asleep; ignore
      });
    } catch (err) {
      WARN("sendMessage failed:", String(err));
    }
    LOG(`report(${reason}) session=${snap.session.session_id || "none"} ` +
        `msgs=${snap.messages.length} gen=${snap.generation.state}`);
  }

  function scheduleReport(reason) {
    if (reportTimer) return;
    reportTimer = setTimeout(() => {
      reportTimer = null;
      report(reason);
    }, REPORT_DEBOUNCE_MS);
  }

  /* ============ 7. observers ============ */

  function startObserver() {
    if (observer) observer.disconnect();
    observer = new MutationObserver(() => scheduleReport("mutation"));
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function stopObserver() {
    if (observer) { observer.disconnect(); observer = null; }
  }

  // SPA navigation: URL changes without a document reload.
  // Only the PATH matters (conversation id lives there); query-string churn
  // (?model=..., A/B params) must not trigger reports.
  let lastPath = location.pathname;
  function checkUrl() {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      LOG("SPA navigation ->", location.pathname);
      lastSignature = ""; // force a report even if signature matches
      report("spa_navigation");
    }
  }
  setInterval(checkUrl, 1000);

  /* ============ 8. external read requests (CF-DEV-002) ============ */
  // Background forwards external get_snapshot requests here. The read must be
  // real-time: run buildSnapshot now, never return a cached copy. Session is
  // checked before AND after the read to detect navigation mid-read.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.type === "cf:read_session") {
      // Lightweight fresh probe used for routing/list_sessions.
      // ChatGPT pages and mock chat pages (127.0.0.1/*/c/<uuid>) report their
      // session; other localhost pages (e.g. bridge_client.html) report null.
      const isChatGPT = /(^|\.)chatgpt\.com$/.test(location.hostname) || location.hostname === "chat.openai.com";
      const isMockChat = location.hostname === "127.0.0.1" && /^\/c\/[0-9a-fA-F-]{8,}/.test(location.pathname);
      const s = (isChatGPT || isMockChat) ? getSession() : { session_id: null, kind: "bridge_page" };
      sendResponse({ ok: true, session_id: s.session_id, kind: s.kind });
      return false; // synchronous
    }
    if (!msg || msg.type !== "cf:read_snapshot") {
      if (msg && msg.type === "cf:read_generation") {
        // CF-DEV-003: lightweight poll endpoint — reads only state attributes,
        // never parses message bodies. Used by wait_for_reply polling.
        try {
          const before = getSession().session_id;
          if (msg.expected_session_id && before !== msg.expected_session_id) {
            sendResponse({ ok: false, request_id: msg.request_id,
              error: { code: "SESSION_CHANGED", message: "session changed (expected " + msg.expected_session_id + ", got " + before + ")" } });
            return false;
          }
          const gen = getGenerationState();
          const turns = findTurns();
          const last = turns[turns.length - 1] || null;
          const lastRole = last ? roleOf(last) : null;
          let lastStatus = null;
          if (last) {
            if (lastRole === "user") lastStatus = "final";
            else {
              const el = last.querySelector("[data-talvt-turn-state]") || last.closest("[data-talvt-turn-state]");
              lastStatus = el ? el.getAttribute("data-talvt-turn-state") : "unknown";
            }
          }
          const after = getSession().session_id;
          if (before !== after) {
            sendResponse({ ok: false, request_id: msg.request_id,
              error: { code: "SESSION_CHANGED", message: "session changed during read (" + before + " -> " + after + ")" } });
            return false;
          }
          sendResponse({ ok: true, request_id: msg.request_id, data: {
            session_id: before,
            kind: getSession().kind,
            generation: gen,
            message_count: turns.length,
            last_message_id: last ? (idsOf(last)[0] || null) : null,
            last_role: lastRole,
            last_status: lastStatus,
            at: new Date().toISOString(),
          } });
        } catch (err) {
          sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
        }
        return false;
      }
      return false;
    }
    try {
      const before = getSession().session_id;
      if (msg.expected_session_id && before !== msg.expected_session_id) {
        sendResponse({
          ok: false,
          request_id: msg.request_id,
          error: { code: "SESSION_CHANGED", message: "session changed before read (expected " + msg.expected_session_id + ", got " + before + ")" },
        });
        return false;
      }
      const snap = buildSnapshot("external_request");
      const after = getSession().session_id;
      if (before !== after) {
        sendResponse({
          ok: false,
          request_id: msg.request_id,
          error: { code: "SESSION_CHANGED", message: "session changed during read (" + before + " -> " + after + ")" },
        });
        return false;
      }
      sendResponse({ ok: true, request_id: msg.request_id, snapshot: snap });
    } catch (err) {
      sendResponse({
        ok: false,
        request_id: msg.request_id,
        error: { code: "INTERNAL_ERROR", message: String(err) },
      });
    }
    return false; // synchronous response
  });

  /* ============ 9. debug entry points ============ */

  // Console: __cfAdapter.snapshot() / __cfAdapter.readMessages()
  globalThis.__cfAdapter = {
    version: VERSION,
    snapshot: (reason = "manual") => buildSnapshot(reason),
    readMessages,
    getSession,
    getGenerationState,
  };

  /* ============ 9. lifecycle ============ */

  function boot() {
    startObserver();
    report("document_idle");
    heartbeatTimer = setInterval(() => scheduleReport("heartbeat"), HEARTBEAT_MS);
    LOG(`content v${VERSION} ready on ${location.href}`);
  }

  function shutdown() {
    destroyed = true;
    stopObserver();
    if (reportTimer) { clearTimeout(reportTimer); reportTimer = null; }
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
  }

  window.addEventListener("pagehide", shutdown, { once: true });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
