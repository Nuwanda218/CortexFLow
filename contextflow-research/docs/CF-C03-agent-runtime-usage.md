# CF-C03 Agent Runtime Adapter 使用说明

本文件说明 ContextFlow 正式 Agent Runtime Adapter 层的使用方式。该层是阶段 C 的前置能力，**不创建 Task/Delivery，不调用 Dispatcher**。

- 代码：`contextflow-research/contextflow_core/agent_runtime/`
- 测试：`contextflow-research/tests/agent_runtime/`
- 报告：`reports/C-03-agent-runtime-adapters.md`

## 1. 能力概览

| Runtime | 形态 | 新会话 | 复用/恢复 | 原生取消 | 结构化结果 | usage | 并发限制 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `codex-cli` | process | 支持 | 支持（thread id） | 不支持 | JSONL | tokens | 无 |
| `claude-cli` | process | 支持 | 支持（session id） | 不支持 | JSON | tokens + USD | 无 |
| `codex-relay` | process | 支持 | 支持 | 不支持 | JSONL | tokens | **1** |
| `dsh` | service | 支持 | 支持 | **支持** | history/turn-end | tokens | 无 |
| `codex-desktop` | gui | 不支持 | 不支持 | 不支持 | 不支持 | 不支持 | — |

`codex-desktop` 只提供能力声明与只读探测；`start/inspect/cancel` 一律返回 `ADAPTER_UNSUPPORTED`。

## 2. 统一接口

```python
from contextflow_core.agent_runtime import (
    ExecutionRequest, SessionPolicy, create_registry,
)

registry = create_registry()          # 默认 journal 在 ~/.contextflow/agent-runtime/journal
adapter = registry.get("claude-cli")  # 或 codex-cli / codex-relay / dsh

request = ExecutionRequest(
    runtime="claude-cli",
    instruction="在当前目录创建 hello.txt",
    workdir=r"D:\tmp\c03-demo",
    caller_ref="cfcall_demo_001",     # 调用方关联标识（未来由 Dispatcher 传 Delivery id）
    session_policy=SessionPolicy.NEW,
    timeout_s=180,
    permission_profile="workspace_write",
)

handle = await adapter.start(request)          # 立即返回，state=running
snapshot = await adapter.inspect(handle.execution_id)
result = await adapter.wait(handle.execution_id)   # 等待终态
```

`ExecutionResult` 关键字段：

- `snapshot.state`：`pending | running | finished | failed | cancelled | unknown`
- `failure_kind`：执行级失败分类（`runtime_error`、`timeout`、`concurrency_limit`、`quota_exceeded`、`authentication`、`protocol_error`、`unknown` 等），**不是业务验收结论**
- `final_text`：Agent 自己的最终文本
- `snapshot.native_session_id`：可持久化的原生会话标识
- `snapshot.result_complete`：是否拿到可靠终态证据
- `snapshot.side_effects_possible` / `retry_safe`：是否可能已产生副作用 / 是否可安全重试

## 3. 独立 CLI（仅用于验证 Adapter）

```powershell
cd D:\CortexFlow\contextflow-research

# 能力矩阵
python -m contextflow_core.agent_runtime.cli list

# 环境探测
python -m contextflow_core.agent_runtime.cli probe --runtime codex-cli

# 新会话执行并等待结果
python -m contextflow_core.agent_runtime.cli run --runtime claude-cli `
  --workdir D:\tmp\c03-claude --instruction "创建 hello.txt，内容为 ContextFlow Adapter Test" `
  --timeout 180 --out D:\tmp\c03-claude-result.json

# 复用原生会话
python -m contextflow_core.agent_runtime.cli resume --runtime dsh `
  --session session-xxxxxxxx --workdir D:\tmp\c03-dsh --instruction "继续"

