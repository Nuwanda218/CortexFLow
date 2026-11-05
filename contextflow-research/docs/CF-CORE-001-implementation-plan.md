# CF-CORE-001 ChatGPT Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build persistent ChatGPT Endpoint binding, strict runtime resolution, normalized message reading, recovery semantics, automated tests, and one real-page verification.

**Architecture:** A lightweight Python Core owns durable endpoint identity and calls the existing WebSocket Adapter through a narrow protocol. The Adapter remains responsible for browser discovery and DOM structure; Core performs exact identity matching, lifecycle classification, and stable output normalization.

**Tech Stack:** Python 3.10 standard library (`dataclasses`, `enum`, `json`, `asyncio`, `unittest`) plus the already-installed `websockets`; existing MV3 JavaScript Adapter.

**Workspace constraint:** Work in the current dirty workspace because the uncommitted v0.5.0 Adapter changes are the required baseline. Do not clean, overwrite, stage, or commit unrelated changes. No commits are made by this plan.

---

## File map

- Create `contextflow-research/contextflow_core/__init__.py`: public API exports.
- Create `contextflow-research/contextflow_core/models.py`: endpoint, resolution, discovery, and snapshot models.
- Create `contextflow-research/contextflow_core/errors.py`: stable structured exceptions.
- Create `contextflow-research/contextflow_core/adapter.py`: Adapter Protocol and WebSocket implementation.
- Create `contextflow-research/contextflow_core/registry.py`: atomic JSON registry.
- Create `contextflow-research/contextflow_core/service.py`: endpoint service.
- Modify `contextflow-research/contextflow-extension/content.js`: add turn group/position fields without removing existing fields.
- Create `contextflow-research/tests/core/fakes.py`: deterministic fake Adapter.
- Create `contextflow-research/tests/core/test_models.py`.
- Create `contextflow-research/tests/core/test_registry.py`.
- Create `contextflow-research/tests/core/test_service.py`.
- Create `contextflow-research/tests/core/test_adapter.py`.
- Create `contextflow-research/experiments/playwright-probe/cfcore001_real.py`: real-page verification.
- Create `contextflow-research/reports/CF-CORE-001.md`: final evidence and limitations.

### Task 1: Define models and errors

- [ ] Write failing tests in `tests/core/test_models.py` for enum serialization, discovery normalization, `fallback-turn-5:2:assistant` fields supplied by Adapter, and snapshot `history_scope` semantics.
- [ ] Run `python -m unittest tests.core.test_models -v`; confirm import failure because `contextflow_core` does not exist.
- [ ] Implement `models.py` with `EndpointState`, `EndpointBinding`, `DiscoveredSession`, `EndpointLocation`, `EndpointResolution`, `NormalizedMessage`, and `EndpointSnapshot` dataclasses. Implement `errors.py` with `ContextFlowError(code, message, retryable, details)`.
- [ ] Re-run the model tests and confirm they pass.

Required public shapes:

```python
class EndpointState(str, Enum):
    AVAILABLE = "available"
    OFFLINE = "offline"
    AMBIGUOUS = "ambiguous"

@dataclass(frozen=True)
class EndpointBinding:
    endpoint_id: str
    provider: str
    native_session_id: str
    session_kind: str | None
    created_at: str
    updated_at: str
```

### Task 2: Implement persistent registry with TDD

- [ ] Write failing tests for new registration, idempotent repeated registration, two different sessions, save/reload identity preservation, atomic replacement, invalid schema, and duplicate persisted native identity.
- [ ] Run `python -m unittest tests.core.test_registry -v`; confirm failures are caused by the missing registry.
- [ ] Implement `EndpointRegistry(path)` with an in-memory index keyed by `(provider, native_session_id)`, UUID endpoint IDs, schema version `1`, and `tempfile + os.replace` atomic writes.
- [ ] Make corrupt or duplicate data raise `REGISTRY_CORRUPT`; never silently reset it.
- [ ] Re-run registry tests and the model tests.

Required API:

```python
registry.get(endpoint_id) -> EndpointBinding | None
registry.find(provider, native_session_id) -> EndpointBinding | None
registry.register(provider, native_session_id, session_kind=None) -> tuple[EndpointBinding, bool]
registry.all() -> list[EndpointBinding]
```

### Task 3: Implement Adapter boundary with TDD

- [ ] Write failing tests around a local in-process WebSocket server for request IDs, success envelopes, Adapter errors, timeouts, disconnecting pending calls, reconnect-after-disconnect, `list_sessions`, and `get_snapshot(session_id, tab_id)` payloads.
- [ ] Run `python -m unittest tests.core.test_adapter -v`; confirm the Adapter module is missing.
- [ ] Define `ChatGPTAdapter` Protocol with `list_sessions`, `get_snapshot`, `connect`, and `close`.
- [ ] Implement `WebSocketChatGPTAdapter` using the existing bridge envelope and unique request IDs. A dropped connection must fail all pending futures with `BRIDGE_DISCONNECTED`; a later idempotent Core call may call `connect()` again.
- [ ] Re-run Adapter tests.

