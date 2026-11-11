# C-03 统一 Agent Runtime Interface 与正式 Adapter 实现

- 任务编号：C-03
- 日期：2026-11-11（Asia/Shanghai）
- 工作树：`D:\CortexFlow`，分支 `feat/cf-core-003-message-store`
- 代码：`contextflow-research/contextflow_core/agent_runtime/`
- 测试：`contextflow-research/tests/agent_runtime/`
- 使用说明：`contextflow-research/docs/CF-C03-agent-runtime-usage.md`
- 真实证据：`contextflow-research/_artifacts/c03-agent-runtime/evidence/`
- 结论性质：正式 Adapter 层 + 真实执行验证；**未创建 Task/Delivery，未实现 Dispatcher，未修改阶段 A/B 契约与 C-01 功能**。

> 脱敏说明：本报告不包含 Token、API Key、Cookie、Authorization 头或私人会话正文。会话标识仅保留用于复现。

---

## 1. 架构设计

### 1.1 分层

```
未来 Core Dispatcher
        │  (TaskId / DeliveryId / 调度 / 幂等 / 持久化 / 恢复)
        ▼
Agent Runtime Interface（本阶段）
        │  capabilities / start / inspect / cancel / wait / result
        ▼
┌───────────────┬───────────────┬───────────────┬───────────────┐
│ CodexCli      │ ClaudeCli     │ CodexRelay    │ DshRuntime    │
│ process       │ process       │ process       │ service       │
└───────────────┴───────────────┴───────────────┴───────────────┘
        │
        ▼
本地执行日志 ExecutionJournal（幂等安全网，非权威 Delivery Store）
```

### 1.2 职责边界

**Core（未来）负责**：Task/Delivery 身份、Task 持久化、投递调度、幂等与重试策略、执行记录、报告回传、Supervisor 审核状态。

**Adapter（本阶段）负责**：连接/启动具体 Agent、把 ExecutionRequest 翻译为原生调用、维护/关联原生会话、读取执行状态、收集结果、实现可支持的取消、提供诊断与能力声明。

**Adapter 明确不做**：判断业务任务是否完成、批准验收、决定是否重新投递、创建 Task/Delivery。

### 1.3 关键设计判断

1. **Execution ≠ Task**：`ExecutionState.FINISHED` 只表示 Agent 侧执行结束，不表示 Supervisor 的目标达成。业务验收必须由报告层/ Supervisor 决定。
2. **能力声明而非统一能力**：不支持的操作用 `ADAPTER_UNSUPPORTED` 明确失败，不伪装成功。
3. **保守的 unknown**：进程被终止、超时、无终态事件时返回 `unknown`，不擅自标记 `failed` 或 `cancelled`。
4. **取消不回滚副作用**：所有快照都带 `side_effects_possible=true`，取消结果只描述“取消动作实际达成了什么”。
5. **身份四层分离**：Task（逻辑工作）→ Delivery（一次投递）→ Execution（一次实际运行）→ Session（原生上下文）。

---

## 2. 正式接口定义

### 2.1 核心类型

```python
class ExecutionState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

class FailureKind(str, Enum):
    NONE, RUNTIME_ERROR, PROTOCOL_ERROR, TIMEOUT, CANCELLED, UNKNOWN,
    CONCURRENCY_LIMIT, AUTHENTICATION, QUOTA_EXCEEDED,
    SESSION_UNAVAILABLE, INVALID_REQUEST

class SessionPolicy(str, Enum):
    NEW = "new"      # 必须创建新原生会话
    REUSE = "reuse"  # 必须复用给定原生会话
    AUTO = "auto"    # 有 native_session_id 则复用，否则新建

class CancelDisposition(str, Enum):
    REQUEST_SENT, LOCAL_PROCESS_TERMINATED, REMOTE_CONFIRMED_STOPPED,
    UNKNOWN, UNSUPPORTED
```

