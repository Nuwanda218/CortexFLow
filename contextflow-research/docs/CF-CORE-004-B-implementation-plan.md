# CF-CORE-004 B1→B2 Implementation Plan

> **For inline execution in this session:** REQUIRED SUB-SKILLS: use `test-driven-development` for each behavior, `systematic-debugging` for unexpected failures, `requesting-code-review` at B1/B2 checkpoints, and `verification-before-completion` before final reporting. The user authorized uninterrupted B1→B2 execution. Do not commit, push, merge, migrate a user database, or enter stage C.

**Goal:** Build and verify `Supervisor Message → SQLite → Parser Worker → LLM Parser → ParseResult → SQLite`, first with a reliable Fake-backed B1 foundation and then with a configuration-backed Agent Directory plus a real Anthropic Messages-compatible provider for B2.

**Architecture:** Keep the stage-0 `InstructionParser` pure and make SQLite orchestration a separate worker/repository boundary. A shared Schema owner atomically creates or explicitly migrates to v2; one parse-job row carries lease, fencing, budget and one formal ParseResult. B2 injects a trusted directory and provider-backed parser through the same worker interface, so it adds no second state machine.

**Tech Stack:** Python 3.10, stdlib `sqlite3`/`asyncio`/`dataclasses`/`json`, existing ContextFlow contracts, pytest/unittest, official Anthropic Python SDK for the one real provider. Fake-first tests use no network or credentials.

**Sources:** `docs/CF-CORE-004-B1-spec.md`, `reports/CF-CORE-004-B-investigation.md`, and the user's CF-CORE-004-B2 task. Source baseline: `76afa1c`; pre-existing untracked artifacts and user docs remain untouched.

---

## File map

| Path | Responsibility |
|---|---|
| `contextflow_core/database_schema.py` | Exact v1/v2 DDL, strict validation, empty-v2 creation and explicit v1→v2 maintenance entry point |
| `contextflow_core/message_store.py` | Preserve phase-A behavior while delegating Schema ownership |
| `contextflow_core/parse_result_codec.py` | Canonical, strict ParseResult/ErrorInfo persistence codec |
| `contextflow_core/parse_jobs.py` | ParseJob state, claim/fence token, Repository, formal-result reader |
| `contextflow_core/fake_parser.py` | Deterministic scripted InstructionParser for B1/fault tests |
| `contextflow_core/parser_worker.py` | Discovery, claim, call authorization, timeout/retry/finalize/recovery lifecycle and CLI composition |
| `contextflow_core/agent_directory.py` | Exact JSON config validation, canonical selectors/aliases and stable AgentId resolution |
| `contextflow_core/instruction_parser.py` | Prompt schema, raw-output validation, grounding/order/target checks, ParseResult construction |
| `contextflow_core/anthropic_provider.py` | Official SDK adapter, environment config, timeout/error mapping and safe diagnostics |
| `requirements-core-b2.txt` | Reproducible official SDK dependency pin established from the installed tested version |
| `tests/core/test_database_schema.py` | Schema creation/migration/concurrency/failure tests |
| `tests/core/test_parse_result_codec.py` | Strict codec and corruption tests |
| `tests/core/test_parse_jobs.py` | Repository state, competing claims, fencing, budget and atomic finalize tests |
| `tests/core/test_parser_worker.py` | Fake lifecycle, timeout/cancel/recovery and B1 chain tests |
| `tests/core/test_agent_directory.py` | Directory config/name/alias tests |
| `tests/core/test_anthropic_provider.py` | SDK response/error/timeout/cancellation tests with injected fake client |
| `tests/core/test_instruction_parser.py` | Prompt-injection, raw schema, target, order and omission tests |
| `tests/core/test_parser_integration.py` | B2 Directory+Provider+Parser+Worker+SQLite deterministic integration |
| `tests/core/test_message_store.py` | Minimal v2 compatibility additions; all existing phase-A assertions retained |
| `docs/CF-CORE-004-B1-usage.md` | Isolated Fake worker and explicit migration usage |
| `docs/CF-CORE-004-B2-usage.md` | Directory/provider env setup, launch and result query |
| `reports/CF-CORE-004-B2.md` | B1/B2 implementation evidence, real/fake distinction and stage-C handoff |

No public stage-0 contract file is changed unless an implementation proof shows it is unavoidable; the current design does not require such a change.

---

### Task 1: Lock the executable baseline

**Files:**
- Modify: `docs/CF-CORE-004-B1-spec.md`
- Create: `docs/CF-CORE-004-B-implementation-plan.md` (this file)