### Task 4: Implement discovery and binding with TDD

- [ ] Add fake Adapter scenarios for ready sessions, drafts, failed probes, duplicate tabs, and bridge errors.
- [ ] Write failing service tests for normalized discovery, `bindable=false` without Session ID, unique binding, idempotent repeat binding, missing target, duplicate target, and no silent replacement.
- [ ] Run the focused service tests and confirm expected failures.
- [ ] Implement `EndpointService.discover()` and `bind(session_id)` using exact Session ID equality only.
- [ ] Re-run focused and complete Core tests.

Required behavior:

```python
await service.bind(sid)
# -> BindResult(endpoint=<binding>, created=True|False)
# zero match -> ContextFlowError("SESSION_NOT_FOUND", ...)
# multiple matches -> ContextFlowError("AMBIGUOUS_SESSION", ...)
```

### Task 5: Implement lifecycle resolution with TDD

- [ ] Write failing tests for available, closed/offline, duplicate/ambiguous, Adapter unavailable, content script unavailable on the last tab, tab ID replacement after refresh, and recovery using the same Endpoint ID.
- [ ] Run focused resolution tests and confirm failures.
- [ ] Implement `resolve(endpoint_id)` so operational unavailability returns an `EndpointResolution` instead of deleting the binding or throwing away diagnostic evidence. Unknown Endpoint remains `ENDPOINT_NOT_FOUND`.
- [ ] Ensure the unique candidate is chosen only by exact `native_session_id`; `tab_id` is never used to substitute another Session.
- [ ] Re-run focused and complete Core tests.

### Task 6: Add Adapter turn normalization with TDD

- [ ] Extend a mock HTML test with `fallback-turn-5:0:user` and `fallback-turn-5:2:assistant`; assert both yield `turn_group_id="fallback-turn-5"` and preserve positions `0` and `2`.
- [ ] Run the test before modifying `content.js`; confirm the new fields are absent.
- [ ] Add a small `parseTurnKey` helper in `content.js` using `^(.*):(\d+):(user|assistant)$`, and emit `turn_group_id` and integer `turn_position` alongside the existing `turn_key`.
- [ ] Re-run the focused Adapter test plus CF-DEV-001/002 snapshot tests.

### Task 7: Implement normalized read and safe recovery with TDD

- [ ] Write failing tests for available reads, normalized messages, raw evidence retention, `history_scope="rendered_dom"`, no deletion inference, `SESSION_CHANGED` during read, one safe reconnect/re-resolve attempt, persistent failure, and ambiguity after the first resolve.
- [ ] Run focused read tests and confirm failures.
- [ ] Implement `read(endpoint_id)` to resolve first, call `get_snapshot` with both exact IDs, verify the returned Session, normalize fields, and retry at most once for retryable read-only transport/routing failures.
- [ ] Do not add send or wait behavior.
- [ ] Re-run all Core tests.

### Task 8: Regression and real-page validation

- [ ] Run existing CF-DEV-002, CF-DEV-003, and CF-DEV-004 Mock suites and record their separate pass counts.
- [ ] Run JavaScript syntax checks and Python compilation.
- [ ] Implement `cfcore001_real.py` to discover a caller-selected stable Session, bind it, read it, print the Endpoint ID, pause for or invoke an exact-tab refresh, then read again using the same Endpoint ID. It must write structured evidence without message bodies.
- [ ] Start/use the existing Bridge and a real ChatGPT test page; execute the real script once. Do not report Mock success as real-page success.
- [ ] If the exact real page or Bridge relay is unavailable, report the precise blocker; do not fabricate completion.

### Task 9: Documentation and final verification

- [ ] Update `docs/agent-usage.md` with the Core Endpoint API while keeping the raw Adapter section.
- [ ] Write `reports/CF-CORE-001.md` with completed capabilities, architecture changes, Mock results, real-page results, known limitations, and CF-CORE-002 readiness.
- [ ] Run the full verification command from repository root:

```powershell
python -m unittest discover -s contextflow-research/tests -p "test_*.py" -v
python -m py_compile contextflow-research/contextflow_core/*.py contextflow-research/experiments/playwright-probe/cfcore001_real.py
node --check contextflow-research/contextflow-extension/content.js
node --check contextflow-research/contextflow-extension/background.js
git diff --check
```

- [ ] Compare the implementation against every CF-CORE-001 acceptance item and explicitly list any unmet item.
- [ ] Inspect `git status --short` and separate pre-existing changes from files created or modified for CF-CORE-001.

