# C-01 Supervisor Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-Session Supervisor binding experience with strict marked-block auto-submission, unified idempotent manual/automatic submission, and authorized Core parse-status feedback.

**Architecture:** Extension owns the active/paused binding, scan anchor, local recovery records, popup, and DOM observation. Bridge remains an authenticated correlation relay. Core owns Endpoint registration, Message acceptance, and an ownership-checked read model over Message/ParseJob/ParseResult; existing Envelope v1, SQLite schema v2, and Parser Worker remain unchanged.

**Tech Stack:** MV3 JavaScript, Chrome storage/runtime/tabs APIs, FastAPI + WebSocket bridge, Python 3.10 asyncio/dataclasses/SQLite, unittest/pytest, Playwright with Edge, Fake LLM extraction adapter.

**Design:** `docs/superpowers/specs/2026-11-11-c01-supervisor-binding-design.md`

**Execution constraints:** Work in an isolated `feat/c-01-supervisor-binding` worktree. Do not modify C-02 artifacts/prototypes, do not implement Stage C, do not commit/push/merge, and do not send real ChatGPT messages or invoke a paid Provider without separate confirmation.

---

## File responsibility map

### New focused files

- `contextflow-extension/dispatch_protocol.js` — pure strict marked-block parser, content normalization, canonical logical submission payload, SHA-256 submission identity helpers usable from content/background/test pages.
- `contextflow-extension/popup.html` — semantic Supervisor control markup only.
- `contextflow-extension/popup.css` — popup presentation and status styles.
- `contextflow-extension/popup.js` — popup→background commands and render mapping; no identity trust decisions.
- `contextflow_core/supervisor_control.py` — Endpoint bind and ownership-checked status read model; no transport loop.
- `tests/core/test_supervisor_control.py` — Core authorization/status tests.
- `tests/adapter/test_c01_bridge_protocol.py` — Bridge control correlation and connection-ownership tests.
- `experiments/playwright-probe/c01_supervisor_test.py` — real extension + mock ChatGPT automated binding/protocol tests.
- `experiments/playwright-probe/c01_fake_e2e.py` — real Extension/Bridge/Core/SQLite/Fake Parser/status feedback demonstration.
- `docs/C-01-supervisor-usage.md` — install, start, bind, submit, inspect, troubleshoot, and manual real-page acceptance.
- `reports/C-01-supervisor-binding.md` — final layered report and evidence inventory.

### Modified files

- `contextflow-extension/manifest.json` — version bump, popup declaration, script ordering, permissions needed for active-tab UI routing.
- `contextflow-extension/content.js` — expose fresh message probes and render page-level submission feedback; delegate parsing/identity/submission to shared/background logic.
- `contextflow-extension/background.js` — authoritative single-binding state machine, per-Session anchors, unified submission construction, scan coordination, status polling, management sender checks.
- `contextflow-extension/bridge_relay.js` — generic Supervisor control request relay.
- `contextflow-extension/bridge_server.py` — authenticated control correlation relay; remove unnecessary path disclosure from health.
- `contextflow-extension/README.md` — C-01 capabilities and startup pointer.
- `contextflow_core/receiver.py` — route authenticated control frames to `SupervisorControlService` while preserving submission behavior.
- `contextflow_core/message_store.py` — no schema change; reuse existing lookup only, adding a narrow reader helper only if tests prove needed.
- `contextflow_core/parse_jobs.py` — no state/schema change; reuse `get_job` and `get_final_result`.
- Existing adapter/receiver/Parser tests — update only assertions affected by additive frames/UI, preserving Stage A/B contract tests.

---

### Task 1: Create isolated C-01 worktree and baseline evidence

**Files:**
- No source changes
- Record commands later in: `reports/C-01-supervisor-binding.md`

- [ ] **Step 1: Load worktree workflow and create the isolated branch**

Use `using-git-worktrees`, create a worktree from current HEAD named `feat/c-01-supervisor-binding`, and verify the source working tree's untracked C-02 artifacts are absent from the new worktree.

Expected checks:

```bash
git status --short --branch
git branch --show-current
```

Expected branch: `feat/c-01-supervisor-binding`; expected C-01 worktree status: clean except design/plan documents deliberately copied or created for C-01.

- [ ] **Step 2: Run the current regression baseline**