- [ ] **Step 1: Validate B1 spec structure and references**

Run a Python checker that asserts six work packages, B1-T01…T16, even code fences, no trailing whitespace and every `path:start-end` citation lies within the actual file.

- [ ] **Step 2: Record baseline tests without claiming new behavior**

Run:

```bash
cd D:/CortexFlow/contextflow-research
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/contracts tests/core -q -p no:cacheprovider
```

Expected: existing tests pass; this is only the pre-change baseline.

- [ ] **Step 3: Preserve the workspace boundary**

Run `git status --short`; record the B1 spec/report as our files and leave `_artifacts/*.sqlite3*` plus root user docs untouched. Do not stage or commit.

### Task 2: Shared Schema v2 and explicit migration

**Files:**
- Create: `contextflow_core/database_schema.py`
- Modify: `contextflow_core/message_store.py`
- Create: `tests/core/test_database_schema.py`
- Modify: `tests/core/test_message_store.py`

- [ ] **Step 1: RED — empty-v2 and v1-migration tests**

Tests construct a real temporary v1 database from the exact former DDL and assert:

```python
assert read_user_version(path) == 1
migrate_to_v2(path)
assert read_user_version(path) == 2
assert snapshot_messages(path) == before
```

Also assert a new empty file opened by `SQLiteMessageStore` is v2, while normal open of known v1 raises `MESSAGE_STORE_MIGRATION_REQUIRED` without changing journal/schema/version.

- [ ] **Step 2: Verify RED**

```bash
python -B -m pytest tests/core/test_database_schema.py tests/core/test_message_store.py -q -p no:cacheprovider
```

Expected: imports/behaviors fail because `database_schema.py` and v2 do not exist.

- [ ] **Step 3: GREEN — exact DDL and validator**

Implement constants for exact v1 messages DDL/indexes and exact v2 parse DDL/indexes. `ensure_schema_v2(connection)` handles empty v0/v2 only; `migrate_path_to_v2(path, busy_timeout)` is the only v1 upgrade path. Both recheck after `BEGIN IMMEDIATE`, validate row/index/trigger definitions and set `user_version=2` only inside the successful transaction.

- [ ] **Step 4: GREEN — MessageStore delegation**

Retain the current connection/lock/accept/read/mark implementation; replace only local Schema constants/init validation with the shared owner. Preserve existing error conversion and readback checks.

- [ ] **Step 5: Verify GREEN and strict failure cases**

Add tests for nonempty-v0, version 99, changed CHECK/index predicate, reserved-object collision, concurrent migrations, injected DDL failure and child-process death before commit. Run the focused files until all pass.

### Task 3: Strict ParseResult persistence codec

**Files:**
- Create: `contextflow_core/parse_result_codec.py`
- Create: `tests/core/test_parse_result_codec.py`

- [ ] **Step 1: RED — exact encode/decode tests**

Cover parsed, unresolved, failed, ordered multi-instruction, frozen ErrorInfo details, Unicode and fingerprint recomputation. Reject duplicate JSON keys, unknown/missing fields, nonfinite numbers, partial instructions, mismatched status/error and tampered fingerprint.

- [ ] **Step 2: Verify RED**

```bash
python -B -m pytest tests/core/test_parse_result_codec.py -q -p no:cacheprovider
```

Expected: module import fails.

- [ ] **Step 3: GREEN — explicit field mapping**

Implement canonical JSON helpers that serialize only stage-0 fields and decode with `object_pairs_hook` duplicate rejection. Reconstruct `ParsedInstruction`, `ErrorInfo`, `ParseResult`; never use `dataclasses.asdict()` and never return a valid subset of corrupt data.

- [ ] **Step 4: Verify GREEN**

Run the focused test and contract model tests.

### Task 4: ParseJob discovery and competing claims

**Files:**
- Create: `contextflow_core/parse_jobs.py`
- Create: `tests/core/test_parse_jobs.py`

- [ ] **Step 1: RED — state constraints and lazy discovery**

Create messages through the real Store; assert only pending Supervisor rows get one job, simultaneous repositories cannot duplicate, completed+parsed/pending does not recreate, and malformed direct SQL rows fail CHECK/FK constraints.

- [ ] **Step 2: Verify RED**

Expected: Repository and job types are missing.

- [ ] **Step 3: GREEN — internal records and serialized async DB calls**

Implement `ParseJobState`, `ParseClaim`, `StoredParseResult`, `AttemptAuthorization` and `SQLiteParseJobRepository`. Open a separate v2-validated connection with the same WAL/FULL/FK/busy policy. Async DB calls shield and await their worker thread before propagating cancellation.

