/* ContextFlow Edge Probe - side panel with user-confirmed handoff prototype.
 *
 * Implements the minimal "user-confirmed clipboard handoff" flow:
 *   pending -> copied -> confirmed | cancelled
 * The panel NEVER sends anything to ChatGPT; it only writes a formatted report
 * to the clipboard when the user clicks "复制汇报".
 */
"use strict";

const $ = (id) => document.getElementById(id);

const HANDOFF = {
  reports: [],          // {task_id, summary, at, status}
  lastCopied: null,
  lastCopiedAt: null,
};

function statusBadge(status) {
  const map = {
    pending: ["待处理", "pending"],
    copied: ["已复制", "copied"],
    confirmed: ["已确认", "confirmed"],
    cancelled: ["已取消", "cancelled"],
  };
  const [label, cls] = map[status] || [status, "pending"];
  return `<span class="badge ${cls}">${label}</span>`;
}

function formatReport(r) {
  return [
    "## Worker 汇报（待 Coordinator 评估）",
    "",
    `- Task: ${r.task_id || "(unknown)"}`,
    `- Summary: ${r.summary || "(no summary)"}`,
    `- 时间: ${r.at || ""}`,
    `- 来源: ContextFlow Core（用户确认式交接）`,
    "",
    "请评估该汇报并决定下一步行动。",
  ].join("\n");
}

function renderReports() {
  const box = $("reports");
  if (!HANDOFF.reports.length) {
    box.innerHTML = "(none yet)";
    return;
  }
  box.innerHTML = HANDOFF.reports
    .map((r, i) => `
      <div class="report" data-task="${r.task_id || ""}">
        <div class="title">Task ${r.task_id || "?"} ${statusBadge(r.status)}</div>
        <div>${(r.summary || "").slice(0, 160)}</div>
        <div style="margin-top:6px">
          <button data-action="copy" data-idx="${i}">复制汇报</button>
          <button data-action="confirm" data-idx="${i}">已确认交接</button>
          <button data-action="cancel" data-idx="${i}">取消</button>
        </div>
      </div>`)
    .join("");
}

async function copyReport(idx) {
  const r = HANDOFF.reports[idx];
  if (!r) return false;
  const text = formatReport(r);
  try {
    await navigator.clipboard.writeText(text);
    r.status = "copied";
    HANDOFF.lastCopied = text;
    HANDOFF.lastCopiedAt = new Date().toISOString();
    renderReports();
    return true;
  } catch (err) {
    $("log").textContent = "clipboard write failed: " + String(err);
    return false;
  }
}

async function refresh() {
  const s = await chrome.runtime.sendMessage({ type: "cf:get_state" });
  if (!s) return;
  $("ws").textContent = s.wsStatus;
  const tabs = s.tabs || {};
  const active = Object.values(tabs).sort((a, b) => (b.at || "").localeCompare(a.at || ""))[0];
  $("page").textContent = active ? `${active.kind} ${active.url}` : "-";
  $("session").textContent = active && active.session ? `${active.session.kind}: ${active.session.value}` : "-";
  $("state").textContent = active ? `${active.appState} / ${active.messages ? active.messages.total : "?"} msgs` : "-";

  // Merge Core worker_completed commands into the handoff list (dedupe by task_id).
  for (const c of s.coreCommands || []) {
    const m = c.msg || {};
    if (m.type === "worker_completed" && m.task_id) {
      if (!HANDOFF.reports.some((r) => r.task_id === m.task_id)) {
        HANDOFF.reports.push({ task_id: m.task_id, summary: m.summary || "", at: c.at || "", status: "pending" });
        renderReports();
      }
    }
  }
  $("log").textContent = JSON.stringify({ navEvents: (s.navEvents || []).slice(-5), coreCommands: (s.coreCommands || []).slice(-4) }, null, 2);
}

$("refresh").addEventListener("click", refresh);
$("connect").addEventListener("click", async () => { await chrome.runtime.sendMessage({ type: "cf_connect_core" }); setTimeout(refresh, 400); });

$("reports").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const idx = Number(btn.dataset.idx);
  const action = btn.dataset.action;
  if (action === "copy") await copyReport(idx);
  else if (action === "confirm") { HANDOFF.reports[idx].status = "confirmed"; renderReports(); }
  else if (action === "cancel") { HANDOFF.reports[idx].status = "cancelled"; renderReports(); }
});

chrome.runtime.onMessage.addListener((m) => { if (m && m.type && m.type.startsWith("cf:")) refresh(); });

// Test hook for the automated harness.
window.__cfHandoff = {
  getReports: () => JSON.parse(JSON.stringify(HANDOFF.reports)),
  getLastCopied: () => HANDOFF.lastCopied,
  copyReport,
  formatReport,
};

refresh();
setInterval(refresh, 1500);