Run from `contextflow-research`:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests -q -p no:cacheprovider
```

Expected: all existing tests pass (baseline previously reported as 330 tests plus subtests; record actual current count).

- [ ] **Step 3: Run the existing browser adapter baseline when Edge/Playwright is available**

```bash
python -B experiments/playwright-probe/cfadapter001_test.py
```

Expected: existing CF-ADAPTER-001 checks pass. If environment blocks Edge, record exact reason rather than claiming pass.

- [ ] **Step 4: Capture the worktree checkpoint**

```bash
git status --short
```

Expected: no C-02 path changed and no commit made.

### Task 2: Strict dispatch-block parser and unified identity primitives

**Files:**
- Create: `contextflow-extension/dispatch_protocol.js`
- Create: `tests/extension/dispatch_protocol_harness.html`
- Create: `experiments/playwright-probe/c01_dispatch_protocol_test.py`
- Modify: `contextflow-extension/manifest.json`

- [ ] **Step 1: Write failing table-driven parser tests**

Create a Playwright browser harness that loads `dispatch_protocol.js` and calls `globalThis.ContextFlowDispatch.parse(text)`. Test exact results for:

```javascript
[
  { name: "none", text: "ordinary answer", status: "none" },
  { name: "one", text: "[CONTEXTFLOW_DISPATCH_V1]\n请 Codex 修复 Adapter。\n[/CONTEXTFLOW_DISPATCH_V1]", status: "valid", content: "请 Codex 修复 Adapter。" },
  { name: "many", text: "[CONTEXTFLOW_DISPATCH_V1]\nA\n[/CONTEXTFLOW_DISPATCH_V1]\ntext\n[CONTEXTFLOW_DISPATCH_V1]\nB\n[/CONTEXTFLOW_DISPATCH_V1]", status: "valid", content: "A\n\nB" },
  { name: "fenced-backtick", text: "```text\n[CONTEXTFLOW_DISPATCH_V1]\nexample\n[/CONTEXTFLOW_DISPATCH_V1]\n```", status: "none" },
  { name: "fenced-tilde", text: "~~~\n[CONTEXTFLOW_DISPATCH_V1]\nexample\n[/CONTEXTFLOW_DISPATCH_V1]\n~~~", status: "none" },
  { name: "nested", text: "[CONTEXTFLOW_DISPATCH_V1]\n[CONTEXTFLOW_DISPATCH_V1]\nA\n[/CONTEXTFLOW_DISPATCH_V1]", status: "invalid", code: "NESTED_OPEN_MARKER" },
  { name: "missing-close", text: "[CONTEXTFLOW_DISPATCH_V1]\nA", status: "invalid", code: "UNCLOSED_BLOCK" },
  { name: "orphan-close", text: "[/CONTEXTFLOW_DISPATCH_V1]", status: "invalid", code: "ORPHAN_CLOSE_MARKER" },
  { name: "empty", text: "[CONTEXTFLOW_DISPATCH_V1]\n \n[/CONTEXTFLOW_DISPATCH_V1]", status: "invalid", code: "EMPTY_BLOCK" },
  { name: "inline-marker", text: "prefix [CONTEXTFLOW_DISPATCH_V1]", status: "invalid", code: "AMBIGUOUS_MARKER" },
  { name: "unclosed-fence", text: "```\n[CONTEXTFLOW_DISPATCH_V1]\nexample\n[/CONTEXTFLOW_DISPATCH_V1]", status: "none" }
]
```

Also test LF/CRLF normalization and fences with up to three leading spaces.

- [ ] **Step 2: Run tests and verify the missing API failure**

```bash
python -B experiments/playwright-probe/c01_dispatch_protocol_test.py
```

Expected: FAIL because `ContextFlowDispatch` does not exist.

- [ ] **Step 3: Implement the strict finite-state parser**

Implement these exported functions without DOM dependencies:

```javascript
globalThis.ContextFlowDispatch = Object.freeze({
  parse,
  normalizeFullMessage,
  canonicalIdentityDocument,
  submissionIdFor,
});
```

`parse` must scan line-by-line with explicit states `outside`, `inside`, and `fence`; it must return one of:

```javascript
{ status: "none", blocks: [], content: null, error: null }
{ status: "valid", blocks: ["..."], content: "...", error: null }
{ status: "invalid", blocks: [], content: null, error: { code, message } }
```

Use `crypto.subtle.digest("SHA-256", TextEncoder(...))` over canonical JSON array:

```javascript
[
  "contextflow-supervisor-submission-v1",
  "chatgpt_web",
  endpointId,
  sessionId,
  messageId,
  "markdown",
  normalizedText
]
```

Return `sub_` plus 64 lowercase hex digits. Trigger mode must not be in the identity document.

- [ ] **Step 4: Add deterministic identity tests**

Assert:

```javascript
manualId === autoId
sameInputsAgain === autoId
differentTextId !== autoId
differentMessageId !== autoId
/^sub_[0-9a-f]{64}$/.test(autoId)
```

Also assert canonical serialization is stable and does not include `trigger`, `user_message_id`, or `turn_group_id`.

- [ ] **Step 5: Run focused tests**

```bash
python -B experiments/playwright-probe/c01_dispatch_protocol_test.py
```

Expected: all parser and identity checks pass.

- [ ] **Step 6: Record Git checkpoint**

```bash
git diff --check
git status --short
```

Expected: only Task 2 files plus approved design/plan docs.

### Task 3: Core Supervisor control service

**Files:**
- Create: `contextflow_core/supervisor_control.py`
- Create: `tests/core/test_supervisor_control.py`
- Modify: `contextflow_core/receiver.py`

- [ ] **Step 1: Write failing bind tests**

Build a temporary Registry, MessageStore, and ParseJobRepository, then assert:

```python
first = await service.bind_supervisor({
    "native_session_id": "native-session-1",
    "session_kind": "chat",
})
second = await service.bind_supervisor({
    "native_session_id": "native-session-1",
    "session_kind": "chat",
})
assert first["endpoint_id"] == second["endpoint_id"]
assert first["created"] is True
assert second["created"] is False
```

Reject extra fields, empty/oversized IDs, caller-provided `endpoint_id`, invalid `session_kind`, and non-object params with `INVALID_CONTROL_REQUEST`.

- [ ] **Step 2: Run bind tests and verify failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/core/test_supervisor_control.py -q -p no:cacheprovider
```

