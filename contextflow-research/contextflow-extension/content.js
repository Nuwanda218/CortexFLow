/* ContextFlow ChatGPT Adapter v0.6.0 — content script (CF-DEV-001 + CF-ADAPTER-001)
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
 * v0.6.0 (CF-ADAPTER-001) adds:
 *   - a paper-plane button next to each Assistant action bar;
 *   - standard submission envelope construction (native IDs only, never faked);
 *   - reliable submit -> bridge -> external receiver acknowledgement state machine;
 *   - chrome.storage.local recovery of confirmed submissions across reloads;
 *   - reverse send handler (external receiver -> prepare + send + verify).
 *
 * The script never sends anything by itself, never touches credentials, and
 * never calls ChatGPT internal APIs. It only reads the rendered DOM, injects
 * one extra button, and uses the existing local bridge for transport.
 */
(() => {
  "use strict";

  const VERSION = "0.7.0";
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

  function displayName(value) {
    if (typeof value !== "string") return null;
    const normalized = value.replace(/\s+/g, " ").trim();
    if (!normalized || normalized.length > 200 || /[\p{C}]/u.test(normalized)) return null;
    return normalized;
  }

  function getSupervisorDisplay() {
    const projectSegment = location.pathname.match(/^\/g\/(g-p-[^/]+)\/c\//);
    const projectId = projectSegment && projectSegment[1].match(/^(g-p-[0-9a-fA-F]{32})/);
    let projectName = null;
    if (projectId) {
      const link = document.querySelector(`a[href="/g/${projectId[1]}/project"]`);
      projectName = displayName(link && (link.getAttribute("aria-label") || link.textContent));
    }
    return {
      project_name: projectName,
      conversation_name: displayName(document.title),
    };
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

  function parseTurnKey(key) {
    const match = String(key || "").match(/^(.*):(\d+):(user|assistant)$/);
    if (!match) return { turn_group_id: null, turn_position: null };
    return { turn_group_id: match[1], turn_position: Number(match[2]) };
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
      const ids = idsOf(turn);
      const markdown = extractMarkdown(contentRoot(turn));
      const turnKey = turn.getAttribute("data-chatgpt-search-unit-key");
      const turnParts = parseTurnKey(turnKey);
      return {
        order: i,
        role: roleOf(turn),
        turn_key: turnKey,
        turn_group_id: turnParts.turn_group_id,
        turn_position: turnParts.turn_position,
        message_id: ids[0] || null,
        message_ids: ids,
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
      // Identity and complete normalized content are both part of the scan
      // signature. Equal-length rewrites and DOM node replacement must report.
      snap.messages.map((m) => [m.order, m.role, m.message_id, m.status, m.content]),
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
    observer = new MutationObserver(() => {
      scheduleReport("mutation");
      scheduleButtons();
    });
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

  /* ============ 7.5 composer (CF-DEV-004: prepare only — NEVER sends) ============ */
  // ChatGPT can render more than one ProseMirror editor on the same page: the
  // chat composer ("询问 ChatGPT") and the writing/canvas editor ("开始写作").
  // Prefer the chat composer and never treat the writing editor as the composer.
  const COMPOSER_SELECTORS = [
    "#prompt-textarea",
    'div.ProseMirror[contenteditable="true"][data-composer-markdown]',
    'form div.ProseMirror[contenteditable="true"]',
    'form [contenteditable="true"][role="textbox"]',
    'form textarea',
    '[contenteditable="true"][role="textbox"][aria-label*="ChatGPT"]',
    'div.ProseMirror[contenteditable="true"]',
    'form [contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
  ];

  function isWritingEditor(el) {
    const label = (el.getAttribute("aria-label") || "").trim();
    if (label === "开始写作" || /开始写作|Start writing/i.test(label)) return true;
    if (el.closest('[data-testid*="writing"], [data-testid*="canvas"], [data-writing-editor]')) return true;
    return false;
  }

  function findComposer() {
    for (const sel of COMPOSER_SELECTORS) {
      for (const el of document.querySelectorAll(sel)) {
        if (el.getClientRects().length === 0) continue;
        if (isWritingEditor(el)) continue;
        return el;
      }
    }
    return null;
  }

  function composerTextOf(el) {
    if (!el) return "";
    return (el.innerText || "").replace(/\u00a0/g, " ").replace(/\r\n/g, "\n");
  }

  function readComposerState() {
    const el = findComposer();
    if (!el) {
      return { found: false, editable: false, text: "", text_length: 0, empty: true, selector: null, tag: null };
    }
    const text = composerTextOf(el);
    return {
      found: true,
      editable: el.isContentEditable === true && el.getAttribute("aria-disabled") !== "true",
      text,
      text_length: text.length,
      empty: text.trim().length === 0,
      selector: el.id ? "#" + el.id : (el.className ? "." + String(el.className).split(/\s+/)[0] : el.tagName.toLowerCase()),
      tag: el.tagName.toLowerCase(),
      aria_label: el.getAttribute("aria-label") || null,
    };
  }

  function focusComposer(el) {
    el.focus();
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function clearComposer(el) {
    focusComposer(el);
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand("delete", false, null);
  }

  function insertViaPaste(el, text) {
    const dt = new DataTransfer();
    dt.setData("text/plain", text);
    const ev = new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
  }

  function insertViaExecCommand(el, text) {
    focusComposer(el);
    return document.execCommand("insertText", false, text);
  }

  // Editors differ in how block elements map to newlines (ProseMirror <p> vs
  // plain contenteditable), so compare non-empty trimmed lines in order.
  // Blank-line fidelity is intentionally not asserted in v1.
  function normalizeForCompare(s) {
    return String(s || "")
      .replace(/\u00a0/g, " ")
      .replace(/\r\n/g, "\n")
      .split("\n")
      .map((line) => line.replace(/\s+$/g, "").replace(/^\s+/g, ""))
      .filter((line) => line.length > 0)
      .join("\n");
  }

  // Fill the composer with text. Never triggers send. Verifies by reading back.
  function prepareMessage(text, force) {
    const el = findComposer();
    if (!el) return { ok: false, error: { code: "COMPOSER_UNAVAILABLE", message: "composer not found" } };
    const before = readComposerState();
    if (!before.editable) return { ok: false, error: { code: "COMPOSER_UNAVAILABLE", message: "composer is not editable" } };
    const hadDraft = !before.empty;
    if (hadDraft && !force) {
      return { ok: false, error: { code: "DRAFT_PRESENT", message: "composer already has " + before.text_length + " chars; pass force=true to replace" } };
    }
    if (hadDraft) clearComposer(el);

    let method = "paste";
    insertViaPaste(el, text);
    let after = readComposerState();
    if (normalizeForCompare(after.text) !== normalizeForCompare(text)) {
      method = "execCommand";
      clearComposer(el);
      insertViaExecCommand(el, text);
      after = readComposerState();
    }
    const verified = normalizeForCompare(after.text) === normalizeForCompare(text);
    if (!verified) {
      const got = (after.text || "").slice(0, 160);
      return {
        ok: false,
        error: { code: "COMPOSER_MISMATCH", message: "text did not land correctly (method=" + method + ", got " + after.text_length + " chars: " + JSON.stringify(got) + ")" },
        data: { method, composer: after },
      };
    }
    return {
      ok: true,
      data: {
        prepared: true,
        sent: false,
        verified: true,
        method,
        draft_replaced: hadDraft,
        composer_text_length: after.text_length,
        composer_empty: after.empty,
        selector: after.selector,
      },
    };
  }

  /* ============ 7.6 send (CF-DEV-004 experiment C: explicit, verified) ============ */
  function findSendButton() {
    const selectors = [
      '[data-testid="send-button"]',
      'button[aria-label*="发送"]',
      'button[aria-label*="Send"]',
      'form button[type="submit"]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.getClientRects().length > 0 && !el.disabled) return el;
    }
    return null;
  }

  function dispatchEnter(el, opts) {
    const base = {
      key: "Enter", code: "Enter", keyCode: 13, which: 13,
      bubbles: true, cancelable: true, composed: true, ...opts,
    };
    el.dispatchEvent(new KeyboardEvent("keydown", base));
    el.dispatchEvent(new KeyboardEvent("keypress", base));
    el.dispatchEvent(new KeyboardEvent("keyup", base));
  }

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  // Send whatever is currently in the composer. Never retries blindly: the
  // caller must treat SEND_UNVERIFIED as "unknown outcome, do not resend".
  async function sendCurrentComposer() {
    const el = findComposer();
    if (!el) return { ok: false, error: { code: "COMPOSER_UNAVAILABLE", message: "composer not found" } };
    const before = readComposerState();
    if (before.empty) return { ok: false, error: { code: "COMPOSER_EMPTY", message: "composer is empty; nothing to send" } };
    const turnsBefore = findTurns().length;
    const sessionBefore = getSession().session_id;

    const waitSent = async (method) => {
      // Short in-page check only: ChatGPT may reload the page right after send,
      // which destroys this content script. The authoritative verification is
      // done by the caller via a fresh snapshot.
      for (let i = 0; i < 5; i++) {
        await sleep(200);
        const turnsNow = findTurns().length;
        const comp = readComposerState();
        if (turnsNow > turnsBefore || comp.empty) {
          const sessionAfter = getSession().session_id;
          if (sessionBefore !== sessionAfter) {
            return { ok: false, error: { code: "SESSION_CHANGED", message: "session changed during send" } };
          }
          return { ok: true, data: {
            sent: true, verified: true, method,
            composer_empty: comp.empty,
            message_count_delta: turnsNow - turnsBefore,
            session_id: sessionAfter,
          } };
        }
      }
      return null;
    };

    // 1) keyboard path: Shift+Enter (the user's remapped send key).
    focusComposer(el);
    dispatchEnter(el, { shiftKey: true });
    let r = await waitSent("shift+enter");
    if (r) return r;

    // 2) Ctrl+Enter (the other remapped send key).
    focusComposer(el);
    dispatchEnter(el, { ctrlKey: true });
    r = await waitSent("ctrl+enter");
    if (r) return r;

    // 3) fallback: click the send button.
    const btn = findSendButton();
    if (btn) {
      btn.click();
      r = await waitSent("button");
      if (r) return r;
    }

    return {
      ok: false,
      error: { code: "SEND_UNVERIFIED", message: "send could not be verified; outcome unknown — do NOT retry blindly" },
      data: { composer_empty: readComposerState().empty, message_count_delta: findTurns().length - turnsBefore },
    };
  }

  /* ============ 7.7 paper-plane submission (CF-ADAPTER-001) ============ */

  const SUBMIT_TOAST_MS = 2200;
  const SUBMIT_BUTTON_ATTR = "data-cf-submit-button";
  const SUBMIT_STATE_ATTR = "data-cf-submit-state";
  const SUBMIT_KEY_ATTR = "data-cf-submit-key";
  const SUBMIT_TOOLBAR_ATTR = "data-cf-submit-toolbar";
  const SUBMIT_STYLE_ID = "cf-submit-style";

  const submissionRecords = new Map(); // transient button-state cache from background
  let submissionRecordsLoaded = true;
  let buttonTimer = null;

  const ICON_PAPER_PLANE =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="M3 11.5 21 3l-8.5 18-2.2-7.3L3 11.5Z" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linejoin="round"/><path d="m10.3 13.7 4.2-4.2" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round"/></svg>';
  const ICON_CHECK =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="m4.5 12.5 5 5 10-11" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_ALERT =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="M12 4 3.5 20h17L12 4Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>' +
    '<path d="M12 10v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' +
    '<circle cx="12" cy="17" r="1" fill="currentColor"/></svg>';
  const ICON_QUESTION =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/>' +
    '<path d="M9.6 9.2a2.5 2.5 0 1 1 3.4 2.4c-.7.3-1 .8-1 1.5v.4" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="17" r="1" fill="currentColor"/></svg>';

  function ensureSubmitStyles() {
    if (document.getElementById(SUBMIT_STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = SUBMIT_STYLE_ID;
    style.textContent = [
      ".cf-submit-toolbar{display:flex;align-items:center;justify-content:flex-end;gap:6px;margin-top:4px;}",
      ".cf-submit-btn{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(127,127,127,.35);",
      "background:transparent;color:inherit;border-radius:999px;padding:4px 9px;font-size:12px;line-height:1;",
      "cursor:pointer;opacity:.82;font-family:inherit;}",
      ".cf-submit-btn:hover{opacity:1;background:rgba(127,127,127,.12);}",
      ".cf-submit-btn:focus-visible{outline:2px solid currentColor;outline-offset:2px;}",
      ".cf-submit-btn[disabled]{cursor:not-allowed;opacity:.45;}",
      '.cf-submit-btn[data-cf-submit-state="accepted"]{color:#0a7f3f;border-color:rgba(10,127,63,.55);opacity:1;}',
      '.cf-submit-btn[data-cf-submit-state="failed"],.cf-submit-btn[data-cf-submit-state="invalid"]{color:#b3261e;border-color:rgba(179,38,30,.55);opacity:1;}',
      '.cf-submit-btn[data-cf-submit-state="unknown"]{color:#8a6d00;border-color:rgba(138,109,0,.55);opacity:1;}',
      ".cf-submit-spinner{width:12px;height:12px;border:2px solid currentColor;border-right-color:transparent;",
      "border-radius:50%;animation:cf-submit-spin .8s linear infinite;}",
      "@keyframes cf-submit-spin{to{transform:rotate(360deg);}}",
      ".cf-submit-label{white-space:nowrap;}",
    ].join("");
    (document.head || document.documentElement).appendChild(style);
  }

  function normalizeError(err) {
    if (err && typeof err === "object" && err.code) {
      return { code: String(err.code), message: String(err.message || err.code) };
    }
    return { code: "INTERNAL_ERROR", message: String(err) };
  }

  function messageIdentity(turn) {
    const session = getSession();
    const ids = idsOf(turn);
    const turnKey = turn.getAttribute("data-chatgpt-search-unit-key");
    const parts = parseTurnKey(turnKey);
    let userMessageId = null;
    if (parts.turn_group_id) {
      const users = findTurns().filter((candidate) => {
        if (candidate === turn || roleOf(candidate) !== "user") return false;
        return parseTurnKey(candidate.getAttribute("data-chatgpt-search-unit-key")).turn_group_id
          === parts.turn_group_id;
      });
      if (users.length === 1) {
        const userIds = idsOf(users[0]);
        userMessageId = userIds[0] || null;
      }
    }
    return {
      session_id: session.session_id,
      session_kind: session.kind,
      message_id: ids[0] || null,
      message_ids: ids,
      user_message_id: userMessageId,
      turn_group_id: parts.turn_group_id,
      turn_position: parts.turn_position,
      turn_key: turnKey,
    };
  }

  function submissionKeyOf(identity) {
    if (!identity || !identity.session_id || !identity.message_id) return null;
    return identity.session_id + ":" + identity.message_id;
  }

  function findActionBar(turn, root) {
    const selectors = [
      'button[aria-label="复制"]', 'button[aria-label="Copy"]',
      'button[aria-label*="复制"]', 'button[aria-label*="Copy"]',
      'button[data-testid*="copy-turn-action"]', 'button[data-testid*="copy"]',
      'button[aria-label*="评价"]', 'button[aria-label*="Good response"]',
      'button[aria-label*="重新生成"]', 'button[aria-label*="Regenerate"]',
    ];
    for (const selector of selectors) {
      const buttons = Array.from(turn.querySelectorAll(selector)).filter((button) => {
        if (button.closest("[" + SUBMIT_TOOLBAR_ATTR + "]")) return false;
        if (button.closest("[data-code-block]")) return false;
        return button.getClientRects().length > 0;
      });
      if (!buttons.length) continue;
      let element = buttons[0];
      for (let depth = 0; depth < 5 && element && element !== turn; depth += 1) {
        const parent = element.parentElement;
        if (!parent || parent === turn) break;
        if (parent.querySelectorAll("button").length >= 2) return parent;
        element = parent;
      }
      return buttons[0].parentElement || buttons[0];
    }
    return null;
  }

  function isTurnGenerating(turn) {
    const assistants = findTurns().filter((candidate) => roleOf(candidate) === "assistant");
    if (assistants[assistants.length - 1] !== turn) return false;
    const stateEl = turn.querySelector("[data-talvt-turn-state]");
    const state = stateEl ? stateEl.getAttribute("data-talvt-turn-state") : null;
    return state === "in_progress" || getGenerationState().state === "generating";
  }

  function setButtonState(button, state, info) {
    const details = info || {};
    const labelText = details.label || "";
    const aria = details.ariaLabel || "提交到 ContextFlow";
    const title = details.title || "提交这条 Assistant 回复到 ContextFlow";
    // Idempotent: the MutationObserver re-applies state every ~250ms.  Rebuilding
    // innerHTML for an unchanged state makes the label/icon flicker, so bail out
    // when nothing actually changed.
    if (
      button.getAttribute(SUBMIT_STATE_ATTR) === state &&
      (button.dataset.cfLabel || "") === labelText &&
      button.title === title &&
      button.getAttribute("aria-label") === aria
    ) {
      return;
    }
    button.setAttribute(SUBMIT_STATE_ATTR, state);
    button.dataset.cfLabel = labelText;
    button.disabled = state === "submitting" || state === "disabled";
    button.setAttribute("aria-label", aria);
    button.title = title;
    button.innerHTML = "";
    if (state === "submitting") {
      const spinner = document.createElement("span");
      spinner.className = "cf-submit-spinner";
      button.appendChild(spinner);
    } else if (state === "accepted") {
      button.insertAdjacentHTML("beforeend", ICON_CHECK);
    } else if (state === "failed" || state === "invalid") {
      button.insertAdjacentHTML("beforeend", ICON_ALERT);
    } else if (state === "unknown") {
      button.insertAdjacentHTML("beforeend", ICON_QUESTION);
    } else {
      button.insertAdjacentHTML("beforeend", ICON_PAPER_PLANE);
    }
    const label = document.createElement("span");
    label.className = "cf-submit-label";
    label.textContent = labelText;
    button.appendChild(label);
    if (state === "accepted" && details.showToast) {
      button.dataset.cfToastUntil = String(Date.now() + SUBMIT_TOAST_MS);
      setTimeout(() => {
        if (button.getAttribute(SUBMIT_STATE_ATTR) !== "accepted") return;
        const current = button.querySelector(".cf-submit-label");
        if (current) current.textContent = "";
        button.dataset.cfLabel = "";
        delete button.dataset.cfToastUntil;
      }, SUBMIT_TOAST_MS);
    }
  }

  function applyRecordState(button, record, identity) {
    if (!identity.session_id) {
      setButtonState(button, "disabled", { title: "当前页面没有稳定的 ChatGPT 会话 ID", label: "不可提交" });
      return;
    }
    if (!identity.message_id) {
      setButtonState(button, "disabled", { title: "该消息缺少稳定原生消息 ID，无法提交", label: "缺少消息 ID" });
      return;
    }
    if (isTurnGenerating(identity.turn)) {
      setButtonState(button, "disabled", { title: "回复生成中，完成后可提交", label: "生成中" });
      return;
    }
    if (!record) {
      setButtonState(button, "idle", { label: "" });
      return;
    }
    if (record.status === "accepted") {
      // Re-applied on every mutation/reload: show the confirmed icon only.
      // The transient “已送达” toast is reserved for the click/ack transition;
      // keep it visible until its own timer expires instead of resetting it.
      const toastActive = Date.now() < Number(button.dataset.cfToastUntil || 0);
      setButtonState(button, "accepted", {
        label: toastActive ? "已送达" : "",
        ariaLabel: "已提交到 ContextFlow",
      });
    } else if (record.status === "submitting") {
      setButtonState(button, "submitting", { label: "提交中…" });
    } else if (record.status === "failed") {
      setButtonState(button, "failed", {
        label: "提交失败，点击重试",
        title: (record.error && record.error.message) || "提交失败",
      });
    } else {
      setButtonState(button, "unknown", {
        label: "结果未知，点击重试",
        title: (record.error && record.error.message) || "提交结果未知，可安全重试",
      });
    }
  }

  function ensureButtonForTurn(turn) {
    const identity = messageIdentity(turn);
    identity.turn = turn;
    const key = submissionKeyOf(identity);
    const existingToolbar = turn.querySelector("[" + SUBMIT_TOOLBAR_ATTR + "]");
    let toolbar = existingToolbar;
    let button = toolbar ? toolbar.querySelector("[" + SUBMIT_BUTTON_ATTR + "]") : null;

    if (!toolbar || !button) {
      if (toolbar) toolbar.remove();
      toolbar = document.createElement("div");
      toolbar.className = "cf-submit-toolbar";
      toolbar.setAttribute(SUBMIT_TOOLBAR_ATTR, "1");
      button = document.createElement("button");
      button.type = "button";
      button.className = "cf-submit-btn";
      button.setAttribute(SUBMIT_BUTTON_ATTR, "1");
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        onPaperPlaneClick(turn, button);
      });
      toolbar.appendChild(button);
      const root = contentRoot(turn);
      const actionBar = findActionBar(turn, root);
      const anchor = actionBar || root;
      anchor.insertAdjacentElement("afterend", toolbar);
      if (toolbar.getClientRects().length === 0) turn.appendChild(toolbar);
    }

    if (key) button.setAttribute(SUBMIT_KEY_ATTR, key);
    else button.removeAttribute(SUBMIT_KEY_ATTR);
    const localRecord = key ? submissionRecords.get(key) : null;
    if (localRecord && localRecord.status === "accepted") {
      applyRecordState(button, localRecord, identity);
      return;
    }
    // Avoid resetting an accepted DOM state while the background reconciliation
    // callback updates the local cache after a page reload.
    if (button.getAttribute(SUBMIT_STATE_ATTR) !== "accepted") {
      applyRecordState(button, localRecord, identity);
    }
    if (button.dataset.cfStatusPending === "1") return;
    button.dataset.cfStatusPending = "1";
    // Always reconcile with Background's payload-specific authority. A local
    // override token is only a click affordance, never a status authority.
    if (key) refreshButtonFromBackground(button, identity).catch(() => {}).finally(() => { delete button.dataset.cfStatusPending; });
  }

  function ensureActionButtons() {
    if (!submissionRecordsLoaded) return;
    ensureSubmitStyles();
    const turns = findTurns();
    document.querySelectorAll("[" + SUBMIT_TOOLBAR_ATTR + "]").forEach((toolbar) => {
      const turn = toolbar.closest("[data-chatgpt-search-unit-key]");
      if (!turn || roleOf(turn) !== "assistant") toolbar.remove();
    });
    for (const turn of turns) {
      if (roleOf(turn) !== "assistant") continue;
      try {
        ensureButtonForTurn(turn);
      } catch (err) {
        WARN("paper-plane injection failed:", String(err));
      }
    }
  }

  function scheduleButtons() {
    if (buttonTimer) return;
    buttonTimer = setTimeout(() => {
      buttonTimer = null;
      ensureActionButtons();
    }, 250);
  }

  async function requestSupervisorSubmission(identity, mode, confirmationToken) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({
          type: "cf:supervisor_submit",
          session_id: identity.session_id,
          message_id: identity.message_id,
          mode,
          ...(confirmationToken ? { confirmation_token: confirmationToken } : {}),
        }, (response) => {
          void chrome.runtime.lastError;
          resolve(response || { ok: false, error: { code: "BACKGROUND_UNAVAILABLE", message: "扩展后台无响应" } });
        });
      } catch (_err) {
        resolve({ ok: false, error: { code: "BACKGROUND_UNAVAILABLE", message: "扩展后台无响应" } });
      }
    });
  }

  async function refreshButtonFromBackground(button, identity) {
    if (!identity.session_id || !identity.message_id) return false;
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({
        type: "cf:supervisor_submission_state",
        session_id: identity.session_id,
        message_id: identity.message_id,
      }, (response) => {
        void chrome.runtime.lastError;
        const payload = response && response.ok && response.data;
        const record = payload && payload.record;
        if (record && record.status === "accepted") {
          const currentKey = submissionKeyOf(identity);
          if (currentKey) submissionRecords.set(currentKey, record);
          delete button.dataset.cfOverrideToken;
          applyRecordState(button, record, identity);
          resolve(true);
          return;
        }
        if (!record && payload && payload.invalid) {
          const currentKey = submissionKeyOf(identity);
          const accepted = currentKey && submissionRecords.get(currentKey);
          if (accepted && accepted.status === "accepted") {
            applyRecordState(button, accepted, identity);
            resolve(true);
            return;
          }
          // Preserve a pending explicit confirmation token until content changes;
          // Background will reject stale tokens against the current digest.
          setButtonState(button, "invalid", {
            label: "指令标记无效，点击确认整条提交",
            title: payload.error && payload.error.message || "指令标记不完整或存在歧义",
          });
          resolve(true);
          return;
        }
        if (!record) { resolve(false); return; }
        const key = submissionKeyOf(identity);
        if (key) submissionRecords.set(key, record);
        delete button.dataset.cfOverrideToken;
        applyRecordState(button, record, identity);
        resolve(true);
      });
    });
  }

  async function onPaperPlaneClick(turn, button) {
    const identity = messageIdentity(turn);
    identity.turn = turn;
    const key = submissionKeyOf(identity);
    if (!key) {
      applyRecordState(button, null, identity);
      return;
    }
    if (isTurnGenerating(turn)) {
      applyRecordState(button, null, identity);
      return;
    }
    const confirmationToken = button.dataset.cfOverrideToken || null;
    const mode = confirmationToken ? "manual_full_override" : "manual";
    // Invalidate any stale per-message UI cache before authoritative submit.
    submissionRecords.delete(key);
    setButtonState(button, "submitting", { label: "提交中…" });
    const result = await requestSupervisorSubmission(identity, mode, confirmationToken);
    if (result && result.error && result.error.code === "MARKER_INVALID_CONFIRMATION_REQUIRED") {
      button.dataset.cfOverrideToken = result.error.details && result.error.details.confirmation_token || "";
      setButtonState(button, "failed", {
        label: "标记无效，点击提交整条回复",
        title: result.error.message || "指令标记无效",
      });
      return;
    }
    delete button.dataset.cfOverrideToken;
    const record = result && result.data && result.data.record;
    if (record) {
      const key = submissionKeyOf(identity);
      if (key) submissionRecords.set(key, record);
      if (record.status === "accepted") {
        delete button.dataset.cfOverrideToken;
        setButtonState(button, "accepted", { label: "已送达", ariaLabel: "已提交到 ContextFlow", showToast: true });
        return;
      }
      applyRecordState(button, record, identity);
      return;
    }
    const error = result && result.error || { code: "SUBMIT_FAILED", message: "提交失败" };
    setButtonState(button, result && result.status === "unknown" ? "unknown" : "failed", {
      label: result && result.status === "unknown" ? "结果未知，点击重试" : "提交失败，点击重试",
      title: error.message,
    });
  }

  /* ============ 7.8 reverse send (external receiver -> ChatGPT) ============ */

  async function sendToEndpoint(text, force) {
    if (typeof text !== "string" || !text.trim()) {
      return { ok: false, error: { code: "INVALID_REQUEST", message: "text is empty" } };
    }
    const beforeSession = getSession().session_id;
    const baseline = readMessages();
    const baselineIds = new Set();
    for (const message of baseline) {
      for (const id of message.message_ids || []) baselineIds.add(id);
    }
    const prepared = prepareMessage(text, force === true);
    if (!prepared.ok) {
      return { ok: false, error: prepared.error, data: prepared.data };
    }
    const sent = await sendCurrentComposer();
    if (!sent.ok) {
      return {
        ok: false,
        error: sent.error,
        data: { prepared: true, sent: false, verified: false, ...(sent.data || {}) },
      };
    }
    const target = normalizeForCompare(text);
    let verified = null;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await sleep(250);
      if (getSession().session_id !== beforeSession) {
        return { ok: false, error: { code: "SESSION_CHANGED", message: "session changed during send" },
                 data: { prepared: true, sent: true, verified: false } };
      }
      for (const message of readMessages()) {
        if (message.role !== "user") continue;
        const ids = message.message_ids || [];
        if (ids.some((id) => baselineIds.has(id))) continue;
        if (normalizeForCompare(message.content).includes(target)) {
          verified = message;
          break;
        }
      }
      if (verified) break;
    }
    if (!verified) {
      return {
        ok: false,
        error: { code: "SEND_UNVERIFIED", message: "消息未在会话中出现，结果未知；请勿盲目重试" },
        data: { prepared: true, sent: true, verified: false, method: sent.data && sent.data.method },
      };
    }
    return {
      ok: true,
      data: {
        prepared: true,
        sent: true,
        verified: true,
        method: sent.data && sent.data.method,
        session_id: beforeSession,
        user_message_id: verified.message_id,
        message_ids: verified.message_ids,
      },
    };
  }

  /* ============ 8. external read requests (CF-DEV-002 / C-01) ============ */
  function supervisorOriginAllowed() {
    const origin = location.origin;
    if (origin === "https://chatgpt.com" || origin === "https://chat.openai.com") return true;
    const config = globalThis.ContextFlowRuntimeConfig;
    return !!config && Array.isArray(config.allowed_mock_origins) && config.allowed_mock_origins.includes(origin);
  }

  function buildSupervisorProbe(expectedSessionId) {
    if (!supervisorOriginAllowed()) {
      return { ok: false, error: { code: "ORIGIN_NOT_ALLOWED", message: "page Origin is not authorized for Supervisor binding" } };
    }
    const before = getSession();
    const display = getSupervisorDisplay();
    if (expectedSessionId && before.session_id !== expectedSessionId) {
      return {
        ok: false,
        error: { code: "SESSION_CHANGED", message: "current Session does not match the expected Supervisor" },
      };
    }
    const messages = buildSnapshot("supervisor_probe").messages;
    const assistants = messages
      .filter((message) => message.role === "assistant")
      .map((message) => ({
        message_id: message.message_id,
        status: message.status,
        order: message.order,
      }));
    const after = getSession();
    if (before.session_id !== after.session_id || before.kind !== after.kind) {
      return {
        ok: false,
        error: { code: "SESSION_CHANGED", message: "Session changed during Supervisor probe" },
      };
    }
    const latest = assistants.length ? assistants[assistants.length - 1] : null;
    return {
      ok: true,
      data: {
        session: {
          session_id: before.session_id,
          kind: before.kind,
          project_name: display.project_name,
          conversation_name: display.conversation_name,
        },
        generation: getGenerationState(),
        assistants,
        latest_assistant_id: latest ? latest.message_id : null,
        at: new Date().toISOString(),
      },
    };
  }

  function readSupervisorMessage(expectedSessionId, messageId) {
    if (!supervisorOriginAllowed()) {
      return { ok: false, error: { code: "ORIGIN_NOT_ALLOWED", message: "page Origin is not authorized for Supervisor submission" } };
    }
    const before = getSession();
    if (!before.session_id || before.session_id !== expectedSessionId) {
      return { ok: false, error: { code: "SESSION_CHANGED", message: "current Session does not match the expected Supervisor" } };
    }
    const matches = findTurns().filter((turn) => roleOf(turn) === "assistant" && idsOf(turn).includes(messageId));
    if (matches.length !== 1) {
      return { ok: false, error: { code: "MESSAGE_NOT_FOUND", message: "Assistant message was not found uniquely" } };
    }
    const turn = matches[0];
    const identity = messageIdentity(turn);
    if (!identity.message_id || identity.message_id !== messageId) {
      return { ok: false, error: { code: "MESSAGE_ID_UNAVAILABLE", message: "Assistant message has no stable native ID" } };
    }
    const content = extractMarkdown(contentRoot(turn));
    const after = getSession();
    if (before.session_id !== after.session_id || before.kind !== after.kind) {
      return { ok: false, error: { code: "SESSION_CHANGED", message: "Session changed while reading Assistant message" } };
    }
    return { ok: true, data: {
      session_id: before.session_id,
      message_id: identity.message_id,
      user_message_id: identity.user_message_id,
      turn_group_id: identity.turn_group_id,
      status: isTurnGenerating(turn) ? "in_progress" : "complete",
      content,
      at: new Date().toISOString(),
    } };
  }

  // Background forwards external get_snapshot requests here. The read must be
  // real-time: run buildSnapshot now, never return a cached copy. Session is
  // checked before AND after the read to detect navigation mid-read.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "cf:supervisor_probe") {
      try {
        sendResponse(buildSupervisorProbe(msg.expected_session_id || null));
      } catch (err) {
        sendResponse({ ok: false, error: { code: "PROBE_FAILED", message: "Supervisor page probe failed" } });
      }
      return false;
    }
    if (msg && msg.type === "cf:supervisor_read_message") {
      try {
        sendResponse(readSupervisorMessage(msg.expected_session_id || null, msg.message_id));
      } catch (_err) {
        sendResponse({ ok: false, error: { code: "READ_FAILED", message: "Assistant message read failed" } });
      }
      return false;
    }
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
    if (msg && msg.type === "cf:read_composer") {
      try {
        const before = getSession().session_id;
        if (msg.expected_session_id && before !== msg.expected_session_id) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "SESSION_CHANGED", message: "session changed (expected " + msg.expected_session_id + ", got " + before + ")" } });
          return false;
        }
        const state = readComposerState();
        const after = getSession().session_id;
        if (before !== after) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "SESSION_CHANGED", message: "session changed during read (" + before + " -> " + after + ")" } });
          return false;
        }
        sendResponse({ ok: true, request_id: msg.request_id, data: { session_id: before, composer: state, at: new Date().toISOString() } });
      } catch (err) {
        sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
      }
      return false;
    }

    if (msg && msg.type === "cf:send_to_endpoint") {
      (async () => {
        try {
          const before = getSession().session_id;
          if (msg.expected_session_id && before !== msg.expected_session_id) {
            sendResponse({ ok: false, request_id: msg.request_id,
              error: { code: "SESSION_CHANGED", message: "session changed (expected " + msg.expected_session_id + ", got " + before + ")" } });
            return;
          }
          const result = await sendToEndpoint(String(msg.text || ""), msg.force === true);
          const after = getSession().session_id;
          if (before !== after) {
            sendResponse({ ok: false, request_id: msg.request_id,
              error: { code: "SESSION_CHANGED", message: "session changed during send (" + before + " -> " + after + ")" } });
            return;
          }
          if (!result.ok) {
            sendResponse({ ok: false, request_id: msg.request_id, error: result.error, data: result.data });
          } else {
            sendResponse({ ok: true, request_id: msg.request_id, data: result.data });
          }
        } catch (err) {
          sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
        }
      })();
      return true; // async response
    }

    if (msg && msg.type === "cf:debug_action_bars") {
      try {
        const before = getSession().session_id;
        if (msg.expected_session_id && before !== msg.expected_session_id) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "SESSION_CHANGED", message: "session changed (expected " + msg.expected_session_id + ", got " + before + ")" } });
          return false;
        }
        const bars = findTurns().filter((turn) => roleOf(turn) === "assistant").map((turn) => {
          const root = contentRoot(turn);
          const actionBar = findActionBar(turn, root);
          const toolbar = turn.querySelector("[" + SUBMIT_TOOLBAR_ATTR + "]");
          const button = turn.querySelector("[" + SUBMIT_BUTTON_ATTR + "]");
          return {
            turn_key: turn.getAttribute("data-chatgpt-search-unit-key"),
            message_ids: idsOf(turn),
            content_root_tag: root.tagName,
            content_root_class: String(root.className || "").slice(0, 160),
            root_children: Array.from(root.children).slice(0, 8).map((child) => ({
              tag: child.tagName,
              class: String(child.className || "").slice(0, 120),
              buttons: child.querySelectorAll("button").length,
            })),
            turn_buttons: Array.from(turn.querySelectorAll("button")).slice(0, 20).map((b) => ({
              aria: b.getAttribute("aria-label"),
              testid: b.getAttribute("data-testid"),
              in_root: root.contains(b),
              visible: b.getClientRects().length > 0,
              parent_class: String((b.parentElement && b.parentElement.className) || "").slice(0, 120),
              grandparent_class: String((b.parentElement && b.parentElement.parentElement
                && b.parentElement.parentElement.className) || "").slice(0, 120),
            })),
            action_bar_found: !!actionBar,
            action_bar_tag: actionBar ? actionBar.tagName : null,
            action_bar_class: actionBar ? String(actionBar.className || "").slice(0, 160) : null,
            action_bar_buttons: actionBar
              ? Array.from(actionBar.querySelectorAll("button")).map((b) =>
                  b.getAttribute("aria-label") || b.getAttribute("data-testid") || (b.textContent || "").trim().slice(0, 24))
              : [],
            toolbar_present: !!toolbar,
            toolbar_visible: toolbar ? toolbar.getClientRects().length > 0 : false,
            button_state: button ? button.getAttribute(SUBMIT_STATE_ATTR) : null,
            button_key: button ? button.getAttribute(SUBMIT_KEY_ATTR) : null,
          };
        });
        sendResponse({ ok: true, request_id: msg.request_id, data: { session: getSession(), bars } });
      } catch (err) {
        sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
      }
      return false;
    }

    if (msg && msg.type === "cf:click_submit_button") {
      try {
        const before = getSession().session_id;
        if (msg.expected_session_id && before !== msg.expected_session_id) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "SESSION_CHANGED", message: "session changed (expected " + msg.expected_session_id + ", got " + before + ")" } });
          return false;
        }
        const messageId = String(msg.message_id || "");
        const turn = findTurns().find((candidate) =>
          roleOf(candidate) === "assistant" && idsOf(candidate).includes(messageId));
        if (!turn) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "MESSAGE_NOT_FOUND", message: "no Assistant turn with message id " + messageId } });
          return false;
        }
        const button = turn.querySelector("[" + SUBMIT_BUTTON_ATTR + "]");
        if (!button) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "BUTTON_NOT_FOUND", message: "paper-plane button is not injected yet" } });
          return false;
        }
        if (button.disabled) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "BUTTON_DISABLED", message: button.title || "button is disabled" } });
          return false;
        }
        button.click();
        sendResponse({ ok: true, request_id: msg.request_id, data: {
          clicked: true,
          message_id: messageId,
          key: button.getAttribute(SUBMIT_KEY_ATTR),
          state: button.getAttribute(SUBMIT_STATE_ATTR),
        } });
      } catch (err) {
        sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
      }
      return false;
    }

    if (msg && msg.type === "cf:prepare_message") {
      try {
        const before = getSession().session_id;
        if (msg.expected_session_id && before !== msg.expected_session_id) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "SESSION_CHANGED", message: "session changed (expected " + msg.expected_session_id + ", got " + before + ")" } });
          return false;
        }
        const result = prepareMessage(String(msg.text || ""), msg.force === true);
        const after = getSession().session_id;
        if (before !== after) {
          sendResponse({ ok: false, request_id: msg.request_id,
            error: { code: "SESSION_CHANGED", message: "session changed during prepare (" + before + " -> " + after + ")" } });
          return false;
        }
        if (!result.ok) {
          sendResponse({ ok: false, request_id: msg.request_id, error: result.error, data: result.data });
        } else {
          sendResponse({ ok: true, request_id: msg.request_id, data: { session_id: before, ...result.data } });
        }
      } catch (err) {
        sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
      }
      return false;
    }

    if (msg && msg.type === "cf:send_message") {
      (async () => {
        try {
          const before = getSession().session_id;
          if (msg.expected_session_id && before !== msg.expected_session_id) {
            sendResponse({ ok: false, request_id: msg.request_id,
              error: { code: "SESSION_CHANGED", message: "session changed (expected " + msg.expected_session_id + ", got " + before + ")" } });
            return;
          }
          const result = await sendCurrentComposer();
          if (!result.ok) sendResponse({ ok: false, request_id: msg.request_id, error: result.error, data: result.data });
          else sendResponse({ ok: true, request_id: msg.request_id, data: result.data });
        } catch (err) {
          sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
        }
      })();
      return true; // async response
    }

    if (msg && msg.type === "cf:clear_composer") {
      try {
        const before = getSession().session_id;
        const el = findComposer();
        if (!el) {
          sendResponse({ ok: false, request_id: msg.request_id, error: { code: "COMPOSER_UNAVAILABLE", message: "composer not found" } });
          return false;
        }
        clearComposer(el);
        const state = readComposerState();
        sendResponse({ ok: true, request_id: msg.request_id, data: { session_id: before, cleared: state.empty, composer: state } });
      } catch (err) {
        sendResponse({ ok: false, request_id: msg.request_id, error: { code: "INTERNAL_ERROR", message: String(err) } });
      }
      return false;
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
    readComposer: readComposerState,
    prepareMessage,
    sendCurrentComposer,
    // CF-ADAPTER-001 debug helpers (console only; not used by the UI).
    ensureActionButtons,
    messageIdentity,
    readSupervisorMessage,
    sendToEndpoint,
  };

  /* ============ 10. bridge relay fallback (127.0.0.1 pages) ============
   *
   * bridge_relay.js is the dedicated relay content script, but in some Edge
   * profiles a reloaded unpacked extension fails to register the second
   * content_scripts entry while the first one keeps working.  Installing the
   * same relay from content.js makes the bridge page self-sufficient.  Both
   * scripts share one isolated world, so the __cfBridgeRelay guard prevents
   * double registration.
   */
  function installBridgeRelay() {
    if (window.__cfBridgeRelay) return;
    window.__cfBridgeRelay = true;
    try { document.documentElement.setAttribute("data-cf-relay", "ready"); } catch (err) { /* ignore */ }

    function relayRequest(detail) {
      return new Promise((resolve) => {
        const requestId = detail.request_id || String(Date.now()) + "-" + Math.random().toString(36).slice(2);
        const resultEvent = detail.result_event;
        const handler = (ev) => {
          if (!ev.detail || ev.detail.request_id !== requestId) return;
          window.removeEventListener(resultEvent, handler);
          clearTimeout(guard);
          resolve(ev.detail.result);
        };
        const guard = setTimeout(() => {
          window.removeEventListener(resultEvent, handler);
          resolve({ ok: false, status: "unknown", error: { code: "RELAY_TIMEOUT", message: "page did not answer within 20s" } });
        }, 20000);
        window.addEventListener(resultEvent, handler);
        window.dispatchEvent(new CustomEvent(detail.request_event, {
          detail: Object.assign({}, detail.payload, { request_id: requestId }),
        }));
      });
    }

    window.addEventListener("cf:bridge_request", async (ev) => {
      const detail = ev.detail || {};
      const requestId = detail.request_id || String(Date.now());
      try {
        const resp = await chrome.runtime.sendMessage({ type: "cf:bridge_request", request: { ...detail.request, id: requestId } });
        window.dispatchEvent(new CustomEvent("cf:bridge_result", { detail: { request_id: requestId, result: resp } }));
      } catch (err) {
        window.dispatchEvent(new CustomEvent("cf:bridge_result", {
          detail: { request_id: requestId, result: { id: requestId, type: "result", success: false, error: { code: "INTERNAL_ERROR", message: String(err) } } },
        }));
      }
    });

    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (msg && msg.type === "cf:bridge_ping") {
        sendResponse({ ok: true, url: location.href, at: Date.now() });
        return false;
      }
      if (msg && msg.type === "cf:bridge_submit") {
        relayRequest({
          request_event: "cf:submit_envelope",
          result_event: "cf:submit_result",
          payload: { envelope: msg.envelope, submission_id: msg.envelope && msg.envelope.submission_id },
        }).then(sendResponse);
        return true;
      }
      if (msg && msg.type === "cf:bridge_endpoint_lookup") {
        relayRequest({
          request_event: "cf:endpoint_lookup",
          result_event: "cf:endpoint_lookup_result",
          payload: { session_id: msg.session_id },
        }).then(sendResponse);
        return true;
      }
      if (msg && msg.type === "cf:bridge_control") {
        relayRequest({
          request_event: "cf:supervisor_control_request",
          result_event: "cf:supervisor_control_result",
          payload: { action: msg.action, params: msg.params },
        }).then(sendResponse);
        return true;
      }
      return false;
    });
    LOG("bridge relay installed from content.js");
  }

  if (location.hostname === "127.0.0.1" || location.hostname === "localhost") {
    installBridgeRelay();
  }

  /* ============ 9. lifecycle ============ */

  function boot() {
    startObserver();
    report("document_idle");
    heartbeatTimer = setInterval(() => {
      scheduleReport("heartbeat");
      scheduleButtons();
    }, HEARTBEAT_MS);
    ensureActionButtons();
    LOG("unified submission buttons ready");
    LOG(`content v${VERSION} ready on ${location.href}`);
  }

  function shutdown() {
    destroyed = true;
    stopObserver();
    if (reportTimer) { clearTimeout(reportTimer); reportTimer = null; }
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    if (buttonTimer) { clearTimeout(buttonTimer); buttonTimer = null; }
  }

  window.addEventListener("pagehide", shutdown, { once: true });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