### 2.2 AgentCapabilities

```python
@dataclass(frozen=True)
class AgentCapabilities:
    runtime: str
    control_kind: ControlKind            # process | service | gui | none
    supports_new_session: bool
    supports_session_reuse: bool
    supports_resume: bool
    supports_streaming: bool
    supports_structured_result: bool
    supports_structured_failure: bool
    supports_native_cancel: bool
    supports_usage: bool
    supports_permission_profiles: bool
    supports_workspace_isolation: bool
    supported_permission_profiles: tuple[str, ...]
    max_concurrency: int | None
    evidence_status: EvidenceStatus      # verified | partial | unverified | blocked | unsupported
    notes: tuple[str, ...]
```

### 2.3 ExecutionRequest

```python
@dataclass(frozen=True)
class ExecutionRequest:
    runtime: str
    instruction: str
    workdir: str                 # 必须是绝对路径
    caller_ref: str              # 调用方关联标识（未来由 Dispatcher 传 Delivery id）
    session_policy: SessionPolicy = SessionPolicy.AUTO
    native_session_id: str | None = None
    timeout_s: float = 300.0
    permission_profile: str = "workspace_write"
    metadata: Mapping[str, Any] = {}
```

- `caller_ref` 是幂等命名空间，不是 TaskId/DeliveryId，也不替代原生 SessionId。
- `fingerprint()` 对 runtime/instruction/workdir/session policy/native session/permission profile 做 SHA-256；timeout 与 metadata 不参与（它们改变“怎么跑”，不改变“跑什么”）。

### 2.4 ExecutionHandle / ExecutionSnapshot / ExecutionResult / CancelResult

```python
@dataclass(frozen=True)
class ExecutionHandle:
    execution_id: str            # Adapter 生成，可持久化
    runtime: str
    caller_ref: str
    state: ExecutionState
    created_at: str
    native_session_id: str | None   # Agent 原生返回或调用方指定，可持久化
    session_policy: SessionPolicy
    resumable: bool
    pid: int | None              # 仅 process 型，进程重启后失效
    diagnostics: Mapping[str, Any]

@dataclass(frozen=True)
class ExecutionSnapshot:
    execution_id, runtime, caller_ref, state
    native_state: str | None
    native_session_id: str | None
    exit_code: int | None
    output_text: str | None
    error: Mapping | None
    started_at, ended_at: str | None
    usage: Mapping
    result_complete: bool
    side_effects_possible: bool
    retry_safe: bool
    diagnostics: Mapping

@dataclass(frozen=True)
class ExecutionResult:
    snapshot: ExecutionSnapshot
    failure_kind: FailureKind
    final_text: str | None
    cancel: CancelResult | None

@dataclass(frozen=True)
class CancelResult:
    execution_id: str
    disposition: CancelDisposition
    requested_at: str
    snapshot: ExecutionSnapshot
    confirmed_at: str | None
    note: str
```

### 2.5 Adapter 接口

```python
class AgentRuntimeAdapter(ABC):
    runtime: str
    capabilities: AgentCapabilities

    async def probe(self) -> dict
    async def start(self, request: ExecutionRequest) -> ExecutionHandle
    async def inspect(self, execution_id: str) -> ExecutionSnapshot
    async def cancel(self, execution_id: str) -> CancelResult
    async def wait(self, execution_id: str, timeout_s: float | None = None) -> ExecutionResult
    async def result(self, execution_id: str) -> ExecutionResult
    async def close(self) -> None
```

`wait` 是便利方法：轮询 `inspect` 直到终态；超时则调用 `cancel` 并返回 `failure_kind=TIMEOUT` 的 `unknown` 结果。核心接口保持 `capabilities/start/inspect/cancel` 四个基础操作。

### 2.6 错误类型

