/* Mock chat page: two sessions, SPA navigation, streaming assistant replies.
 * Deliberately mimics the *shape* of a chat web app (not ChatGPT's real DOM).
 * Observable contract used by the experiments:
 *   body[data-session-id]         current session id
 *   body[data-app-state]          idle | generating
 *   #conversation[data-conversation-id]
 *   .message[data-role][data-message-id][data-status=streaming|complete]
 *   window.__mockChat             main-world-only marker (isolation test)
 */
(() => {
  "use strict";

  const SESSIONS = ["session-a", "session-b"];
  const CHUNK_MS = 120;
  const STORE_KEY = "mockchat.messages.v1";

  const el = {
    body: document.body,
    conv: document.getElementById("conversation"),
    form: document.getElementById("composer"),
    input: document.getElementById("prompt-input"),
    send: document.getElementById("send"),
    stop: document.getElementById("stop"),
    status: document.getElementById("status-indicator"),
    links: Array.from(document.querySelectorAll(".session-link")),
  };

  // ---- main-world marker: a content script in an isolated world must NOT see this.
  window.__mockChat = {
    version: "1.0.0",
    sessionId: () => document.body.dataset.sessionId,
    state: () => document.body.dataset.appState,
    messageCount: () => document.querySelectorAll(".message").length,
    pushStateCalls: 0,
    startedAt: Date.now(),
  };

  // ---- message store (sessionStorage so a reload restores the transcript) ----
  let store = loadStore();
  let streamTimer = null;
  let seq = 0;

  function loadStore() {
    try {
      const raw = sessionStorage.getItem(STORE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (_) { /* ignore */ }
    return {
      "session-a": [
        { id: "a-1", role: "user", text: "Hello from session A. Summarize the plan." },
        { id: "a-2", role: "assistant", text: "Session A assistant reply. Step 1: inspect. Step 2: report." },
      ],
      "session-b": [
        { id: "b-1", role: "user", text: "Hello from session B. Different transcript." },
        { id: "b-2", role: "assistant", text: "Session B assistant reply. Only in B." },
      ],
    };
  }
  function saveStore() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(store)); } catch (_) { /* ignore */ }
  }

  function sessionFromPath() {
    const m = location.pathname.match(/\/mock\/chat\/([^/?#]+)/);
    const id = m ? decodeURIComponent(m[1]) : "session-a";
    return SESSIONS.includes(id) ? id : "session-a";
  }

  function currentSession() { return el.body.dataset.sessionId; }

  function setAppState(state) {
    el.body.dataset.appState = state;
    el.status.dataset.state = state;
    el.status.textContent = state;
    el.send.disabled = state === "generating";
    el.stop.disabled = state !== "generating";
    document.dispatchEvent(new CustomEvent("mockchat:state", { detail: { state } }));
  }

  // ---- rendering ----
  function render() {
    const sid = currentSession();
    el.conv.dataset.conversationId = sid;
    el.conv.textContent = "";
    for (const msg of store[sid] || []) el.conv.appendChild(renderMessage(msg));
    el.links.forEach((a) => {
      if (a.dataset.sessionId === sid) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    scrollToBottom();
  }

  function renderMessage(msg) {
    const node = document.createElement("div");
    node.className = "message";
    node.dataset.role = msg.role;
    node.dataset.messageId = msg.id;
    node.dataset.status = msg.status || "complete";
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${msg.role} · ${msg.id}`;
    node.appendChild(meta);
    const body = document.createElement("div");
    body.className = "body";
    body.innerHTML = msg.html || escapeHtml(msg.text || "");
    node.appendChild(body);
    return node;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function scrollToBottom() { el.conv.scrollTop = el.conv.scrollHeight; }

  // ---- navigation ----
  function navigate(sid, { push = true } = {}) {
    if (!SESSIONS.includes(sid)) return;
    if (push) history.pushState({ sid }, "", `/mock/chat/${sid}`);
    el.body.dataset.sessionId = sid;
    render();
    document.dispatchEvent(new CustomEvent("mockchat:navigate", { detail: { sessionId: sid, url: location.href } }));
  }

  el.links.forEach((a) => {
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      const sid = a.dataset.sessionId;
      if (sid !== currentSession()) navigate(sid);
    });
  });

  window.addEventListener("popstate", (ev) => {
    const sid = (ev.state && ev.state.sid) || sessionFromPath();
    el.body.dataset.sessionId = sid;
    render();
    document.dispatchEvent(new CustomEvent("mockchat:navigate", { detail: { sessionId: sid, url: location.href, source: "popstate" } }));
  });

  // ---- streaming reply ----
  const REPLY_PARTS = [
    "Understood. Here is a structured answer.\n\n",
    "1. First point about the task.\n",
    "2. Second point with a table:\n\n",
    "| item | value |\n|---|---|\n| alpha | 1 |\n| beta | 2 |\n\n",
    "3. Code sample:\n\n```python\nprint('hello from mock chat')\n```\n\n",
    "Done. This is the end of the mock reply.",
  ];

  function startReply(sid, userText) {
    const userMsg = { id: `${sid}-u-${++seq}`, role: "user", text: userText };
    const replyMsg = { id: `${sid}-a-${++seq}`, role: "assistant", text: "", status: "streaming" };
    (store[sid] || (store[sid] = [])).push(userMsg, replyMsg);
    saveStore();
    render();
    setAppState("generating");

    const live = el.conv.querySelector(`[data-message-id="${replyMsg.id}"] .body`);
    let i = 0;
    streamTimer = setInterval(() => {
      if (i >= REPLY_PARTS.length) {
        clearInterval(streamTimer);
        streamTimer = null;
        replyMsg.status = "complete";
        replyMsg.text = REPLY_PARTS.join("");
        saveStore();
        const node = el.conv.querySelector(`[data-message-id="${replyMsg.id}"]`);
        if (node) node.dataset.status = "complete";
        setAppState("idle");
        document.dispatchEvent(new CustomEvent("mockchat:reply-complete", { detail: { sessionId: sid, messageId: replyMsg.id } }));
        return;
      }
      replyMsg.text += REPLY_PARTS[i];
      if (live) live.innerHTML = escapeHtml(replyMsg.text);
      i += 1;
      scrollToBottom();
    }, CHUNK_MS);
  }

  el.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = el.input.value.trim();
    if (!text || el.body.dataset.appState === "generating") return;
    el.input.value = "";
    startReply(currentSession(), text);
  });

  el.input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      el.form.requestSubmit();
    }
  });

  el.stop.addEventListener("click", () => {
    if (!streamTimer) return;
    clearInterval(streamTimer);
    streamTimer = null;
    const sid = currentSession();
    const last = (store[sid] || []).slice(-1)[0];
    if (last && last.role === "assistant") {
      last.status = "complete";
      saveStore();
      const node = el.conv.querySelector(`[data-message-id="${last.id}"]`);
      if (node) node.dataset.status = "complete";
    }
    setAppState("idle");
    document.dispatchEvent(new CustomEvent("mockchat:reply-aborted", { detail: { sessionId: sid } }));
  });

  // ---- boot ----
  const initial = sessionFromPath();
  el.body.dataset.sessionId = initial;
  if (location.pathname === "/" || location.pathname === "/mock/chat") {
    history.replaceState({ sid: initial }, "", `/mock/chat/${initial}`);
  }
  render();
  setAppState("idle");
  document.dispatchEvent(new CustomEvent("mockchat:ready", { detail: { sessionId: initial } }));
})();