- [ ] **Step 4: RED — two-connection claim race**

Use two Repository instances and a barrier to compete for one job. Assert only one valid token, generation increments on expiry, a live Lease cannot be stolen, and `find_claim(owner, request_id)` recovers the committed token after a canceled await.

- [ ] **Step 5: GREEN — claim transaction**

Inside `BEGIN IMMEDIATE`, first recover current owner/request, then select due ready/retry_wait/expired-processing with pending-Supervisor cross-table check, update generation/owner/request/expiry from one SQLite time sample, read back and commit.

- [ ] **Step 6: Verify GREEN**

Run state/discovery/claim tests using distinct real SQLite connections, including one real-clock expiry test.

### Task 5: Attempt budget, retry and fenced formal result

**Files:**
- Modify: `contextflow_core/parse_jobs.py`
- Modify: `tests/core/test_parse_jobs.py`

- [ ] **Step 1: RED — one authorization per generation**

Assert `begin_attempt` increments only once, duplicate authorization does not permit another call, max attempts survives reopen/config change, and a budget-exhausted job can still be claimed only for final failure.

- [ ] **Step 2: Verify RED**

Expected: authorization methods absent/failing.

- [ ] **Step 3: GREEN — fenced begin/retry**

Every owner write atomically checks message_id/state/owner/request/generation/strictly-live-Lease/pending-Supervisor. `schedule_retry` persists safe ErrorInfo and due time then clears claim fields; SQLite busy does not change attempt count.

- [ ] **Step 4: RED — atomic finalization matrix**

Assert parsed+completed leaves Message pending; unresolved/failed atomically block it with the same ErrorInfo. Reject old generation, expired owner even before takeover, changed Message state, second different result and write/commit/trigger failures. Same-result confirmation returns the stored record.

- [ ] **Step 5: GREEN — finalize and reader**

Use the codec in one short transaction, enforce formal-result uniqueness, update Message directly in the same transaction when blocked, verify readback before COMMIT, and expose only `get_final_result(message_id)` plus diagnostic `get_job(message_id)`.

- [ ] **Step 6: Verify GREEN**

Run focused Repository/codec/MessageStore tests. Add late-old-worker, rollback failure and COMMIT-response-loss simulations.

### Task 6: B1 Worker and deterministic Fake

**Files:**
- Create: `contextflow_core/fake_parser.py`
- Create: `contextflow_core/parser_worker.py`
- Create: `tests/core/test_parser_worker.py`
- Create: `docs/CF-CORE-004-B1-usage.md`

- [ ] **Step 1: RED — Fake scripts and one-cycle worker**

Tests script parsed/unresolved/failed/retryable error/nonretryable error/block/cancel/wrong-type outcomes and assert Worker uses the existing `InstructionParser` signature.

- [ ] **Step 2: Verify RED**

Expected: modules missing.

- [ ] **Step 3: GREEN — one-cycle lifecycle**

Implement `run_once`: discover → claim → read Message → begin attempt → transaction-free parser task → validate result → retry/finalize. Generate process owner once and claim request per call. Do not use stage-0 `ProcessResult` for parsed+pending.

- [ ] **Step 4: RED — timeout, cancellation and late result**

Use Events to prove a parser task is not inside a DB write transaction, timeout/stop does not permit its late result to commit, canceled DB awaits settle before close, and an uncooperative parser stops that Worker from taking more jobs while another instance can recover after Lease expiry.

- [ ] **Step 5: GREEN — bounded lifecycle and CLI**

Implement execution timeout, submit margin, exponential capped retry, polling, stop, in-flight task observation and safe diagnostics. Fake CLI requires explicit `--database`; it must not use the default Store path or network.

- [ ] **Step 6: B1 acceptance checkpoint**

Run B1-T01…T16 focused tests, then contracts/core suite. Inspect diff and run an independent code review. Fix confirmed Critical/Important findings with RED→GREEN tests. Only after this checkpoint proceed to B2.

### Task 7: Trusted configuration-backed Agent Directory

**Files:**
- Create: `contextflow_core/agent_directory.py`
- Create: `tests/core/test_agent_directory.py`
- Create/update: `docs/CF-CORE-004-B2-usage.md`

- [ ] **Step 1: RED — exact config validation**

Use JSON:

```json
{
  "schema_version": 1,
  "agents": [
    {"agent_id": "claude-code-local", "name": "Claude Code", "aliases": ["Claude"], "available": true},
    {"agent_id": "codex-local", "name": "Codex", "aliases": ["OpenAI Codex"], "available": true}
  ]
}
```