`AdapterError` 基类 + `InvalidRequest`、`UnsupportedOperation`、`RuntimeUnavailable`、`SessionUnavailable`、`ExecutionNotFound`、`ConcurrencyLimit`、`IdempotencyConflict`、`ProtocolError`。每个错误带 `code/message/retryable/details`，可序列化。`retryable` 只描述“同一调用是否可再试”，**不授权重新投递任务**。

---

## 3. 四种 Adapter 实现

### 3.1 Codex CLI（`adapters/codex_cli.py`，process）

- 启动：`codex exec --json -C <workdir> -s <sandbox> -c approval_policy="never" --skip-git-repo-check -o <last-message> <instruction>`
- 恢复：`codex exec resume <thread-id> --json -c approval_policy="never" -o <last-message> <instruction>`（cwd 由进程工作目录指定）
- 事件映射：`thread.started`→原生会话；`turn.started`→running；`item.completed`（agent_message/command_execution/file_change）→输出与工具证据；`turn.completed`→finished+usage；`turn.failed`→failed。
- 失败分类：403 单会话→`CONCURRENCY_LIMIT`；usage limit/quota→`QUOTA_EXCEEDED`；401/login→`AUTHENTICATION`；其余→`RUNTIME_ERROR`。
- 无终态事件：exit 0 → `UNKNOWN/PROTOCOL_ERROR`；exit≠0 → `FAILED/RUNTIME_ERROR`；被终止 → `UNKNOWN`。
- 权限档：`read_only`→`-s read-only`，`workspace_write`→`-s workspace-write`，`full_access`→`-s danger-full-access`。
- 已知环境风险：会加载用户 MCP/skills/AGENTS.md；生产部署建议隔离 `CODEX_HOME`。

### 3.2 Claude Code CLI（`adapters/claude_cli.py`，process）

- 启动：`claude -p <instruction> --output-format json --permission-mode <mode> --allowedTools <tools> --session-id <uuid>`
- 恢复：`--resume <session-id>`（同一 session_id，上下文连续）
- 结果解析：`session_id`、`is_error`、`subtype`、`stop_reason`、`result`、`duration_ms`、`total_cost_usd`、`usage`、`permission_denials`、`num_turns`。
- 终态映射：`is_error`/`subtype≠success`→failed；exit 0 + success→finished；无 result 对象→failed/protocol；被终止→unknown。
- 权限档：`read_only`→`acceptEdits` + `Read,Glob,Grep`；`workspace_write`→`acceptEdits` + 读写工具；`full_access`→`bypassPermissions` + Bash。
- 重要：CLI 成功（exit 0 / is_error=false）不代表任务目标达成；Adapter 只报告执行事实。

### 3.3 DSH（`adapters/dsh.py`，service）

- 连接：读取 `%LOCALAPPDATA%/DSH-Launcher/state.json` 的 capability URL，换 cookie 后调用 loopback JSON-RPC（不打印/不持久化该 URL）。
- 启动：`session/create {cwd}`（新会话）或复用给定 session；`session/prompt {sessionId, requestId, mode:queue, content}`。
- 状态：`session/projections`（running、tokenUsage、sessionStats、inbox）；`session/page`（user/assistant 消息、turn/end）。
- 终态：按 `turn/end.data.turn == 本次 turn` 匹配；`completed`→finished；`aborted`→cancelled；其他→failed；无 turn/end→unknown。
- 取消：`session/cancel` + 轮询直到 idle + 读取 `turn/end aborted/user` → `REMOTE_CONFIRMED_STOPPED`。
- 限制：`turn/end completed` 不代表业务成功；无流式订阅，仅轮询。

### 3.4 Codex-Relay（`adapters/codex_relay.py`，process）

- 复用 Codex CLI 实现；独立 `CODEX_HOME`（默认 `~/.codex-cli-relay`）、独立 journal 目录、独立会话空间。
- 命令默认 `D:\codex\codex-relay.cmd`（可用 `CF_C03_RELAY_COMMAND` 覆盖）。
- `max_concurrency=1`：渠道单会话限制，超出返回 `ADAPTER_CONCURRENCY_LIMIT`；403 单会话错误映射为 `CONCURRENCY_LIMIT`。
- 认证为渠道 API Key（由 Relay 自身配置提供），Adapter 不读取/不记录密钥。