# 查询 / 取消
python -m contextflow_core.agent_runtime.cli inspect --runtime codex-cli --execution-id cfexec_...
python -m contextflow_core.agent_runtime.cli cancel --runtime dsh --execution-id cfexec_...
```

退出码：`0` finished，`3` failed，`4` cancelled，`5` unknown，`1` 参数/适配器错误。

## 4. 配置

默认配置可用环境变量覆盖：

| 环境变量 | 含义 |
| --- | --- |
| `CF_C03_JOURNAL_ROOT` | 执行日志根目录（默认 `~/.contextflow/agent-runtime/journal`） |
| `CF_C03_CODEX_COMMAND` | Codex CLI 可执行文件 |
| `CF_C03_CLAUDE_COMMAND` | Claude Code 可执行文件 |
| `CF_C03_RELAY_COMMAND` | Codex-Relay 入口（默认 `D:\codex\codex-relay.cmd`） |
| `CF_C03_RELAY_CODEX_HOME` | Relay 独立 `CODEX_HOME`（默认 `~/.codex-cli-relay`） |
| `CF_C03_DSH_LAUNCHER_STATE` | DSH launcher state.json 路径 |
| `CF_C03_DSH_POLL_INTERVAL` | DSH 轮询间隔秒数 |
| `CF_C03_CONFIG` | 可选 JSON 配置文件路径 |

JSON 配置示例：

```json
{
  "journal_root": "D:/CortexFlow/contextflow-research/_artifacts/c03-agent-runtime/journal",
  "codex": {"command": ["codex"], "max_concurrency": null},
  "claude": {"command": ["claude"]},
  "relay": {"command": ["D:/codex/codex-relay.cmd"], "codex_home": "C:/Users/me/.codex-cli-relay", "max_concurrency": 1},
  "dsh": {"launcher_state": "C:/Users/me/AppData/Local/DSH-Launcher/state.json", "poll_interval": 2.0}
}
```

凭据不写入配置：Codex/Claude 使用各自既有 OAuth 存储，Relay 使用渠道密钥文件，DSH 使用 launcher capability URL（仅内存使用，不落日志）。

## 5. 幂等与并发边界

- `caller_ref` 是 Adapter 本地幂等命名空间。同一 `caller_ref` + 同一请求指纹 → 返回已有 execution（`duplicate_suppressed`），不会启动第二次。
- 同一 `caller_ref` + 不同请求 → `ADAPTER_IDEMPOTENCY_CONFLICT`。
- 进程重启后，journal 中非终态记录一律保守改写为 `unknown`；再次使用同一 `caller_ref` 会得到 `ADAPTER_IDEMPOTENCY_CONFLICT`，必须对账而不是重跑。
- **Adapter 不决定是否重新投递**；正式幂等仍由未来 Core Delivery Store 负责。
- `codex-relay` 的 `max_concurrency=1` 是硬约束，超出返回 `ADAPTER_CONCURRENCY_LIMIT`（retryable），由 Core 决定稍后重试。

## 6. 取消语义

| Runtime | 取消动作 | 结果 |
| --- | --- | --- |
| Codex / Claude / Relay | 终止本地进程树 | `disposition=local_process_terminated`，执行状态 `unknown`（无终态证据） |
| DSH | `session/cancel` 并等待 `turn/end aborted` | `disposition=remote_confirmed_stopped`，执行状态 `cancelled` |

取消**不会回滚**已产生的文件修改或其他副作用；`snapshot.side_effects_possible` 始终为 `true`。

## 7. 测试

```powershell
cd D:\CortexFlow\contextflow-research

# 单元 / 契约 / 生命周期测试（不调用真实 Agent）
python -m pytest tests\agent_runtime -q

# 真实 Agent 测试（消耗 token/额度，默认跳过）
$env:CF_C03_REAL_AGENT_TESTS="1"
$env:CF_C03_EVIDENCE_DIR="D:\CortexFlow\contextflow-research\_artifacts\c03-agent-runtime\evidence"
python -m pytest tests\agent_runtime\test_real_agents.py -q

# 阶段 A/B 回归
python -m pytest tests -q --ignore=tests\agent_runtime
```

## 8. 已知限制

- Codex/Claude 前台进程无原生取消；kill 后只能判 `unknown`。
- DSH 的 `turn/end completed` 不代表业务目标达成；业务验收必须由报告层判断。
- Codex CLI 受 ChatGPT 账号配额限制；Relay 受渠道单会话限制。
- Codex 桌面端无外部控制接口，仅保留能力声明。
- 跨进程接管正在运行的执行未实现；Adapter 重启后一律按 `unknown` 处理。