Test canonical names, aliases, unknown, empty directory, offline known identity, wrong fields/types, duplicate AgentId, and NFKC+casefold collisions across names/aliases.

- [ ] **Step 2: Verify RED**

Expected: module missing.

- [ ] **Step 3: GREEN — immutable catalog**

Load exact fields, construct stable `AgentId`/`AgentDescriptor`, reject every duplicate normalized selector, and provide existing `known_agents/resolve/get_agent`. Add private immutable candidate snapshots carrying canonical name+aliases for prompt rendering; never read Endpoint/Session/Registry.

- [ ] **Step 4: Verify GREEN**

Run focused tests and existing AgentDirectory structural tests.

### Task 8: Real Anthropic Messages-compatible Provider Adapter

**Files:**
- Create: `contextflow_core/anthropic_provider.py`
- Create: `tests/core/test_anthropic_provider.py`
- Create: `requirements-core-b2.txt`

- [ ] **Step 1: Confirm protocol before code**

Public AgentRouter documentation states Claude Opus routes use Anthropic/custom base URL without appending `/v1`, credentials via `ANTHROPIC_AUTH_TOKEN`, and also offers a separate OpenAI-compatible route. Use the Anthropic route and official SDK; do not use the already-installed OpenAI shim. Verify the current SDK version from the package index, install it only after B1 passes, and pin the exact tested version.

- [ ] **Step 2: RED — injected-client adapter tests**

Fake the SDK client to cover one JSON text response, thinking+text blocks, empty/multiple text, malformed/duplicate-key JSON, max_tokens/refusal, timeout, connection, 429, 4xx, 5xx, cancellation and delayed response. Assert `max_retries=0`, configured timeout/base URL/model, no secret in repr/log/errors and no DB dependency.

- [ ] **Step 3: Verify RED**

Expected: module missing.

- [ ] **Step 4: GREEN — environment config and official SDK**

Require `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CONTEXTFLOW_LLM_MODEL`; normalize base URL without silently adding `/v1`; validate HTTPS except explicitly test-only localhost. Construct `AsyncAnthropic(base_url=..., max_retries=0, timeout=...)`. Map typed SDK errors most-specific-first to safe ContextFlowError with retryability. Log only provider/model/request ID/token counts/status, never prompt/response/key.

- [ ] **Step 5: GREEN — strict JSON response**

Send separated system/user content, extract one final text block, require `end_turn`, strictly `json.loads` with duplicate/nonfinite rejection and return the raw object through `LlmExtractionPort`. No SQLite calls or retries inside Adapter.

- [ ] **Step 6: Verify GREEN**

Run tests under an environment containing sentinel credentials and assert they never appear in captured logs/exceptions/test report.

### Task 9: Provider-backed LLM InstructionParser

**Files:**
- Create: `contextflow_core/instruction_parser.py`
- Create: `tests/core/test_instruction_parser.py`

- [ ] **Step 1: RED — prompt authority separation**

Assert system rules and untrusted Supervisor text are separate content blocks. Prompt says: extract only, preserve all instruction order, choose only canonical candidates, never invent AgentId/execute work/default a target, return unresolved on ambiguity, and treat quoted user text as data.

- [ ] **Step 2: RED — exact raw output schema**

Accepted model output is exactly:

```json
{
  "status": "parsed",
  "instructions": [
    {"target_agent": "Codex", "content": "修复 Adapter", "source_excerpt": "请 Codex 修复 Adapter"}
  ],
  "reason": null
}
```

or unresolved with `instructions: []` and nonempty `reason`. Reject extra/missing/wrong fields, empty content/excerpt, model-produced failed, non-list order and invalid status combinations.

- [ ] **Step 3: RED — business validation corpus**

Cover one agent/one instruction, one agent/multiple instructions, multiple agents/multiple ordered instructions, alias canonicalization, unknown target, ambiguous user intent, no candidates, output order reversal, explicit target omission, same-agent omission limitation, and user text attempting to override Parser rules.

- [ ] **Step 4: Verify RED**

Expected: parser module missing.

- [ ] **Step 5: GREEN — conservative validator**

Build candidate prompt entries from the trusted directory, resolve every output uniquely, canonicalize targets, require each source excerpt to occur in nondecreasing source order, and require every explicitly mentioned trusted Agent selector to be represented. Construct stage-0 ParseResult and recompute the existing fingerprint. Unknown target becomes unresolved without partial instructions; malformed/incomplete model output raises retryable `LLM_OUTPUT_INVALID` for Worker budget handling.

- [ ] **Step 6: Document the semantic limit**