---

## 4. Codex 桌面端处理策略

`adapters/codex_desktop.py` 是**能力声明 + 只读探测**，不是可执行 Adapter：

- `control_kind=gui`，所有执行能力为 `false`，`evidence_status=partial`。
- `probe()` 返回 Appx 版本、`~/.codex` 共享会话/队列是否存在。
- `start/inspect/cancel` 一律抛 `UnsupportedOperation`（`ADAPTER_UNSUPPORTED`）。
- 原因（C-02 实测）：桌面端是 Electron 客户端，后端为内置 `codex.exe app-server` 的 **stdio** 私有管道；无对外控制 API、无监听控制套接字；`codex://` 仅启动/聚焦应用；GUI 自动化脆弱。
- 明确不把桌面端与 Codex CLI / Relay 视为可互换 Endpoint。

---

## 5. Execution 状态模型

| 统一状态 | 含义 | Codex CLI | Claude CLI | DSH |
| --- | --- | --- | --- | --- |
| `pending` | 已接受，尚未开始 | 进程已启动但无事件 | 进程已启动但无结果 | prompt 已入队未运行 |
| `running` | 正在执行 | `turn.started` 后 | 进程存活 | `running=true` |
| `finished` | 执行结束（不代表业务成功） | `turn.completed` + exit 0 | `subtype=success` + exit 0 | `turn/end completed` |
| `failed` | 执行失败 | `turn.failed` / exit≠0 | `is_error`/`subtype≠success`/exit≠0 | `turn/end` 非 completed / `agent/error` |
| `cancelled` | 取消被原生确认 | 不支持（kill→unknown） | 不支持（kill→unknown） | `turn/end aborted/user` |
| `unknown` | 结果不可证 | kill/超时/无终态 | kill/超时/无终态 | 超时取消/无法确认 |

**保守规则**：

- 进程被 Adapter 终止 → `unknown`（即使 exit code 已知）。
- exit 0 但无终态事件 → `unknown` + `PROTOCOL_ERROR`。
- 超时 → `unknown` + `TIMEOUT` + `cancel` 结果。
- `unknown` 的 `retry_safe=false`、`side_effects_possible=true`。

---

## 6. 会话与执行身份模型

| 标识 | 生成方 | 可持久化 | 重启后有效 | 说明 |
| --- | --- | --- | --- | --- |
| `caller_ref` | 调用方（未来 Dispatcher） | 是 | 是 | 幂等命名空间，不是 TaskId |
| `execution_id` | Adapter | 是 | 是（journal） | 一次执行的身份 |
| `native_session_id` | Agent 原生或调用方指定 | 是 | 是 | 会话身份；**不代表可恢复任意历史执行状态** |
| `pid` | OS | 否 | 否 | 仅当前进程生命周期 |
| `requestId`（DSH） | Adapter | 是（诊断） | 是 | 仅用于关联，不保证去重 |
| `thread_id`（Codex） | Agent | 是 | 是 | 即原生会话 ID |
| `session_id`（Claude） | 调用方预生成或 Agent 返回 | 是 | 是 | 支持 `--resume` |

**关键区分**：

- 有 `native_session_id` ≠ 能恢复任意执行状态。Codex/Claude 只能 resume 会话上下文；DSH 只能继续会话，不能重放某次执行。
- `execution_id` 与 `native_session_id` 是多对一：同一会话可承载多次执行。
- 进程重启后，Adapter 只能从 journal 读到状态；非终态一律保守改写为 `unknown`。

---

## 7. 并发和幂等边界

### 7.1 幂等

