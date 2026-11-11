/* ContextFlow WS bridge relay — runs as a content script on 127.0.0.1 bridge pages.
 *
 * The bridge page itself (bridge_client.html) opens a WebSocket to the local
 * bridge server and forwards requests here via DOM CustomEvents; this script
 * relays them to the extension background via chrome.runtime.sendMessage and
 * posts the result back into the page. The extension NEVER opens sockets itself;
 * the page (http://127.0.0.1) does, so no extra extension permissions are needed.
 *
 * CF-ADAPTER-001 adds the reverse direction (extension -> page) for paper-plane
 * submissions and endpoint lookups:
 *   background --runtime cf:bridge_submit--> relay --DOM cf:submit_envelope--> page
 *   page --DOM cf:submit_result--> relay --sendResponse--> background
 */
(() => {
  "use strict";
  console.log("[ContextFlow:relay] script loaded on", location.href);
  if (window.__cfBridgeRelay) return; // never double-register
  window.__cfBridgeRelay = true;
  // Visible marker for diagnostics (the isolated-world flag is not visible to
  // the page or to external inspection tools).
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

  // Extension background -> bridge page: submit one envelope to the external receiver.
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
      return true; // async
    }
    if (msg && msg.type === "cf:bridge_endpoint_lookup") {
      relayRequest({
        request_event: "cf:endpoint_lookup",
        result_event: "cf:endpoint_lookup_result",
        payload: { session_id: msg.session_id },
      }).then(sendResponse);
      return true; // async
    }
    if (msg && msg.type === "cf:bridge_control") {
      relayRequest({
        request_event: "cf:supervisor_control_request",
        result_event: "cf:supervisor_control_result",
        payload: { action: msg.action, params: msg.params },
      }).then(sendResponse);
      return true; // async
    }
    return false;
  });
})();