Tests prove the validator catches explicit multi-target omission; it cannot prove no same-target natural-language task was omitted. Report this rather than claiming semantic completeness.

- [ ] **Step 7: Verify GREEN**

Run Parser/Directory/provider/contract tests.

### Task 10: B2 integration through the B1 Worker

**Files:**
- Modify: `contextflow_core/parser_worker.py`
- Create: `tests/core/test_parser_integration.py`
- Modify: `docs/CF-CORE-004-B2-usage.md`

- [ ] **Step 1: RED — deterministic full B2 chain**

Submit a Supervisor Envelope containing at least two targets/two instructions through the real MessageStore. Inject ConfiguredAgentDirectory + Fake provider response + LLMInstructionParser into the real Worker. Assert formal ordered/canonicalized ParseResult persists, Message remains pending, source MessageId matches and restart makes zero additional provider calls.

- [ ] **Step 2: Verify RED**

Expected: composition/config path incomplete.

- [ ] **Step 3: GREEN — one composition root**

Worker obtains the immutable candidate snapshot before authorizing/calling Parser and passes it through the unified interface. The Fake Parser and real Parser share exactly the same Worker lifecycle. Add CLI mode `--provider fake|anthropic`, requiring explicit database, directory and environment config; default tests use fake.

- [ ] **Step 4: Verify reliability reuse**

Repeat old-generation, timeout, budget exhaustion, DB commit failure, competing Worker and restart tests with the real parser component over a Fake provider. Assert no parallel B2 state table or direct Message mark.

### Task 11: Real Provider validation and reproducible demo

**Files:**
- Create/update: `docs/CF-CORE-004-B2-usage.md`
- Create: `reports/CF-CORE-004-B2.md`
- Test artifacts: a new isolated temporary/artifact directory only if needed, never the phase-A manual DB

- [ ] **Step 1: Fake end-to-end demo**

Create an isolated database/Registry/Directory; receive a two-agent/two-instruction message, run Fake-provider Worker twice, query Message/ParseJob/formal result, and save content-free evidence (IDs/status/count/order/targets, no secrets).

- [ ] **Step 2: Authenticated read-only capability check**

Use the user-authorized credential only in one process environment. Call models or a minimal endpoint to confirm an available configured Claude model and Anthropic Messages compatibility. Do not print headers/token or write it to disk. If the Router does not expose models, use its documented model string and record that limitation.

- [ ] **Step 3: Explicit low-cost real extraction**

Run one two-agent/two-instruction message through the real adapter/Parser/Worker in a fresh isolated DB with strict max output and no SDK retry. This is user-authorized paid external I/O. Record request ID/model/status/token counts only; do not record credentials or raw Provider diagnostics containing sensitive data.

- [ ] **Step 4: Classify evidence honestly**

Separately label: Fake full chain; real API Parser chain; real ChatGPT Extension→Core chain. Do not infer the third from the first two. If credentials/proxy/model fail, keep Fake verified and report real API as not passed with a sanitized error.

### Task 12: Documentation, independent review and final verification

**Files:**
- Finalize: `docs/CF-CORE-004-B1-usage.md`
- Finalize: `docs/CF-CORE-004-B2-usage.md`
- Finalize: `reports/CF-CORE-004-B2.md`
- Update only if needed: `docs/CF-CORE-004-B1-spec.md`

- [ ] **Step 1: Usage and migration safety**

Document env names with placeholders, directory JSON, explicit temp DB commands, maintenance migration command, Worker launch and safe SQL/API query helpers. State that real v1 maintenance requires stopped services + consistent backup + separate approval.

- [ ] **Step 2: Report all required evidence**

Report modules/interfaces, directory, provider choice, prompt/validation, persistence flow, B1 fixes, tests, demo, limitations, joint-stage-B recommendation and stage-C Reader interface. Include 0 secrets.

- [ ] **Step 3: Independent review checkpoint**

Review the entire uncommitted diff for correctness and scope. Verify each finding against code; fix confirmed Critical/Important issues one at a time with failing tests first.

- [ ] **Step 4: Focused and full verification**

```bash
cd D:/CortexFlow/contextflow-research
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/contracts tests/core -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests -q -p no:cacheprovider
```

Also run the Fake e2e command and, if successful in Task 11, the explicitly enabled real-provider test once. Scan tracked/untracked deliverables for the secret value and redact any occurrence before reporting.

- [ ] **Step 5: Final workspace evidence**

Run `git status --short`, `git diff --check`, and list exact modified/new files. Do not stage, commit, push, merge or clean the user's untracked files. Stop after the B1+B2 report; do not begin stage C.