- 正式实现：`ExecutionJournal`（JSON 文件 + 原子替换 + 线程锁）。
- 同一 `caller_ref` + 同一请求指纹 → 返回已有 execution（`duplicate_suppressed=true`），不启动第二次。
- 同一 `caller_ref` + 不同请求 → `ADAPTER_IDEMPOTENCY_CONFLICT`。
- 进程重启后，journal 中 `pending/running` 记录一律改写为 `unknown`；再次使用同一 `caller_ref` → `ADAPTER_IDEMPOTENCY_CONFLICT`，必须对账。
- **边界**：journal 只是 Adapter 本地安全网，不是持久化 Delivery Store。正式幂等仍由未来 Core Delivery Store 负责；Adapter 不决定是否创建新 Delivery。

### 7.2 并发

- `ConcurrencyGuard`：非阻塞计数；超过限制抛 `ConcurrencyLimit`（retryable），由 Core 决定稍后重试。
- Codex-Relay：`max_concurrency=1`（渠道硬限制）。
- Codex/Claude/DSH：默认无限制，但受账号配额/服务能力约束。
- 每个 runtime 使用独立 journal 子目录，避免会话空间/幂等空间混用。

---

## 8. 错误与取消语义

### 8.1 错误分类

| `FailureKind` | 触发条件 |
| --- | --- |
| `NONE` | 正常结束 |
| `RUNTIME_ERROR` | CLI/服务报告运行时错误（非配额/认证/并发） |
| `PROTOCOL_ERROR` | 无终态事件、无结果对象、事件不可解析 |
| `TIMEOUT` | Adapter 超时并终止 |
| `CANCELLED` | 原生取消被确认 |
| `UNKNOWN` | 被终止/结果不可证 |
| `CONCURRENCY_LIMIT` | Relay 单会话 403、并发守卫 |
| `AUTHENTICATION` | 401/未登录/认证失败 |
| `QUOTA_EXCEEDED` | usage limit / quota / rate limit |
| `SESSION_UNAVAILABLE` | 指定会话不存在/不可用 |
| `INVALID_REQUEST` | 参数/权限档/会话策略非法 |

### 8.2 取消语义

| Runtime | 取消动作 | `CancelDisposition` | 执行状态 |
| --- | --- | --- | --- |
| Codex / Claude / Relay | 终止本地进程树 | `LOCAL_PROCESS_TERMINATED` | `unknown`（无终态证据） |
| DSH | `session/cancel` + 等待 `turn/end aborted` | `REMOTE_CONFIRMED_STOPPED` | `cancelled` |
| 已终态执行 | 无操作 | `UNSUPPORTED` | 原状态 |
| 非本进程执行 | 无操作 | `UNSUPPORTED` | journal 状态 |

取消**不撤销**文件修改或其他副作用；`CancelResult.note` 明确说明这一点。

---

## 9. 配置说明

- 默认 journal：`~/.contextflow/agent-runtime/journal`（可用 `CF_C03_JOURNAL_ROOT` 覆盖）。
- 环境变量：`CF_C03_CODEX_COMMAND`、`CF_C03_CLAUDE_COMMAND`、`CF_C03_RELAY_COMMAND`、`CF_C03_RELAY_CODEX_HOME`、`CF_C03_DSH_LAUNCHER_STATE`、`CF_C03_DSH_POLL_INTERVAL`、`CF_C03_CONFIG`。
- JSON 配置支持 `journal_root`、`codex`、`claude`、`relay`、`dsh` 段。
- 凭据不进入配置/日志：Codex/Claude 使用各自 OAuth 存储，Relay 使用渠道密钥文件，DSH 使用 launcher capability URL（仅内存）。
- 工作目录必须显式绝对路径；不依赖隐式全局 cwd；不默认授予危险工具权限。

详见 `docs/CF-C03-agent-runtime-usage.md`。

---

## 10. 测试结果

### 10.1 单元 / 契约 / 生命周期测试（不调用真实 Agent）

