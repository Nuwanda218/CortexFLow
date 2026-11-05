/* ContextFlow — ChatGPT 页面只读探测脚本 v2（Console 版，CF-EXP-002）
 *
 * 用法：在真实 ChatGPT 会话页打开 DevTools Console，整段粘贴回车。
 * 只读：不修改 DOM、不发网络请求、不读 Cookie/Token。
 *
 * v2 修复（相对 v1）：
 *   - user 回合的 h4 在回合容器外，改用 turn_key 后缀（:user / :assistant）判断角色
 *   - session_id 以 URL 的 /c/<id> 为准；DOM 的 data-chatgpt-selection-conversation-id
 *     是 local-chatgpt:<uuid>，不是服务端会话 id，仅作候选记录
 */
(() => {
  "use strict";

  // ============ 1. 会话识别（URL 优先，DOM 候选全部列出） ============
  function getSession() {
    const url = location.href;
    const out = { session_id: null, kind: "unknown", url, source: null, candidates: {} };

    const sel = document.querySelector("[data-chatgpt-selection-conversation-id]");
    if (sel) out.candidates.selection = sel.getAttribute("data-chatgpt-selection-conversation-id");
    const above = document.querySelector("[data-above-composer-conversation-id]");
    if (above) out.candidates.above_composer = above.getAttribute("data-above-composer-conversation-id");

    let m = url.match(/\/g\/([^/]+)\/c\/([0-9a-fA-F-]{8,})/);
    if (m) {
      out.kind = "project"; out.session_id = m[2]; out.source = "url:project_chat";
    } else if ((m = url.match(/\/c\/([0-9a-fA-F-]{8,})/))) {
      out.kind = "chat"; out.session_id = m[1]; out.source = "url:chat";
    } else if (/\/g\/([^/]+)\/project/.test(url)) {
      out.kind = "project"; out.source = "url:project_page";
    } else if (/^https:\/\/chatgpt\.com\/?$/.test(url)) {
      out.kind = "new_chat"; out.source = "url:root";
    }

    if (!out.session_id) {
      const v = out.candidates.above_composer || out.candidates.selection || "";
      out.session_id = v.replace(/^(local-)?chatgpt:/, "") || null;
      out.source = out.session_id ? "dom:" + (out.candidates.above_composer ? "above_composer" : "selection") : null;
    }
    return out;
  }

  // ============ 2. 回合定位（用 key 后缀判断角色） ============
  function findTurns() {
    const all = Array.from(document.querySelectorAll("[data-chatgpt-search-unit-key]"));
    return all.filter((el) => {
      if (el.getClientRects().length === 0) return false;
      const key = el.getAttribute("data-chatgpt-search-unit-key") || "";
      if (!/:(user|assistant)$/.test(key)) return false;
      const parentTurn = el.parentElement && el.parentElement.closest("[data-chatgpt-search-unit-key]");
      return !parentTurn;
    });
  }

  function roleOf(turn) {
    const h = turn.querySelector("h4[data-conversation-role]");
    if (h) return h.getAttribute("data-conversation-role");
    const key = turn.getAttribute("data-chatgpt-search-unit-key") || "";
    const m = key.match(/:(user|assistant)$/);
    return m ? m[1] : null;
  }

  function idsOf(turn) {
    const ids = [];
    const raw = turn.getAttribute("data-chatgpt-search-message-ids") || "";
    raw.split(/\s+/).filter(Boolean).forEach((x) => ids.push(x));
    const dil = turn.querySelector("[data-dil-message-id]");
    if (dil) ids.push(dil.getAttribute("data-dil-message-id"));
    return Array.from(new Set(ids));
  }

  // ============ 3. 正文提取 ============
  function contentRoot(turn) {
    return turn.querySelector("[data-dil-message-id]") ||
           turn.querySelector("[data-user-message-bubble='true']") || turn;
  }

  function extractMarkdown(root) {
    const parts = [];
    function walk(el) {
      if (!(el instanceof Element)) return;
      const comp = el.getAttribute("data-d-component") || el.getAttribute("data-d-client-defined-widget");

      if (comp === "text") { const t = el.innerText.trim(); if (t) parts.push(t); return; }
      if (comp === "title") {
        const tag = el.tagName.toLowerCase();
        const level = tag === "h1" ? 1 : tag === "h2" ? 2 : tag === "h3" ? 3 : 2;
        const t = el.innerText.trim(); if (t) parts.push("#".repeat(level) + " " + t); return;
      }
      if (comp === "blockquote") {
        const t = el.innerText.trim(); if (t) parts.push(t.split("\n").map((l) => "> " + l).join("\n")); return;
      }
      if (comp === "code_block") {
        const code = el.querySelector("code");
        const lang = el.getAttribute("data-d-language") || (code && (code.className.match(/language-([\w-]+)/) || [])[1]) || "";
        parts.push("```" + lang + "\n" + (code || el).innerText.replace(/\n$/, "") + "\n```"); return;
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
    const clone = root.cloneNode(true);
    clone.querySelectorAll("h4[data-conversation-role]").forEach((n) => n.remove());
    return clone.innerText.trim();
  }

  // ============ 4. 生成状态 ============
  function getGenerationState() {
    const states = Array.from(document.querySelectorAll("[data-talvt-turn-state]")).map((el) => el.getAttribute("data-talvt-turn-state"));
    const stopBtn = document.querySelector('button[aria-label*="停止"], button[aria-label*="Stop"], [data-testid="stop-button"]');
    const evidence = { turn_states: states, stop_button: !!stopBtn };
    if (states.includes("in_progress")) return { state: "generating", evidence };
    if (stopBtn) return { state: "generating", evidence };
    if (states.length && states[states.length - 1] === "complete") return { state: "completed", evidence };
    return { state: "unknown", evidence };
  }

  // ============ 5. 读取消息（DOM 顺序即真实顺序） ============
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
        markdown,
        text_length: markdown.length,
      };
    });
  }

  // ============ 6. 运行 ============
  const session = getSession();
  const generation = getGenerationState();
  const messages = readMessages();

  console.log("%c[ContextFlow] probe v2", "color:#4f8cff;font-weight:bold");
  console.log("session:", session);
  console.log("generation:", JSON.stringify(generation));
  console.table(messages.map((m) => ({
    order: m.order, role: m.role, key: m.turn_key,
    ids: m.message_ids.join(","), len: m.text_length,
    preview: m.markdown.slice(0, 50).replace(/\n/g, " "),
  })));
  console.log("messages JSON:", JSON.stringify(messages, null, 2));
  window.__cfRead = { getSession, readMessages, getGenerationState, last: { session, generation, messages } };
})();