Expected: collection/import FAIL because `supervisor_control.py` is missing.

- [ ] **Step 3: Implement bind and control response contract**

Define:

```python
class SupervisorControlService:
    def __init__(self, registry: EndpointRegistry, messages: SQLiteMessageStore,
                 parse_jobs: SQLiteParseJobRepository) -> None: ...

    async def handle(self, action: object, params: object) -> dict: ...
    async def bind_supervisor(self, params: object) -> dict: ...
    async def get_submission_status(self, params: object) -> dict: ...
```

`bind_supervisor` fixes provider to `chatgpt_web`, calls `EndpointRegistry.register`, and returns only endpoint ID, native session ID, session kind, and `created`.

Use a transport-neutral exception→wire mapper producing:

```python
{"success": False, "error": {"code": "...", "message": "..."}}
```

Messages must not include local paths or user content.

- [ ] **Step 4: Write failing ownership tests for status lookup**

Accept a Supervisor Envelope, then query with all four ownership fields. Test:

```python
{
  "submission_id": raw["submission_id"],
  "endpoint_id": binding.endpoint_id,
  "native_session_id": "native-session-1",
  "native_message_id": raw["source"]["message_id"],
}
```

Assert success and Core accepted. For each field, replace it independently with another valid value and assert the same non-enumerating result:

```python
{"found": False}
```

Do the same for a guessed absent submission ID. Neither error nor response may reveal whether another Supervisor's submission exists.

- [ ] **Step 5: Implement fail-closed ownership checks**

In order:

1. Exact-field/type validation.
2. `EndpointRegistry.get(endpoint_id)` and provider/session check.
3. `SQLiteMessageStore.get_message_by_submission_id(SubmissionId(...))`.
4. Verify stored Envelope endpoint/session/native message equal the supplied values.
5. Return `{"found": False}` for every absence/mismatch.

Never authorize solely from submission ID or MessageId.

- [ ] **Step 6: Add status-mapping tests**

Create or drive each job state and assert public mapping:

```text
no ParseJob / ready -> waiting
processing          -> processing
retry_wait          -> retry_wait
completed parsed    -> parsed + count + targets
completed unresolved-> unresolved + safe error
completed failed    -> failed + safe error
```

Assert no response contains Envelope content, database path, Registry path, lease owner, request raw JSON, or Provider details. Cap user-visible error message length.