```
python -m pytest tests/agent_runtime -q
58 passed, 4 skipped in 18.25s
```

覆盖：

- 契约校验：Request 字段、绝对路径、会话策略、超时、指纹、序列化。
- 状态转换：start→running→cancel/timeout→unknown；终态集合。
- JSON/JSONL 解析：回放 C-02 真实证据（Codex 成功/业务失败/403/被杀；Claude 成功/业务失败/被杀；DSH completed/aborted）。
- 错误分类：并发、配额、认证、协议、超时、未知。
- 取消语义：本地终止、原生确认、已终态 no-op。
- 会话标识：thread_id/session_id 提取与持久化。
- 能力声明：矩阵完整性、桌面端 unsupported。
- 重复调用：同 caller_ref 抑制、不同请求冲突、重启后 unknown 阻塞。
- 不支持操作：`ADAPTER_UNSUPPORTED`。
- CLI：list/probe/unsupported/unknown execution 退出码。

### 10.2 真实 Agent 测试（`CF_C03_REAL_AGENT_TESTS=1`）

```
python -m pytest tests/agent_runtime/test_real_agents.py -q
4 passed（claude 场景在模型偶发拒绝时自动重试后通过）
```

| 场景 | 结果 | 证据文件 |
| --- | --- | --- |
| Claude 新会话 + 文件结果 | finished，notes.txt=alpha | `claude-new-session.json` |
| Claude 会话恢复 + 上下文连续 | finished，同 session_id，alpha+beta | `claude-resume.json` |
| DSH 新会话 + 文件结果 | finished，hello.txt | `dsh-new-session.json` |
| DSH 原生取消 | cancelled，`remote_confirmed_stopped` | `dsh-cancel.json` |
| Codex CLI 新会话 | **blocked**：`quota_exceeded`（账号 usage limit） | `codex-new-session.json` |
| Codex-Relay 新会话 | finished，hello.txt | `relay-new-session.json` |
| 独立 CLI 入口（DSH） | finished，hello.txt | `cli-dsh-run.json` |

### 10.3 回归测试

```
python -m pytest tests -q --ignore=tests/agent_runtime
330 passed, 284 subtests passed in 274.67s
```

阶段 A/B 全部通过，未破坏既有契约与可靠性机制。

---

## 11. 真实执行证据

### 11.1 Claude Code CLI（Verified）

| 项 | 新会话 | 恢复会话 |
| --- | --- | --- |
| execution_id | `cfexec_9973520c415143dc898e91194e5dea90` | `cfexec_24de37795485417bbbad3fbb87d782b8` |
| native_session_id | `c7b2e88a-55e3-4873-9f60-f0454c8e3c54` | 同左（一致） |
| state / failure | finished / none | finished / none |
| 时间 | 17:13:35Z → 17:14:00Z | 17:14:00Z → 17:14:25Z |
| cost | $0.17014 | $0.23870 |
| 文件结果 | `notes.txt` = `alpha` | `notes.txt` = `alpha\nbeta` |

观察：Claude 对简短文件任务偶发回答 “I'm sorry, but I cannot assist…”。Adapter 正确报告 `finished` + 拒绝文本（执行事实），业务层必须自行判断；测试在检测到拒绝时用新 caller_ref 重试。

### 11.2 DSH（Verified）

| 项 | 新会话 | 取消 |
| --- | --- | --- |
| execution_id | `cfexec_32e44120c4bb446683cb3fbb08ded09f` | `cfexec_8949e9830ec04ec3b22018344775d1d5` |
| native_session_id | `session-74e5f50c-f475-420c-89a8-a325d1250aaa` | `session-18f9533b-01cf-4da6-951b-77fee74a1742` |
| state / failure | finished / none | cancelled / — |
| disposition | — | `remote_confirmed_stopped` |
| native_state | completed | `aborted`（reason `user`） |
| 时间 | 17:11:21Z → 17:11:39Z | 17:06:15Z → 17:06:26Z |
| usage | tokens 89972/245（uncached/output） | turn 1, steps 1 |

