/* ContextFlow WS bridge relay — runs as a content script on 127.0.0.1 bridge pages.
 *
 * The bridge page itself (bridge_client.html) opens a WebSocket to the local
 * bridge server and forwards requests here via DOM CustomEvents; this script
 * relays them to the extension background via chrome.runtime.sendMessage and
 * posts the result back into the page. The extension NEVER opens sockets itself;
 * the page (http://127.0.0.1) does, so no extra extension permissions are needed.
 */
(() => {
  "use strict";
  if (window.__cfBridgeRelay) return; // never double-register
  window.__cfBridgeRelay = true;

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
})();
