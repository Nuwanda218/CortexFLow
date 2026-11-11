"use strict";
const runtime = chrome.runtime;
const $ = (name) => document.querySelector(`[data-testid="${name}"]`);
let replacing = false;
let replacementSessionId = null;
let busy = false;
let refreshGeneration = 0;
let lastView = null;

function text(name, value) { $(name).textContent = value || "—"; }
function label(status) {
  return ({unbound:"未绑定",active:"已绑定",paused:"已暂停",mismatch:"当前会话与绑定不一致",identity_unavailable:"当前会话身份不可用",binding_invalid:"绑定记录无效",unavailable:"页面不可用"})[status] || "状态未知";
}
function setBusy(value) {
  busy = value;
  if (value) document.querySelectorAll("button").forEach((button) => { button.disabled = true; });
}
function applyButtonState(view) {
  const current = view && view.current || {};
  const binding = view && view.binding || null;
  const status = view && view.status;
  const canBind = !!current.bindable && status !== "binding_invalid" && view.bridge_status === "connected" && view.core_status === "available";
  $("bind").disabled = busy || !canBind;
  $("replace-confirm").disabled = busy || !replacing;
  $("pause").disabled = busy || !binding || binding.state === "paused" || status === "binding_invalid";
  $("resume").disabled = busy || !binding || binding.state !== "paused" || status === "mismatch" || status === "identity_unavailable" || status === "binding_invalid";
  $("unbind").disabled = busy || (!binding && status !== "binding_invalid");
  $("refresh").disabled = busy;
}
function render(response) {
  const view = response && response.ok ? response.data : null;
  const error = response && response.error;
  if (!view) {
    text("last-error", (error && error.message) || "无法读取状态");
    applyButtonState(lastView);
    $("refresh").disabled = false;
    return;
  }
  lastView = view;
  const current = view.current || {};
  const binding = view.binding || null;
  text("current-project", current.project_name || (current.kind === "project" ? "名称暂不可用" : "不属于项目"));
  text("current-conversation", current.conversation_name || (current.kind === "new_chat" ? "新会话（尚未命名）" : "名称暂不可用"));
  text("bound-project", binding && (binding.project_name || (binding.session_kind === "project" ? "名称暂不可用" : "不属于项目")));
  text("bound-conversation", binding && (binding.conversation_name || "名称暂不可用"));
  text("binding-status", label(view.status));
  $("binding-status").dataset.state = view.status;
  text("bridge-status", view.bridge_status === "connected" ? "已连接" : "不可用");
  $("bridge-status").dataset.state = view.bridge_status;
  text("core-status", view.core_status === "available" ? "可用" : "不可用");
  $("core-status").dataset.state = view.core_status;
  text("last-error", (view.error && view.error.message) || "");
  text("detail-session", binding && binding.native_session_id);
  text("detail-endpoint", binding && binding.endpoint_id);
  text("detail-anchor", binding && binding.anchor && binding.anchor.message_id);
  $("replace-confirm").hidden = !replacing;
  applyButtonState(view);
}
async function command(type, extra) {
  refreshGeneration += 1; // invalidate every refresh started before this mutation
  setBusy(true);
  let actionError = null;
  try {
    const result = await runtime.sendMessage({type, ...(extra || {})});
    if (result && result.error && result.error.code === "REPLACEMENT_CONFIRMATION_REQUIRED") {
      replacementSessionId = result.error.details && result.error.details.target_session_id || null;
      replacing = !!replacementSessionId;
    } else if (result && result.ok) {
      replacing = false;
      replacementSessionId = null;
    }
    actionError = result && result.error;
  } catch (_err) {
    actionError = {message:"扩展后台不可用"};
  } finally {
    busy = false;
    try {
      await refresh(actionError);
    } catch (_err) {
      render({ok:false,error:actionError || {message:"扩展后台不可用"}});
    }
  }
}
async function refresh(actionError) {
  const generation = ++refreshGeneration;
  try {
    const state = await runtime.sendMessage({type:"cf:supervisor_get_state"});
    if (generation !== refreshGeneration) return;
    if (actionError && state && state.ok) state.data.error = actionError;
    render(state);
  } catch (_err) {
    if (generation !== refreshGeneration) return;
    render({ok:false,error:actionError || {message:"扩展后台不可用"}});
  }
}
$("bind").addEventListener("click", () => command("cf:supervisor_bind", {confirm_replace:false}));
$("replace-confirm").addEventListener("click", () => command("cf:supervisor_bind", {
  confirm_replace:true,
  expected_replacement_session_id:replacementSessionId,
}));
$("pause").addEventListener("click", () => command("cf:supervisor_pause"));
$("resume").addEventListener("click", () => command("cf:supervisor_resume"));
$("unbind").addEventListener("click", () => command("cf:supervisor_unbind"));
$("refresh").addEventListener("click", () => refresh());
refresh();