### 11.3 Codex CLI（Blocked by account quota）

| 项 | 值 |
| --- | --- |
| execution_id | `cfexec_5e0c72c171d54abbb4e698c36a77dfc1` |
| native_session_id | `01a1c671-e862-7fa1-bdaf-cf8f0b1a8791` |
| state / failure | failed / `quota_exceeded` |
| 时间 | 17:11:55Z → 17:12:12Z |
| 错误 | `You've hit your usage limit … try again at 2:26 AM` |

说明：Codex CLI 控制路径已在 C-02 用真实成功执行验证（`codex exec --json`，thread `01a1c639-…`，exit 0，179s，hello.txt 生成）。C-03 复测时账号配额耗尽，按 blocked 记录，未用 Mock 替代。

### 11.4 Codex-Relay CLI（Verified）

| 项 | 值 |
| --- | --- |
| execution_id | `cfexec_3b1835e6a47242d6a0feddc3a62ff784` |
| native_session_id | `01a1c672-2b47-7802-badf-65b751f208c5` |
| state / failure | finished / none |
| 时间 | 17:12:12Z → 17:12:56Z |
| usage | input 86576 / cached 72192 / output 749 / reasoning 240 |
| 文件结果 | `hello.txt` = `ContextFlow Adapter Test` |

### 11.5 独立 CLI 入口（Verified）

```
python -m contextflow_core.agent_runtime.cli --journal-root ... run --runtime dsh \
  --workdir ... --instruction "创建 hello.txt ..." --timeout 180 --out ...
```

结果：`finished`，execution `cfexec_460b4c66b925487c948d0ff2f99667f6`，session `session-c0fc2076-b404-43db-812f-c4fd6af14fc2`，15s，hello.txt 内容正确。

---

## 12. 与未来 TaskDelivery 的对接建议

### 12.1 身份映射

| Core 概念 | Adapter 概念 | 建议 |
| --- | --- | --- |
| `TaskId` | — | Adapter 不感知；Task 是逻辑工作 |
| `DeliveryId` | `ExecutionRequest.caller_ref` | Dispatcher 传 DeliveryId 作为幂等命名空间 |
| — | `ExecutionHandle.execution_id` | Core 持久化到 `task_executions` 表 |
| — | `native_session_id` | Core 持久化，供 resume/审计 |
| Delivery 状态 | Execution 状态 | 见 12.2 |

### 12.2 建议状态机

```
Delivery:  pending ──► sending ──► confirmed（Agent 已接受，带 execution_id）
                         │              │
                         ├──► failed（明确未接受）
                         └──► unknown（发送结果不可证）

Execution: pending ──► running ──► finished
                          │   ├──► failed
                          │   ├──► cancelled（仅原生取消）
                          └──► unknown（kill/超时/无终态）
```

- `Delivery.confirmed` 只表示“已接受执行”，不等于 Task 完成。
- Task 终态只由 final report 决定。
- `Execution.unknown` 时 Task 保持 `dispatched`，等待对账/人工决策。
- `confirmed` 的 evidence 建议包含：`execution_id`、`native_session_id`、`runtime`、`adapter_version`。

### 12.3 Dispatcher 调用顺序建议

1. 校验整批目标合法性与可用性（复用阶段 0 契约）。
2. 原子创建 Task + 初始 Delivery + intent fingerprint。
3. 发送前持久化 `Delivery.state=sending`。
4. 调 `adapter.start(request)`，持久化 `execution_id`、`native_session_id`。
5. 轮询/等待 `inspect`，更新 Execution 状态。
6. 将 `ExecutionResult` 转为 `AgentReportEnvelope`（`kind=final`，`outcome=completed/failed`，正文 `final_text`，诊断携带 execution/session/usage/failure_kind）。
7. `recover_inflight`：把 stale `sending/running` 转 `unknown`，不重发。
8. 按 runtime 限制并发（Relay=1；Codex/Claude 受账号配额约束）。