- [ ] **Step 7: Implement status mapping using repository APIs**

Use `get_job` and formal `ParseResult`; map enums explicitly. For a completed result, expose only:

```python
{
    "status": result.status.value,
    "instruction_count": len(result.instructions),
    "target_agents": [item.target_agent for item in result.instructions],
    "error": safe_error(result.error),
    "completed_at_ms": job.completed_at_ms,
}
```

For no job, return waiting; do not infer failed from elapsed time.

- [ ] **Step 8: Route control frames in CoreReceiver**

Extend `CoreReceiver.__init__` with optional `controls: SupervisorControlService | None`. `handle_frame` accepts:

```python
{
  "type": "supervisor_control_request",
  "request_id": "ctl-1",
  "action": "bind_supervisor",
  "params": {...}
}
```

and returns:

```python
{
  "type": "supervisor_control_result",
  "request_id": "ctl-1",
  "success": True,
  "data": {...},
}
```

If controls are not configured, return `CORE_CONTROL_UNAVAILABLE`; existing submission frames and ack shape remain unchanged.

- [ ] **Step 9: Compose Receiver CLI with one Registry/DB view**

In `_run_cli`, construct one `EndpointRegistry`, one `SQLiteMessageStore`, one `SQLiteParseJobRepository`, and `SupervisorControlService`. Close both SQLite owners in `finally`. Preserve `--once` semantics: only accepted submissions increment/exit; control calls never count as accepted messages.

- [ ] **Step 10: Run focused Core tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/core/test_supervisor_control.py \
  tests/core/test_receiver.py \
  tests/core/test_message_store.py \
  tests/core/test_parse_jobs.py -q -p no:cacheprovider
```

Expected: all pass; legacy ack excludes Core message ID.

- [ ] **Step 11: Record Git checkpoint**

```bash
git diff --check
git status --short
```

### Task 4: Bridge control correlation and security

**Files:**
- Modify: `contextflow-extension/bridge_server.py`
- Modify: `contextflow-extension/bridge_relay.js`
- Modify: fallback relay section in `contextflow-extension/content.js`
- Create: `tests/adapter/test_c01_bridge_protocol.py`

- [ ] **Step 1: Write failing control round-trip tests**

Start the real Bridge with fake page/Core clients. Page sends:

```json
{
  "type": "supervisor_control_request",
  "request_id": "page-ctl-1",
  "action": "ping_core",
  "params": {}
}
```

Assert Core receives the same action/params plus a Bridge-owned correlation key, then Core response returns only to the originating page request.

- [ ] **Step 2: Add adversarial correlation tests**

Cover:

- no Core → explicit `CORE_UNAVAILABLE`;
- invalid/missing request ID/action/params → `INVALID_CONTROL_REQUEST`;
- duplicate in-flight request ID with changed payload → conflict;
- response from non-owner ignored;
- wrong correlation/request ID ignored;
- late result after Core replacement ignored;
- late result after page replacement ignored;
- disallowed WebSocket Origin rejected;
- `/health` omits `registry_path`, database path, token, and content.

- [ ] **Step 3: Run tests and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/adapter/test_c01_bridge_protocol.py -q -p no:cacheprovider
```

Expected: FAIL because control frame handling is absent and health still exposes `registry_path`.

- [ ] **Step 4: Implement Bridge control pending records**

Add `pending_controls` keyed by a Bridge-generated ID. Each record binds:

```python
{
  "page_request_id": request_id,
  "page_client_id": page_client_id,
  "receiver_client_id": state["external_client"],
  "canonical_request": canonical_json,
  "at": time.time(),
}
```

Forward to Core as `supervisor_control_request` with Bridge correlation. Route only matching `supervisor_control_result` from the same external client to the same current page client. Expire records after a bounded TTL. Do not persist them.

- [ ] **Step 5: Add relay message type**

In `bridge_relay.js` and content fallback, accept only background's `cf:bridge_control`, dispatch `cf:supervisor_control_request`, and wait for `cf:supervisor_control_result`. Use timeout→`status: unknown`; do not retry or alter params in the relay.

In generated bridge client HTML, map DOM event↔page WebSocket control frame and keep submission paths unchanged.

- [ ] **Step 6: Sanitize health output**

Return only operational counts/booleans:

```python
{
  "ok": True,
  "page_connected": ...,
  "external_clients": ...,
  "pending_requests": ...,
  "pending_submissions": ...,
  "pending_controls": ...,
}
```

Remove Registry path.

- [ ] **Step 7: Run bridge tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/adapter/test_c01_bridge_protocol.py \
  tests/adapter/test_bridge_protocol.py \
  tests/adapter/test_core_receiver_integration.py -q -p no:cacheprovider
```

Expected: all pass, including existing ownership and late-ack tests.

- [ ] **Step 8: Record Git checkpoint**

```bash
git diff --check
git status --short
```

### Task 5: Extension single-binding state machine and popup

**Files:**
- Modify: `contextflow-extension/manifest.json`
- Modify: `contextflow-extension/background.js`
- Modify: `contextflow-extension/content.js`
- Create: `contextflow-extension/popup.html`
- Create: `contextflow-extension/popup.css`
- Create: `contextflow-extension/popup.js`
- Create: `experiments/playwright-probe/c01_supervisor_test.py`

- [ ] **Step 1: Write failing popup/binding browser tests**

Using the existing mock ChatGPT DOM, load the real extension and popup. Assert initial states and operations:

1. stable Session + no binding → bind available;
2. new chat/no stable Session → identity unavailable and bind disabled;
3. bind calls Core and stores one active binding;
4. reload page/extension → same binding restored after live verification;
5. navigate current tab to another Session → mismatch, no silent migration;
6. bind different Session → explicit replacement confirmation is required;
7. pause → paused; resume on matching Session → active with fresh anchor;
8. resume on mismatched Session → rejected;
9. unbind → extension state removed, Core history untouched;
10. content script cannot invoke management operation directly.

- [ ] **Step 2: Run browser test and confirm failure**

```bash
python -B experiments/playwright-probe/c01_supervisor_test.py --group binding
```

Expected: FAIL because popup and background management APIs do not exist.

- [ ] **Step 3: Add popup manifest and markup**

Manifest changes:

```json
"permissions": ["storage", "tabs"],
"action": {
  "default_title": "ContextFlow Supervisor",
  "default_popup": "popup.html"
}
```

Ensure `dispatch_protocol.js` loads before `content.js` on ChatGPT/mock pages. Popup sections must have stable test IDs for current Session, bound Session, binding status, Bridge, Core, recent submission, actions, error, and developer details.

- [ ] **Step 4: Implement background binding document and sender gate**

Define storage keys and pure transitions in background:

```javascript
const SUPERVISOR_BINDING_KEY = "cfSupervisorBindingV1";
const SUBMISSION_RECORDS_KEY = "cfSupervisorSubmissionsV1";
```

Management handler accepts only extension pages:

```javascript
function isTrustedExtensionPage(sender) {
  return sender.id === chrome.runtime.id && !sender.tab &&
    typeof sender.url === "string" && sender.url.startsWith(chrome.runtime.getURL(""));
}
```

Implement `cf:supervisor_get_state`, `bind`, `pause`, `resume`, `unbind`, and `refresh`. Every operation probes the active tab in real time and checks allowed real/mock Origin. `bind` calls Core control and writes state only after success.

- [ ] **Step 5: Implement popup rendering and actions**

Map background facts to user language without exposing raw DB fields. Require an explicit second click/confirm UI before replacement. Disable impossible actions rather than sending them. Put SessionId/EndpointId/submission/MessageId in `<details>`.

- [ ] **Step 6: Implement content probe API**

Add one fresh read handler returning only current Session, generation, ordered Assistant identities/status, and latest Assistant ID. It must perform pre/post Session checks and reject `SESSION_CHANGED`. Include extracted content only for a specifically requested message ID, never as a bulk management response.

- [ ] **Step 7: Run binding tests**

```bash
python -B experiments/playwright-probe/c01_supervisor_test.py --group binding
```

Expected: all binding, replacement, pause/resume, reload, mismatch, and sender-gate checks pass.

- [ ] **Step 8: Run existing browser regression**

```bash
python -B experiments/playwright-probe/cfadapter001_test.py
```

Expected: legacy manual paper-plane behavior remains compatible or intentionally updated assertions pass without weakening security/idempotency checks.

- [ ] **Step 9: Record Git checkpoint**

```bash
git diff --check
git status --short
```

### Task 6: Automatic scan anchors and unified manual/automatic submission

**Files:**
- Modify: `contextflow-extension/background.js`
- Modify: `contextflow-extension/content.js`
- Modify: `experiments/playwright-probe/c01_supervisor_test.py`

- [ ] **Step 1: Write failing automatic submission tests**

Cover:

- ordinary completed Assistant reply → no submission, anchor advances;
- complete legal block → one submission;
- two blocks → one Envelope with `A\n\nB`;
- in-progress complete-looking block → no submission until same message becomes complete;
- half block while streaming → no submission;
- invalid block after complete → no auto submission and explicit diagnostic;
- fenced example → no submission;
- DOM rerender/repeated scan → no duplicate;
- page refresh/extension reload → no historical re-submit;
- switch Session and return to old tab → no historical re-submit;
- same Session in two tabs → at most one logical submission;
- bind and resume anchor skip prior messages;
- anchor missing fails closed; a newly observed in-progress ID can re-arm and submit on completion.

- [ ] **Step 2: Write failing manual/auto identity tests**

For the same Assistant and valid extracted payload:

```javascript
assert(autoRecord.submission_id === manualRecord.submission_id)
assert(coreMessageCount === 1)
```

For explicit whole-message override of an invalid marked response:

```javascript
assert(wholeMessageId !== extractedOrPriorId)
```

For a manually submitted ordinary response versus a later DOM content change under the same native message ID, assert distinct deterministic IDs and no silent success reuse; Core either accepts a new ID or reports conflict only for identical IDs with changed Envelope.

- [ ] **Step 3: Run submission tests and confirm failure**

```bash
python -B experiments/playwright-probe/c01_supervisor_test.py --group submission
```

Expected: FAIL because automatic scan/background unified submit do not exist.

- [ ] **Step 4: Move Envelope authority to background**

Content sends an immutable candidate:

```javascript
{
  tab_id,
  expected_session_id,
  message_id,
  user_message_id,
  turn_group_id,
  status,
  raw_text,
  requested_mode: "auto" | "manual" | "manual_full_override"
}
```

Background re-probes current Session/message/status, parses/normalizes with shared protocol, checks binding active/matching for auto (matching for manual), computes deterministic ID, resolves the bound Endpoint from stored binding, and persists the Envelope before transport.

Do not trust Endpoint/Session/submission fields supplied by page content.

- [ ] **Step 5: Implement anchor transition logic**

Persist anchor only for the bound Session. For each scan:

```text
matching active binding + known anchor -> process only later messages in DOM order
ordinary/valid/invalid complete       -> record disposition and advance anchor
in-progress                           -> arm ID, do not advance as complete
missing anchor with no armed live ID  -> fail closed, preserve history
paused/mismatch                       -> no scan mutation
resume                               -> latest current Assistant becomes anchor
```

Serialize scans in background per Session so two tabs cannot race transition state. Use deterministic submission ID + record lookup + Core idempotency as secondary defenses.

- [ ] **Step 6: Replace paper-plane click with unified background request**

Keep page button visuals, but call the same background constructor. `valid` uses extracted content; `none` uses normalized full content. `invalid` shows syntax error and requires explicit second “submit whole reply” action. Accepted existing records replay state; unknown records query/retry same snapshot.

- [ ] **Step 7: Implement submission and status record migration**

Load old `cfSubmissionRecords`. For an old Envelope whose Session/message/normalized text matches a new candidate, retain old Envelope and random ID as the authoritative historical submission. Never silently assign a new deterministic ID to already-accepted legacy content. Store new records under Session+message+payload digest.

- [ ] **Step 8: Run all extension tests**

```bash
python -B experiments/playwright-probe/c01_dispatch_protocol_test.py
python -B experiments/playwright-probe/c01_supervisor_test.py
python -B experiments/playwright-probe/cfadapter001_test.py
```

Expected: all pass.

- [ ] **Step 9: Record Git checkpoint**

```bash
git diff --check
git status --short
```

### Task 7: Status polling and recovery feedback

**Files:**
- Modify: `contextflow-extension/background.js`
- Modify: `contextflow-extension/content.js`
- Modify: `contextflow-extension/popup.js`
- Modify: `experiments/playwright-probe/c01_supervisor_test.py`

- [ ] **Step 1: Write failing status lifecycle tests**

Using a controllable fake Core status service, assert UI transitions:

```text
not submitted → submitting → Core accepted → waiting Parser
→ processing → parsed(targets/count)
```

Separately test unresolved, failed, retry_wait, query timeout/unknown, Bridge down, Core down, Worker-not-started waiting, and connection recovery.

- [ ] **Step 2: Write unknown-acceptance recovery test**

Drop submission ack after Core durably accepts. Assert Extension:

1. marks transport result unknown;
2. queries original submission ID with endpoint/session/native message ownership tuple;
3. discovers the Core Message;
4. displays Core accepted without creating another Message;
5. later displays parser result.

- [ ] **Step 3: Run focused tests and confirm failure**

```bash
python -B experiments/playwright-probe/c01_supervisor_test.py --group status
```

Expected: FAIL because status polling is absent.

- [ ] **Step 4: Implement safe control calls and polling**

Background `callSupervisorControl(action, params)` uses bridge relay. Poll only recent non-terminal records with bounded exponential cadence; popup refresh can trigger an immediate query. Query always sends all ownership fields. A timeout preserves last known facts and overlays connection/status unknown; it never changes parser result to failed.

- [ ] **Step 5: Map status to popup and page buttons**

User states:

```text
idle / submitting / core_accepted / parser_waiting / parser_processing
parsed / unresolved / failed / unknown / invalid
```

For waiting beyond a UI threshold, append “尚未发现 Parser 处理；请确认 Worker 已启动” but retain state `parser_waiting`.

- [ ] **Step 6: Run status and full browser tests**

```bash
python -B experiments/playwright-probe/c01_supervisor_test.py
python -B experiments/playwright-probe/cfadapter001_test.py
```

Expected: all checks pass; no false failed for absent Worker.

- [ ] **Step 7: Record Git checkpoint**

```bash
git diff --check
git status --short
```

### Task 8: Real Fake-Parser end-to-end demonstration

**Files:**
- Create: `experiments/playwright-probe/c01_fake_e2e.py`
- Create runtime artifacts under: `_artifacts/c01-fake-<run-id>/`
- Test: existing Core and extension modules

- [ ] **Step 1: Build a deterministic isolated harness**

The script must allocate temporary ports and paths, start:

1. mock ChatGPT HTTP page;
2. real `bridge_server.py`;
3. real `contextflow_core.receiver` with isolated Registry + new Schema v2 DB;
4. real unpacked Extension in Edge;
5. real Parser Worker configured with Fake extraction response and isolated Agent Directory.

Do not use MockCoreReceiver for this demonstration.

- [ ] **Step 2: Drive the full scenario**

Through popup/background user-equivalent actions:

1. bind Session A;
2. add a completed Assistant response containing one block with Codex then Claude Code instructions;
3. observe automatic submission;
4. wait for Core accepted/waiting;
5. run or keep running Fake Parser Worker;
6. wait for parsed with two ordered targets;
7. reload page and assert Message count stays one;
8. pause; add another legal response; assert Message count stays one.

- [ ] **Step 3: Persist evidence without secrets or full unrelated content**

Write `evidence.json` with ports removed or marked ephemeral, IDs, state transitions, final targets/count, DB message count, duplicate count, pause result, test summary, and exact versions. Save popup screenshots for accepted/parsed/paused. Do not store tokens beyond the fixed local dev token or private conversation content.

- [ ] **Step 4: Run the demonstration**

```bash
python -B experiments/playwright-probe/c01_fake_e2e.py
```

Expected: all checks pass and an isolated artifact directory is printed.

- [ ] **Step 5: Inspect evidence and DB through supported APIs**

Use Core readers in the harness to verify one Message and one formal ParseResult. Do not make the Extension read SQLite directly.

- [ ] **Step 6: Record Git checkpoint**

```bash
git diff --check
git status --short
```

### Task 9: Documentation and manual real-ChatGPT acceptance

**Files:**
- Create: `docs/C-01-supervisor-usage.md`
- Modify: `contextflow-extension/README.md`
- Create: `reports/C-01-supervisor-binding.md`

- [ ] **Step 1: Write user startup and install instructions**

Document commands with explicit paths for:

- dependencies;
- isolated Registry/Schema v2 DB/Agent Directory;
- Bridge start and bridge page URL;
- Core Receiver start;
- Fake or real Parser Worker start;
- unpacked Extension reload and required ChatGPT tab refresh;
- popup bind/pause/resume/unbind;
- legal dispatch block and paper-plane manual fallback;
- status meanings and troubleshooting.

Explicitly state no implicit v1 migration.

- [ ] **Step 2: Write manual real-page acceptance checklist**

Include observation-only prerequisites and user actions for:

- bind existing stable conversation;
- new chat without ID;
- refresh recovery;
- Session switch mismatch;
- complete block automatic submit once;
- ordinary response not submitted;
- streaming half block not submitted;
- manual submit and duplicate click;
- Bridge/Core disconnect/reconnect;
- parsed/unresolved/failed display using controlled isolated inputs;
- pause/resume/unbind.

Mark any step requiring a new real ChatGPT prompt as manual and not executed automatically. Do not invoke paid Provider without confirmation.

- [ ] **Step 3: Add troubleshooting matrix**

Map user symptoms to safe diagnostics: bridge page absent, Core absent, Endpoint bind failure, mismatch, no Session ID, waiting Parser, unknown result, invalid marker, extension reload requiring page refresh. Do not instruct direct SQLite editing.

- [ ] **Step 4: Draft the final report structure**

Report sections exactly cover:

1. implementation overview;
2. architecture reuse;
3. binding model;
4. dispatch protocol;
5. idempotency;
6. Core query authorization/design;
7. UI;
8. automated tests;
9. real browser evidence;
10. known limits;
11. manual acceptance;
12. Stage C reusable interfaces;
13. changed files;
14. Git status.

Use layered Supervisor summary first, then technical evidence.

- [ ] **Step 5: Check documentation links and commands**

```bash
python -B -m contextflow_core.receiver --help
python -B -m contextflow_core.parser_cli --help
```

Expected: both exit 0 and documented flags match actual help.

### Task 10: Full verification, review, and delivery stop

**Files:**
- Update: `reports/C-01-supervisor-binding.md`
- Update evidence references only; no Stage C files

- [ ] **Step 1: Run Python full regression**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests -q -p no:cacheprovider
```

