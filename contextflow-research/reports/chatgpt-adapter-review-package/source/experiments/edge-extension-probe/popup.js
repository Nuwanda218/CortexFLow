/* ContextFlow Edge Probe - popup logic. */
"use strict";

const $ = (id) => document.getElementById(id);

function renderState(s) {
  const ws = $("ws");
  ws.textContent = s.wsStatus || "?";
  ws.className = "pill " + (s.wsStatus === "connected" ? "ok" : "bad");
  const tabs = s.tabs || {};
  const ids = Object.keys(tabs);
  const active = ids.map((id) => tabs[id]).sort((a, b) => (b.at || "").localeCompare(a.at || ""))[0];
  if (!active) {
    $("tab").textContent = "-";
    $("kind").textContent = "-";
    $("session").textContent = "-";
    $("appstate").textContent = "-";
    $("messages").textContent = "-";
    $("isolation").textContent = "-";
  } else {
    $("tab").textContent = `#${active.tabId} ${active.url || ""}`;
    $("kind").textContent = active.kind || "-";
    $("session").textContent = active.session ? `${active.session.kind}: ${active.session.value || "-"}` : "-";
    $("appstate").textContent = active.appState || "-";
    $("messages").textContent = active.messages ? `${active.messages.total} (u${active.messages.byRole.user}/a${active.messages.byRole.assistant}) last=${active.messages.lastStatus}` : "-";
    const iso = active.isolation || {};
    $("isolation").textContent = `isoSeesPageVar=${iso.isolatedWorldSeesPageVar} pushStateSeen=${iso.isolatedPushStateCalls} mainWorld=${iso.mainWorldProbe ? iso.mainWorldProbe.available : "?"}`;
  }
  $("log").textContent = JSON.stringify({ navEvents: (s.navEvents || []).slice(-6), coreCommands: (s.coreCommands || []).slice(-4), lastError: s.lastError }, null, 2);
}

async function refresh() {
  const s = await chrome.runtime.sendMessage({ type: "cf:get_state" });
  renderState(s || {});
}

async function activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab ? tab.id : null;
}

$("refresh").addEventListener("click", refresh);
$("ping").addEventListener("click", async () => {
  const id = await activeTabId();
  if (id == null) return;
  const r = await chrome.runtime.sendMessage({ type: "cf_ping_content", tabId: id, nonce: "popup-" + Date.now() });
  $("log").textContent = JSON.stringify(r, null, 2);
});
$("connect").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "cf_connect_core" });
  setTimeout(refresh, 400);
});
$("sidepanel").addEventListener("click", async () => {
  const id = await activeTabId();
  try {
    if (chrome.sidePanel && chrome.sidePanel.open && id != null) await chrome.sidePanel.open({ tabId: id });
  } catch (err) {
    $("log").textContent = "sidePanel.open failed: " + String(err);
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && (msg.type === "cf:page_state" || msg.type === "cf:ws_status" || msg.type === "cf:nav_event")) refresh();
});

refresh();
setInterval(refresh, 1000);