### 12.4 最小契约扩展建议（不在本阶段实施）

| 建议 | 说明 |
| --- | --- |
| `AgentDescriptor` 增加可选 `runtime_kind`、`workspace_root`、`permission_profile`、`native_session_ref` | 解决 Agent→Runtime→Session 绑定 |
| 新增 `AgentExecutionControl` Port（start/inspect/cancel） | 与现有 `AgentGateway.send_task` 并存 |
| `TaskDelivery` 增加可选 `execution_id` | 或先放 `evidence` |
| `TaskState` 增加 `CANCELLED` | 取消是真实终态 |
| `AgentReportEnvelope` 增加可选 `diagnostics` | 承载执行诊断 |
| 阶段 C 新增 `task_executions` 表 | 持久化执行记录 |

---

## 13. 已知限制与待决策事项

### 13.1 已知限制

1. Codex/Claude 前台进程无原生取消；kill 后只能判 `unknown`。
2. DSH `turn/end completed` 不代表业务成功；业务验收必须由报告层判断。
3. Codex CLI 受 ChatGPT 账号配额限制（本次实测 `quota_exceeded`）。
4. Codex-Relay 受第三方渠道单会话限制（`max_concurrency=1`）。
5. Codex 桌面端无外部控制接口，仅保留能力声明。
6. 跨进程接管正在运行的执行未实现；Adapter 重启后一律按 `unknown` 处理。
7. DSH 仅轮询，未使用 WebSocket 事件订阅。
8. Claude 偶发拒绝良性文件任务（模型行为，不是 Adapter 失败）。

### 13.2 待决策事项

| 优先级 | 问题 | 需要谁决定 |
| --- | --- | --- |
| P0 | Execution 持久化模型（`task_executions` 表/Port） | 架构/用户 |
| P0 | unknown 对账与重投策略 | Supervisor/用户 |
| P0 | Agent 逻辑身份 vs runtime kind vs session 映射 | 架构/用户 |
| P1 | 权限档位与工作区隔离策略 | 用户 |
| P1 | Codex 配额/Relay 并发错误处理策略 | 用户 |
| P1 | 是否支持 Codex 桌面端及方式 | Supervisor/用户 |
| P2 | DSH 自控制递归防护 | 架构 |

---

## 14. 交付清单与 Git 状态

| 交付物 | 路径 |
| --- | --- |
| 报告 | `contextflow-research/reports/C-03-agent-runtime-adapters.md` |
| 正式 Adapter 层 | `contextflow-research/contextflow_core/agent_runtime/` |
| 使用说明 | `contextflow-research/docs/CF-C03-agent-runtime-usage.md` |
| 测试 | `contextflow-research/tests/agent_runtime/`（58 单元/契约 + 4 真实，4 skipped 默认） |
| 真实证据 | `contextflow-research/_artifacts/c03-agent-runtime/evidence/` |
| 独立 CLI | `python -m contextflow_core.agent_runtime.cli` |

**Git 状态**：分支 `feat/cf-core-003-message-store`；本阶段只新增 `contextflow_core/agent_runtime/`、`tests/agent_runtime/`、`docs/CF-C03-agent-runtime-usage.md`、`reports/C-03-agent-runtime-adapters.md` 与 `_artifacts/c03-agent-runtime/`；**未修改**阶段 A/B 契约与实现、Extension、Bridge、Core Receiver、Message Store、Parser Worker、ParseResult 持久化、C-01 Supervisor 绑定功能；**未实现** Dispatcher；未提交 commit。

> 注：工作区中 `.claude/`、`contextflow-research/docs/superpowers/` 等未跟踪内容为并行 C-01/Claude Code 产生，本阶段未读取、未修改、未清理。