Expected: all pass; record exact test/subtest count.

- [ ] **Step 2: Run JavaScript syntax checks**

```bash
node --check contextflow-extension/dispatch_protocol.js
node --check contextflow-extension/background.js
node --check contextflow-extension/content.js
node --check contextflow-extension/bridge_relay.js
node --check contextflow-extension/popup.js
```

Expected: all exit 0.

- [ ] **Step 3: Run Python compile checks**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m py_compile \
  contextflow_core/supervisor_control.py \
  contextflow_core/receiver.py \
  contextflow-extension/bridge_server.py \
  experiments/playwright-probe/c01_supervisor_test.py \
  experiments/playwright-probe/c01_fake_e2e.py
```

Expected: exit 0 and no tracked bytecode artifacts.

- [ ] **Step 4: Run browser suites**

```bash
python -B experiments/playwright-probe/c01_dispatch_protocol_test.py
python -B experiments/playwright-probe/c01_supervisor_test.py
python -B experiments/playwright-probe/cfadapter001_test.py
python -B experiments/playwright-probe/c01_fake_e2e.py
```

Expected: all pass. Distinguish Mock browser/Fake Parser from real ChatGPT page.

- [ ] **Step 5: Invoke verification-before-completion**

Re-run any command required by the skill and use only fresh output in completion claims.

- [ ] **Step 6: Invoke requesting-code-review and security-review**

Review current C-01 diff for correctness and security. Fix only verified C-01 defects, then rerun affected tests. Pay special attention to:

- webpage→management privilege escalation;
- status enumeration across Sessions;
- submission identity divergence;
- anchor history replay;
- Bridge late-response ownership;
- content/path/credential leakage.

- [ ] **Step 7: Confirm scope and workspace safety**

```bash
git diff --name-only
git status --short --branch
```

Expected: no C-02 Adapter prototype/evidence modified, no Task/Dispatcher/Agent runtime implementation, no commit/push/merge, and source workspace user files untouched.

- [ ] **Step 8: Finalize report with actual evidence**

Fill exact changed-file list, test counts, commands, artifact paths, real browser status, paid Provider status, known limitations, manual steps, and Git status. Never claim real ChatGPT E2E if only Mock was run.

- [ ] **Step 9: Deliver and stop**

Return the requested summary and stop C-01 work pending joint C-01/C-02 Stage C prerequisite acceptance.
