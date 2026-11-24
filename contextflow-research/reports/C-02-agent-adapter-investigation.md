# C-02 Agent Adapter 能力调查与真实控制验证

- 任务编号：C-02
- 日期：2026-11-11（Asia/Shanghai）
- 工作树：`D:\CortexFlow`，分支 `feat/cf-core-003-message-store`（阶段 B 分支）
- 测试根目录：`contextflow-research/_artifacts/c02-agent-adapter/`
- 原型：`contextflow-research/_artifacts/c02-agent-adapter/prototype/`
- 结论性质：真实执行证据 + 最小原型；**未实现正式 Dispatcher，未修改阶段 A/B 正式实现与公共契约**。

> 脱敏说明：本报告不包含任何 Token、API Key、Cookie、Authorization 头或私人会话正文。CLI 回执中的凭据类字段一律省略；会话 ID 仅保留用于复现的标识符。

---

## 1. Executive Summary

### 1.1 五种运行形态是否可被 Core 稳定控制

| 运行形态 | 控制方式 | 结论 | 证据等级 |
| --- | --- | --- | --- |
| **Codex CLI** | 进程型：`codex exec --json` | **可以稳定控制**：非交互执行、JSONL 事件、thread id、resume、结构化失败、usage 全部实测通过 | Verified |
| **Claude Code CLI** | 进程型：`claude -p --output-format json` | **可以稳定控制**：session_id、resume、结构化结果、cost/token、permission_denials 全部实测通过 | Verified |
| **DSH 本身** | 服务型：本机 loopback HTTP RPC | **可以稳定控制**：外部进程可创建会话、发指令、轮询状态、读历史、原生取消、恢复会话 | Verified |
| **Codex-Relay CLI** | 进程型：官方 Codex CLI + 独立 `CODEX_HOME` | **可以控制，但受上游并发限制**：真实隔离任务成功；同一渠道同时只允许一个会话（实测 403） | Verified（控制面）/ 受限（并发） |
| **Codex 桌面端** | GUI 型：Electron + 内置 app-server（stdio） | **当前无法被外部程序稳定控制**：无对外控制 API、无监听控制套接字；仅共享 `~/.codex` 会话与队列 | Partial / Blocked |

### 1.2 推荐的 Adapter 架构

采用**能力声明 + 异步执行句柄**的最小 Adapter 抽象，而不是统一同步 `send_task`：

1. **三种 Adapter 形态**：`process`（Codex CLI / Claude CLI / Relay）、`service`（DSH）、`gui`（Codex 桌面端，research-only）。
2. **四层身份分离**：Task（逻辑工作）→ Delivery（一次投递）→ Execution（Agent 侧一次实际运行）→ Session（Agent 原生上下文）。
3. **Adapter 只负责**：启动/连接、能力声明、原生会话/进程管理、状态翻译、结果与诊断返回。
4. **Core 负责**：TaskId、DeliveryId、ExecutionId 的持久化、投递状态机、幂等、调度、故障恢复、是否重投的决策。
5. **同步/异步**：`start_task()` 立即返回 accepted + execution_id；执行进度通过 `poll/wait` 或事件流获取。`confirmed` 只表示“已被 Agent 接受”，不表示任务完成。

### 1.3 阶段 C 的关键技术风险

1. **执行结果未知（unknown）**：CLI 进程被终止/超时后没有终态事件，副作用可能已经发生；绝不能自动重试。
2. **无原生幂等键**：五种形态均无 CLI 级幂等键；重复投递保护必须由 Core 的 Delivery 状态机 + Adapter 本地日志共同承担。
3. **“CLI 成功 ≠ 任务成功”**：Codex 与 Claude 在“读取不存在文件”这类业务失败上都返回 exit 0 / `is_error=false`；业务成败必须由 Agent 报告层判定。
4. **账号/渠道配额与并发**：Codex CLI 实测触发 ChatGPT usage limit；Relay 渠道实测单会话 403；Adapter 必须把这类错误分类为不可盲目重试的 CLI 失败。
5. **配置/技能污染**：Codex CLI 会自动加载用户 MCP、skills、AGENTS.md（实测触发 tldraw MCP 认证错误、superpowers 技能创建 `.superpowers/` 目录并打开浏览器页面）；生产 Adapter 必须隔离 `CODEX_HOME` 或显式禁用。
6. **身份混用**：Codex CLI、Codex 桌面端、Codex-Relay 共享底层二进制/模型，但控制面、会话存储和并发语义不同，**不能合并为同一个可互换 Endpoint**。

---

## 2. Environment

| 项 | 值 |
| --- | --- |
| 操作系统 | Microsoft Windows 11 家庭版 中文版，Build 10.0.26200（x64） |
| 主机名 | NUWANDA |
| Node.js | v24.15.0 |
| Python | 3.10.11（`D:\python310\python.exe`） |
| Codex CLI | `codex-cli 0.162.1`（npm 安装，`D:\codex\codex.cmd`；实际可执行文件在 `D:\codex\node_modules\@openai\codex\...\codex.exe`） |
| Codex 桌面端 | `OpenAI.Codex 26.1103.7773.0`（Appx，DisplayName ChatGPT，`app/ChatGPT.exe` + 内置 `app/resources/codex.exe`） |
| Codex-Relay | 同一官方 Codex CLI 0.162.1，入口 `D:\codex\codex-relay.cmd`，`CODEX_HOME=%USERPROFILE%\.codex-cli-relay` |
| Claude Code | `2.1.322 (Claude Code)`（`D:\npm-global\claude.cmd`） |
| DSH | `0.2.1-alpha.1`，Web profile 运行于 `http://127.0.0.1:3080`，launcher state：`%LOCALAPPDATA%\DSH-Launcher\state.json` |
| 认证状态 | Codex CLI：ChatGPT OAuth（`Logged in using ChatGPT`）；Claude Code：`oauth_token` / firstParty；Relay：渠道 API Key（file store）；DSH：launcher capability URL 换 cookie |
| 隔离测试根 | `contextflow-research/_artifacts/c02-agent-adapter/`（各 Agent 独立子目录） |

测试期间未购买服务、未修改账户配置、未扩大权限；Relay 使用既有渠道密钥环境。

---

## 3. Capability Matrix（五 Agent × 统一能力）

状态定义：**Verified** 真实执行验证通过；**Partial** 部分验证；**Unverified** 有接口线索未验证；**Blocked** 明确阻塞；**Unsupported** 当前环境/接口不支持。

| 能力 / 运行形态 | Codex CLI | Claude Code CLI | DSH | Codex-Relay CLI | Codex 桌面端 |
| --- | --- | --- | --- | --- | --- |
| T1 安装/版本/认证 | Verified | Verified | Verified | Verified | Verified（Appx 26.1103.7773.0） |
| 非交互执行 | Verified | Verified | Verified | Verified | Unsupported |
| 新建会话 | Verified（thread id） | Verified（`--session-id`） | Verified（`session/create`） | Verified | Unsupported（仅 GUI） |
| 会话恢复 | Verified（`exec resume`） | Verified（`--resume`） | Verified（同 sessionId 再 prompt） | Verified（同 Codex） | Unverified |
| 会话绑定（向已有会话追加） | Partial（`queue` 入队；exec 内未观察到第二轮） | Verified（resume 续接） | Verified | Partial（同 Codex） | Partial（共享 queue，未验证桌面消费） |
| 流式事件 | Verified（`--json` JSONL） | Verified（`--output-format stream-json` 文档；本轮用 json） | Unsupported（本轮仅轮询） | Verified | Unsupported |
| 结构化最终结果 | Verified（JSONL + `-o`） | Verified（单 JSON result） | Verified（history/turn-end） | Verified | Unsupported |
| 结构化失败 | Verified（`turn.failed`） | Verified（`is_error`/`subtype`） | Partial（`turn/end` 无 failed 语义） | Verified | Unsupported |
| 原生取消 | Unsupported（前台 exec 无） | Unsupported（前台 `-p` 无；`--bg` 有 `claude stop`，未验证） | Verified（`session/cancel` → aborted/user） | Unsupported | Unsupported |
| 超时（Adapter 强制） | Verified（kill → unknown） | Verified（kill → unknown） | Verified（cancel → unknown） | Verified | Unsupported |
| 进程/应用恢复 | Verified（resume 后上下文连续） | Verified（resume 后上下文连续） | Verified（同会话继续） | Verified | Unverified |
| 重复投递保护 | Unsupported（无原生幂等键） | Unsupported | Unsupported（requestId 仅关联） | Unsupported | Unsupported |
| 权限/沙箱控制 | Verified（`-s`、`-c approval_policy`） | Verified（`--permission-mode`、`--allowedTools`、`permission_denials`） | Partial（受 Agent preset/工具配置约束） | Verified | Unsupported |
| 工作区隔离 | Verified（`-C`、sandbox） | Verified（cwd、`--add-dir`） | Verified（session cwd） | Verified | Unsupported |
| Usage/成本 | Verified（tokens） | Verified（tokens + USD） | Verified（tokens + sessionStats） | Verified（tokens） | Unsupported |
| 多轮任务 | Verified（resume） | Verified（resume） | Verified（同会话） | Verified | Unverified |
| 外部程序可控制 | Verified | Verified | Verified | Verified | **Blocked** |

---

## 4. Real Execution Evidence

统一测试维度：T1 可用性、T2 单次任务、T3 会话绑定、T4 结构化状态、T5 超时/取消、T6 异常恢复、T7 重复投递、T8 权限隔离、T9 成本性能。所有真实任务均在隔离目录执行。

### 4.1 DSH（服务型，Verified）

**运行机制**

- 实际入口：常驻 Web host（`D:\DSH\deepseek-harness\apps\cli` 以 `web --port 3080` 启动），不是 CLI。
- 程序化控制：本机 loopback HTTP JSON-RPC。launcher 把 capability URL 写入 `%LOCALAPPDATA%\DSH-Launcher\state.json`；外部程序用它换 cookie 后调用 `/api/<method>`。
- 已使用的方法：`session/list`、`session/create`、`session/prompt`、`session/projections`、`session/page`、`session/cancel`、`workspace/create`、`session/rename`。
- 控制脚本：`C:\Users\Nuwanda\.dsh\skills\dsh-terminal-control\scripts\dsh_control.py`（用户级既有技能，非 ContextFlow 代码）。C-02 从外部进程调用它完成全部测试。
- 三种能力区分：
  1. DSH 能调用其他 Agent：本轮未验证（不在 C-02 范围）。
  2. **外部程序能调用 DSH：Verified**（创建会话、发 prompt、读状态/历史）。
  3. **外部程序能恢复 DSH 既有会话：Verified**（同一 sessionId 继续 prompt，上下文连续）。

**测试记录**

| 测试 | 命令/调用 | 结果 | 证据 |
| --- | --- | --- | --- |
| T1 | `dsh_control.py health` | `authenticated=true`，`sessionCount=143` | 运行输出 |
| T2 | `create --path <隔离目录>` → `prompt "创建 hello.txt…"` → `wait` | `accepted:true`；`turn/end completed`；`hello.txt` 24 字节；1 turn / 4 steps；`llmMs=12941` | `dsh/t2-*.json`、`dsh/hello.txt` |
| T3 | 同一 session 第二轮 `创建 counter.txt=1`，第三轮 `读取并更新为 2` | counter 1 → 2；`turn/end completed`；sessionId 不变 `session-d7d4032e-8c57-488c-a5df-4031914dc8d3` | `dsh/t3-*.json`、`dsh/counter.txt` |
| T4 | `读取不存在的 missing-c02.txt` | Agent 报告“文件不存在”；`turn/end` 仍为 `completed` | `dsh/t4-history.json` |
| T5 | 长任务（输出 1..100000）→ 10s 后 `session/cancel` | `session/cancel accepted`；`turn/end reason.kind=aborted, reason.kind=user`；`running=false` | `dsh/t5-*.json` |
| T6 | 取消后同会话再 prompt | `completed`；Agent 正确复述上一轮内容 | `dsh/t6-history.json` |
| T9 | `state` 投影 | `tokenUsage`（uncached 123795 / output 667）、`sessionStats`（turns/steps/llmMs/toolMs/ttftMs） | `dsh/t2-wait.json` |

**关键限制**

- `session/prompt` 的 `accepted` 只代表入队，不代表完成；完成必须看 `session/projections.running` 与 `turn/end`。
- 取消**不会回滚已产生的副作用**：T5 取消前 Agent 已写出 `numbers-1-100000.txt`（688,895 字节），取消后文件仍在。这直接证明“取消/超时后必须按 unknown 处理副作用”。
- 本轮未使用 WebSocket 事件订阅，只有轮询；`steer`、问答审批、模型切换、断线恢复未验证。
- DSH 是“当前正在执行本任务的宿主”，外部控制 DSH 存在递归风险（Core 控制 DSH，DSH 又执行 Core 任务），阶段 C 需要显式防护。

### 4.2 Codex CLI（进程型，Verified）

**运行机制**

- 入口：`codex`（`D:\codex\codex.cmd` → 官方 `codex.exe` 0.162.1）。
- 非交互：`codex exec [PROMPT]`；`--json` 输出 JSONL；`-o` 写最终消息；`-C` 指定工作目录；`-s read-only|workspace-write|danger-full-access`；`-c approval_policy="never"`。
- 会话：`thread.started.thread_id` 即原生会话 ID；`codex exec resume <thread-id> <prompt>` 续接同一 thread。
- 程序化控制线索（未全部使用）：`codex app-server`（stdio/ws/unix JSON-RPC，experimental）、`codex queue --thread`、`codex remote-control`、`codex exec-server`、`codex agents`（需 TTY）。

**测试记录**

| 测试 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| T1 | `codex --version` / `codex login status` | `codex-cli 0.162.1`；`Logged in using ChatGPT` | — |
| T2 | `codex exec --json -C <隔离目录> -s workspace-write -c approval_policy="never" --skip-git-repo-check -o last.txt "创建 hello.txt…"` | **exit 0**，179.45s；`thread_id=01a1c639-394d-76f0-bfea-2095802b0f4f`；`turn.completed` usage `input=310434, cached=284544, output=747, reasoning=130`；`hello.txt` 24 字节 | `codex-cli/t2-exec.jsonl`、`hello.txt`、`last-message.txt` |
| T3 | 运行中执行 `codex queue --thread <t2-thread> --message C02_PROBE_QUEUE` | 回执 `Queued message 01a1c639-ab99-7cd3-bc52-a62aeca3d98c for thread …`；随后 `queue_1.sqlite.queued_items` 为空；该 exec 未观察到第二轮 turn | 运行输出、`inspect_codex_queue.py` |
| T4 | `读取不存在的 missing-c02.txt` | **exit 0**；`command_execution exit_code=1 status=failed`；Agent 文本报告失败；`turn.completed` | `codex-cli/t4-failure.jsonl` |
| T5 | 多步分析任务，31.57s 时终止进程树 | **exit -1**；JSONL 停在 `agent_message`，无 `turn.completed`/`turn.failed`；无终态 | `codex-cli/t5-cancel.jsonl` |
| T6 | `codex exec resume 01a1c63c-7a84-7f42-a66a-4a3eb0b07c91 --json "…"` | **exit 0**，36.81s；`turn.completed`；Agent 正确复述上一轮正在做的事 | `codex-cli/t6-resume.jsonl` |
| 配额 | 后续 E2E 再执行 | `turn.failed`：`You've hit your usage limit … try again at 2:26 AM`；exit 1 | `evidence/e2e-codex.json` |

**关键限制**

- `codex exec` **不接受 `-a`**；非交互必须用 `-c approval_policy="never"`，否则会在文件写入处等待人工确认（实测第一次运行卡住）。
- 无原生取消；终止进程后没有终态事件，只能判 unknown。
- 自动加载用户配置：MCP（tldraw 认证失败日志）、skills（superpowers 技能在仓库根创建 `.superpowers/` 并打开浏览器页面）、`AGENTS.md`。生产 Adapter 必须隔离 `CODEX_HOME` 或使用 `--ignore-user-config`/干净配置。
- `codex agents` 在非 TTY 下报 `ERROR: stdin is not a terminal`，不能作为脚本化会话发现接口。
- ChatGPT 账号配额会中断执行，且错误以 `turn.failed` 形式出现。

### 4.3 Claude Code CLI（进程型，Verified）

**运行机制**

- 入口：`claude`（2.1.322）。
- 非交互：`claude -p <prompt>`；`--output-format json|stream-json`；`--permission-mode`；`--allowedTools`；`--session-id <uuid>`；`--resume <uuid>`；`--max-budget-usd`；`--json-schema`；`--bg` 后台会话 + `claude agents/attach/logs/stop/rm`。
- 结构化结果（单 JSON）：`session_id`、`is_error`、`subtype`、`stop_reason`、`result`、`duration_ms`、`total_cost_usd`、`usage`、`permission_denials`、`num_turns`、`terminal_reason`。

**测试记录**

| 测试 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| T1 | `claude --version` / `claude auth status` | `2.1.322`；`loggedIn=true, authMethod=oauth_token` | — |
| T2 | `claude -p "创建 hello.txt…" --output-format json --permission-mode acceptEdits --allowedTools "Write,Read" --session-id <uuid>` | **exit 0**，38.25s；`session_id=29d29ca6-863a-458d-9406-546e0543c480`；`is_error=false`；`total_cost_usd=0.1893405`；`hello.txt` 24 字节；`permission_denials` 记录 Bash 被拒 | `claude-cli/t2-exec.json` |
| T3 | 新会话 turn1 `创建 counter.txt=1`；turn2 `--resume <sid>` 更新为 2 | turn1 28.56s → counter=1；turn2 20.43s → counter=2；session_id 不变 `d1b7346f-9f09-4d26-ba20-ef7f57021928` | `claude-cli/t3-*.json` |
| T4 | `读取不存在的 missing-c02.txt` | **exit 0**；`is_error=false`；`subtype=success`；`result="无法报告文件内容：missing-c02.txt 不存在…"` | `claude-cli/t4-business-failure.json` |
| T5 | 长任务（数到 100000 / Start-Sleep 120 / 多文件分析） | 前两次被模型拒绝（`I cannot assist`，11.6s/35s，正常完成）；第三次多步分析在 31.68s 终止进程树 → **exit -1**，JSON 输出 0 字节 | `claude-cli/t5c-cancel.json`（空）、`t5c-cancel.meta.txt` |
| T6 | 终止后 `--resume <sid>` | **exit 0**，7.99s；同 session_id；Agent 正确复述“上一轮正在扫描 Python 文件…” | `claude-cli/t6-resume.json` |

**关键限制**

- 前台 `-p` 无原生取消；终止后没有 result JSON，只能判 unknown。
- 业务失败与 CLI 成功不可由 exit code 区分（T4 证据）。
- 模型可能拒绝任务（`I cannot assist`），这属于业务层拒绝，不是 CLI 失败。
- `--bg` 后台会话提供 `claude stop/logs`，可能成为原生取消路径，本轮未验证。

### 4.4 Codex-Relay CLI（进程型，Verified / 并发受限）

**运行机制识别（重点）**

- `D:\codex\codex-relay.cmd` 只是**包装器**：设置 `CODEX_HOME=%USERPROFILE%\.codex-cli-relay`，清空继承的 `OPENAI_API_KEY/OPENAI_BASE_URL/CODEX_ACCESS_TOKEN/CODEX_API_KEY`，然后调用同一个官方 `codex.cmd`。
- 因此它**不是**独立程序、独立协议或远程控制中继；控制面与 Codex CLI 完全相同（同一 0.162.1 二进制）。
- 差异只在配置：第三方 Responses 渠道 `https://new.sharedchat.cc/codex`，模型 `gpt-5.6-sol`，`approval_policy=never`，`sandbox_mode=danger-full-access`，`model_reasoning_effort=high`；认证为渠道 API Key；会话/日志/队列独立在 `~/.codex-cli-relay/`。
- 该 `CODEX_HOME` 下还运行着独立 app-server daemon（0.162.1，`app-server --listen unix:// --managed-daemon`）。
- 它与桌面端/普通 CLI 的会话**不共享**（不同 `CODEX_HOME`），但二进制与协议共享。

**测试记录**

| 测试 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| T1 | `codex-relay login status` | `Logged in using an API key`（渠道密钥，已脱敏） | — |
| T2（两次失败） | `codex-relay exec --json … "创建 hello.txt…"` | **exit 1**；`turn.failed`：`unexpected status 403 Forbidden: Only one Codex conversation can run at a time` | `codex-relay/t2-exec.jsonl`、`t2b-exec.jsonl` |
| T2（成功） | 同上（渠道空闲时） | **exit 0**，62.1s；`thread_id=01a1c643-c221-7be3-a4db-39c76186d67f`；usage `input=80005, cached=69376, output=347, reasoning=49`；`hello.txt` 25 字节 | `evidence/e2e-relay.json`、`prototype-e2e/relay/hello.txt` |

**关键限制**

- 渠道级并发上限为 1：并发调用返回 403；Adapter 必须按渠道串行化（`max_concurrency=1`）。
- 认证、计费、可用性与官方 ChatGPT OAuth 完全独立；配额/限流错误形态也不同。
- 不应把 Relay 与 Codex CLI 视为同一 Endpoint；应作为同一 runtime 的**不同配置实例**。

### 4.5 Codex 桌面端（GUI 型，Partial / Blocked）

**运行机制识别**

- 形态：Electron 桌面应用（Appx `OpenAI.Codex 26.1103.7773.0`，DisplayName ChatGPT，`app/ChatGPT.exe`）。
- 后端：内置 `app/resources/codex.exe app-server --analytics-default-enabled …`（PID 11384），**通过 stdio 与 Electron 主进程通信**，没有 `--listen` 监听地址。
- 另有 `app/resources/codex.exe exec-server --remote https://codex-cloud-environments.chatgpt.com/api …` 用于云端环境（PID 25592）。
- 共享状态：与 CLI 共用 `%USERPROFILE%\.codex`（`sessions/`、`state_5.sqlite`、`queue_1.sqlite`、`session_index.jsonl`）。实测 CLI 创建的 rollout 与桌面端会话出现在同一 `sessions/` 树中。
- 协议注册：`codex://`（appx `windows.protocol`，`--single-argument %1`），仅用于启动/聚焦应用，不是命令通道。
- 无对外控制 API：默认 `CODEX_HOME` 的 app-server daemon 控制套接字不可用（`codex app-server daemon version` → 连接被拒绝）；桌面端 app-server 是 stdio 私有管道。

**探测记录**

| 探测 | 结果 |
| --- | --- |
| `codex app-server daemon version`（默认 HOME） | `failed to connect … app-server-control.sock`（连接被拒绝） |
| `codex agents` | `ERROR: stdin is not a terminal`（需要 TTY） |
| `codex queue --thread <CLI thread> --message …` | 成功入队（共享 `queue_1.sqlite`），回执消息 ID；证明队列是共享的 |
| `queue_1.sqlite` 只读检查 | 表 `queued_items`、`queued_thread_revisions`；探测消息随后被消费（表为空） |
| UI Automation 枚举 | 当前状态下 UIA 根未暴露 ChatGPT 窗口（应用可能最小化/隐藏）；`Get-Process` 可见 `ChatGPT` 主窗口标题 |
| 进程命令行 | 确认 app-server 为 stdio、exec-server 为云端环境 |

**结论**

- 外部程序**不能**可靠地创建/选择桌面会话、发送消息、读取生成状态或取消执行。
- 唯一可验证的共享通道是 `queue_1.sqlite`（`codex queue` 入队）；但“桌面端会消费并执行”本轮**未验证**（为避免打扰用户正在使用的桌面工作，未向真实桌面会话注入）。
- GUI 自动化（UIA/SendKeys）理论可行但脆弱、维护成本高，本轮未实现，标记 research-only。
- 与 Codex CLI 的共享边界：**会话存储与队列共享，控制面与执行进程不共享**。

### 4.6 横向关键语义对比（真实证据）

| 语义 | Codex CLI | Claude CLI | DSH | Relay |
| --- | --- | --- | --- | --- |
| “已接受”信号 | `thread.started` / `turn.started` | 进程启动 | `session/prompt accepted+requestId` | 同 Codex |
| 完成信号 | `turn.completed` + exit 0 | result JSON `subtype=success` + exit 0 | `turn/end reason=completed` | 同 Codex |
| 业务失败 | exit 0，Agent 文本报告；`command_execution exit_code=1` | exit 0，`is_error=false`，文本报告 | `turn/end completed`，文本报告 | 同 Codex |
| CLI/运行时失败 | `turn.failed` + exit≠0 | `is_error=true`/`subtype≠success`/exit≠0 | `agent/error` 事件（本轮未触发） | `turn.failed`（403） |
| 取消 | 无原生；kill → 无终态 | 无原生（前台）；kill → 无终态 | 原生 `aborted/user` | 同 Codex |
| 取消后副作用 | 可能已发生 | 可能已发生 | **已证实发生**（688KB 文件） | 可能已发生 |
| 恢复 | `exec resume` 上下文连续 | `--resume` 上下文连续 | 同 sessionId 连续 | 同 Codex |
| 幂等键 | 无 | 无 | 无（requestId 仅关联） | 无 |
| 配额/限流 | ChatGPT usage limit（`turn.failed`） | 未见（本轮） | 无（本地模型/网关） | 渠道 403 单会话 |

---

## 5. Adapter Prototype

### 5.1 文件结构

```
contextflow-research/_artifacts/c02-agent-adapter/
├── prototype/
│   ├── agent_adapter.py          # 统一状态、能力声明、请求/句柄/结果、进程基类、幂等日志
│   ├── registry.py               # runtime kind -> adapter 工厂 + 能力矩阵
│   ├── cli.py                    # 命令行验证工具（list/probe/run/queue）
│   ├── adapters/
│   │   ├── codex_cli.py          # Codex CLI 进程 Adapter
│   │   ├── claude_cli.py         # Claude Code CLI 进程 Adapter
│   │   ├── codex_relay.py        # Codex-Relay（隔离 CODEX_HOME，max_concurrency=1）
│   │   ├── dsh_rpc.py            # DSH 服务 Adapter（HTTP RPC）
│   │   └── codex_desktop.py      # 桌面端 research-only（仅 probe/queue）
│   └── tests/test_prototype.py   # 13 个单元测试（回放真实证据 + 进程生命周期）
├── codex-cli/ claude-cli/ dsh/ codex-relay/   # 各 Agent 真实测试证据
├── prototype-e2e/                # 原型真实执行目录
└── evidence/                     # 原型 E2E 结果 JSON
```

### 5.2 主要接口

```python
class ExecutionState(str, Enum):
    ACCEPTED, RUNNING, COMPLETED, FAILED, CANCELLED, UNKNOWN

class FailureClass(str, Enum):
    NONE, AGENT_BUSINESS, CLI, TIMEOUT, CANCELLED, UNKNOWN

@dataclass(frozen=True)
class AgentCapabilities:
    runtime: str
    control_kind: ControlKind            # process | service | gui | none
    supports_noninteractive: bool
    supports_session_resume: bool
    supports_session_binding: bool
    supports_streaming_events: bool
    supports_structured_result: bool
    supports_native_cancel: bool
    supports_structured_failure: bool
    supports_idempotency_key: bool
    supports_usage_metrics: bool
    supports_permission_control: bool
    supports_workspace_isolation: bool
    max_concurrency: int | None
    evidence_status: str                 # verified | partial | unverified | blocked
    notes: tuple[str, ...]

class AgentAdapter(Protocol):
    runtime: str
    capabilities: AgentCapabilities
    def probe(self) -> dict
    def start(self, request: ExecutionRequest) -> ExecutionHandle
    def poll(self, handle: ExecutionHandle) -> ExecutionHandle
    def wait(self, handle: ExecutionHandle, timeout_s: float | None = None) -> ExecutionResult
    def cancel(self, handle: ExecutionHandle) -> ExecutionResult
    def resume(self, session_ref: str, request: ExecutionRequest) -> ExecutionHandle
```

`ExecutionResult` 关键字段：`state`、`failure_class`、`final_text`、`native_session_id`、`exit_code`、`duration_s`、`diagnostics`、`side_effects_possible`、`retry_safe`、`error`。

### 5.3 使用方法

```powershell
cd contextflow-research\_artifacts\c02-agent-adapter
python -m prototype.cli list
python -m prototype.cli probe --adapter codex-cli
python -m prototype.cli run --adapter claude-cli --workdir prototype-e2e\claude `
    --task "创建 hello.txt，内容为 ContextFlow Adapter Test" --timeout 180 --out evidence\run.json
python -m prototype.cli run --adapter dsh --workdir prototype-e2e\dsh --task "..." --session session-...
python -m prototype.cli queue --thread <thread-id> --message "follow up"
python -m unittest discover -s prototype/tests -t .
```

### 5.4 已验证功能（真实执行）

| 功能 | 证据 |
| --- | --- |
| Codex CLI 启动/JSONL 解析/thread id/usage | `prototype-e2e` 与 `codex-cli/t2-exec.jsonl` 回放 |
| Claude CLI 启动/JSON 解析/session_id/cost/permission_denials | `evidence/e2e-claude.json`（completed，54.7s，session `859482f6-…`，cost `0.2857895`） |
| DSH 服务启动/轮询/历史/usage | `evidence/e2e-dsh.json`（completed，17.53s，session `session-d5c19787-…`） |
| Relay 隔离 CODEX_HOME 执行 | `evidence/e2e-relay.json`（completed，62.1s，thread `01a1c643-…`） |
| 超时/取消 → UNKNOWN + side_effects_possible | 单元测试 `ProcessLifecycleTests`（真实子进程） |
| 重复 key 抑制 | `evidence/idem-second.json`：同一 execution_id，`duplicate_suppressed=true`，未再次执行 |
| 未知 key 拒绝重执行 | 单元测试 `IdempotencyTests.test_unknown_key_blocks_reexecution` |
| 能力矩阵/桌面端不可互换 | 单元测试 `CapabilityTests` |
| 真实证据回放解析（成功/业务失败/运行时失败/被杀） | 13 个测试全部通过（4.6s） |

**未实现**：正式 Dispatcher、持久化 Execution 表、事件流订阅（DSH WebSocket / Codex app-server）、`claude --bg` 原生停止、GUI 自动化、跨进程执行恢复（Adapter 重启后接管正在运行的进程）。

---

## 6. Contract Compatibility

### 6.1 现有契约（阶段 0/B）复核

| 契约 | 现状 | C-02 结论 |
| --- | --- | --- |
| `Task` | `task_id, source_message_id, instruction_index, target_agent_id, content, state, created_at, updated_at, final_report_message_id`；`TaskState = pending_delivery/dispatched/completed/failed` | 可复用。`target_agent_id` 只表达逻辑 Agent，不表达 runtime/会话；`TaskState` 缺 `cancelled`。 |
| `TaskDelivery` | `delivery_id, subject_id(TaskId), target_id(AgentId), state(pending/sending/confirmed/failed/unknown), error, evidence` | 可复用。`evidence` 是自由 Mapping，足以承载 `execution_id/native_session_id/adapter diagnostics`，**无需改结构**。 |
| `DeliveryState` | pending/sending/confirmed/failed/unknown；`confirmed` 需非空 evidence；`unknown` 不可重发 | 与真实语义吻合。`confirmed` 应解释为“Agent 已接受执行”，不是完成。 |
| `AgentReportEnvelope` | `report_id, task_id, kind(progress/question/blocked/final), content, outcome(completed/failed)` | 可表达最终文本与成败，但**无法承载** execution_id、session id、exit code、duration、tokens、failure_class。 |
| `AgentGateway.send_task(agent_id, task_id, content) -> DeliveryOutcome` | 同步返回投递结果；`task_id` 是接收方幂等键 | 不足以表达“已接受但仍在运行”的异步执行；缺少 execution handle、状态查询、取消。 |
| `AgentDirectory` / `AgentDescriptor` | `agent_id, available` | 只表达逻辑身份与可用性；**没有 runtime kind、工作区、权限档、原生会话绑定**。 |
| `EndpointIdentity` | `endpoint_id, provider, native_session_id` | 面向 Supervisor（ChatGPT Web）Endpoint；Agent 侧缺少对应身份对象。 |
| DB Schema v2 | 仅 `messages`、`message_parse_jobs` | 阶段 C 尚无 Task/Delivery/Execution 表；本轮未修改。 |

### 6.2 可直接复用

- `Task`、`TaskDelivery`、`DeliveryState`、`DeliveryOutcome`、`ErrorInfo`、`CreateTasksResult` 的语义与真实投递结果高度一致。
- `TaskDelivery.evidence` 可直接存放 Adapter 证据（execution_id、native_session_id、runtime、usage、exit_code）。
- `AgentReportEnvelope` 可作为 Adapter 结果 → Core 报告的载体：`kind=final` + `outcome=completed/failed`，正文放 Agent 最终文本。
- `AgentDirectory` 的“未知返回 None、歧义报错、不创建 Agent”原则可继续沿用。

### 6.3 缺失与歧义

1. **Agent → Runtime 绑定缺失**：`AgentId` 无法区分 Codex CLI / 桌面端 / Relay；`AgentDescriptor` 没有 runtime kind、工作区、权限档、默认会话。
2. **Execution 身份缺失**：Task/Delivery 之外没有“一次实际运行”的持久身份；无法查询/取消/恢复一次运行。
3. **异步执行语义缺失**：`send_task` 同步返回，无法表达 accepted→running→completed 的长过程。
4. **取消/unknown 语义缺失**：`TaskState` 无 `cancelled`；`DeliveryState.unknown` 存在但没有“执行结果未知”的载体。
5. **报告诊断缺失**：`AgentReportEnvelope` 无法携带结构化执行诊断（exit code、session、usage、failure class）。
6. **原生会话绑定缺失**：没有 `AgentId ↔ native_session_id` 的持久映射；resume 只能靠 Adapter 临时保存。
7. **幂等边界不清**：`AgentGateway` 要求 `task_id` 幂等，但真实 CLI 无此能力；需要明确“Core 持久状态 + Adapter 本地日志”的分工。

### 6.4 最小兼容扩展建议（不在本轮实施）

| 建议 | 兼容性 | 说明 |
| --- | --- | --- |
| `AgentDescriptor` 增加可选字段：`runtime_kind: str \| None = None`、`workspace_root: str \| None = None`、`permission_profile: str \| None = None`、`native_session_ref: str \| None = None` | 向后兼容（默认 None） | 解决 Agent→Runtime→Session 绑定；Agent Directory 配置可保持 v1，新增可选字段。 |
| 新增 Port `AgentExecutionControl`：`start_task() -> ExecutionHandle`、`get_execution()`、`poll_execution()`、`cancel_execution()` | 新增接口，不改旧接口 | 与 `AgentGateway.send_task` 并存；`send_task` 保留为“投递”语义。 |
| `TaskDelivery` 增加可选 `execution_id: str \| None = None` | 向后兼容 | 便于查询；也可先只用 `evidence`。 |
| `TaskState` 增加 `CANCELLED`（可选 `UNKNOWN`） | 枚举扩展，旧数据不受影响 | 取消是真实终态；unknown 建议留在 Delivery/Execution 层。 |
| `AgentReportEnvelope` 增加可选 `diagnostics: Mapping[str, Any] = {}` | 向后兼容（`from_dict` 允许缺省） | 承载 execution_id/session/exit_code/usage/failure_class。 |
| 阶段 C 新增表 `task_executions` | 新表，不改旧表 | 字段建议：`execution_id, delivery_id, task_id, runtime_kind, native_session_id, state, started_at, ended_at, exit_code, diagnostics_json, side_effects_possible, retry_safe`。 |
| Agent Directory 配置 v2（可选） | v1 继续可读 | 增加 `runtimes` 段：runtime_kind、command、CODEX_HOME/env、workspace_root、permission_profile、max_concurrency。 |

**明确不建议**：为适配某个 CLI 修改阶段 0 公共契约的必填字段或状态组合；不建议把 Codex CLI/桌面端/Relay 合并为同一 Endpoint。

---

## 7. Risks

### 7.1 重复投递

- 五种形态均**无原生幂等键**。`codex queue` 是消息队列，不是幂等键；DSH `requestId` 只用于关联，不保证去重。
- 若 Core 在“已发送但未收到确认”时重发，可能造成重复执行与重复副作用。
- 缓解：Core 持久化 Delivery 状态（发送前写 `sending`）；Adapter 本地幂等日志；`unknown` 一律不自动重试。

### 7.2 执行状态未知

- 进程被 kill/超时后：Codex/Claude 无终态事件；DSH 取消有 `aborted` 但副作用已发生。
- 缓解：`unknown` + `side_effects_possible=true` + `retry_safe=false`；进入人工/对账流程（查询原生会话历史、检查工作区），不得自动重投。

### 7.3 进程崩溃

- 父进程（Core/Dispatcher）崩溃时，子 Agent 进程可能继续运行并产生副作用；重启后无法接管原进程。
- 缓解：阶段 C 首版采用“执行前持久化 + 重启后把 stale sending/execution 标 unknown”；不做跨进程接管。

### 7.4 会话丢失

- Codex/Claude 会话文件损坏、被清理、版本升级迁移（Codex 有 rollout migration）会导致 resume 失败。
- DSH 会话依赖本机数据库与 launcher；DSH 升级/重启后需复测。
- 缓解：resume 失败时报 `SESSION_RESUME_FAILED`，由 Core 决定新建会话还是失败，不静默替换。

### 7.5 权限控制

- Claude 的 `permission_denials` 证明工具级权限可限制；Codex 的 sandbox/approval 可限制，但非交互必须强制 `approval_policy=never`，否则挂起。
- DSH 的权限受 Agent preset/工具配置影响，Adapter 无法细粒度控制。
- 桌面端无程序化权限控制。
- 缓解：Adapter 暴露 permission_profile；Core/用户决定档位；高风险操作默认拒绝。

### 7.6 Agent 对工作区的副作用

- 实测副作用：Codex 自动加载 skills 在仓库根创建 `.superpowers/` 并打开浏览器页面（已清理）；DSH 取消后仍留下 688KB 文件；Claude 尝试 Bash 被拒但仍完成写文件。
- 缓解：每次执行使用隔离工作目录；生产 Adapter 隔离 `CODEX_HOME`；执行后由 Core 记录工作区变更证据（`workspace/changes` 事件）。

### 7.7 配额、并发与上游可用性

- Codex CLI：ChatGPT usage limit 会以 `turn.failed` 中断。
- Relay：渠道单会话并发限制（403），且为第三方服务，可用性/计费不受控。
- 缓解：错误分类 + 退避/人工处理；Relay `max_concurrency=1`；不把配额错误当可重试网络错误。

### 7.8 身份与安全

- Codex 桌面端、CLI、Relay 共享二进制/模型但控制面不同；若合并为同一 Agent，会出现“以为在控制桌面端，实际启动了 CLI 会话”。
- DSH 外部控制使用 launcher capability URL；该 URL 是敏感能力凭据，不得写入日志/报告。
- 缓解：显式 `runtime_kind`；敏感字段脱敏；DSH 控制仅限 loopback。

---

## 8. Recommendations for Stage C

### 8.1 推荐的最小 Adapter 接口

```python
# Core -> Adapter
start_task(request: ExecutionRequest) -> ExecutionHandle   # 立即返回 accepted + execution_id
get_execution(execution_id: str) -> ExecutionSnapshot      # 状态 + 诊断
wait_execution(execution_id: str, timeout_s: float) -> ExecutionResult
cancel_execution(execution_id: str) -> ExecutionResult     # 原生取消或 kill
resume_session(agent_id: str, native_session_id: str, request) -> ExecutionHandle
capabilities() -> AgentCapabilities
```

**同步 vs 异步**：Core 不应阻塞等待 Agent 完成。`start_task` 同步返回“已接受/未接受”，执行进度异步获取；Dispatcher 用轮询或事件回调推进。

**执行标识**：`execution_id` 由 Core 生成并持久化（Adapter 只回传），与 `delivery_id` 一对一（首版），允许未来重试产生多个 execution。

**状态映射**（Adapter 必须翻译为统一语义）：

| 统一状态 | Codex CLI | Claude CLI | DSH |
| --- | --- | --- | --- |
| accepted | `thread.started`/`turn.started` | 进程启动 | `session/prompt accepted` |
| running | 事件持续 | 进程存活 | `running=true` |
| completed | `turn.completed` + exit 0 | `subtype=success` + exit 0 | `turn/end completed` |
| failed | `turn.failed` / exit≠0 | `is_error`/`subtype≠success`/exit≠0 | `agent/error` / `turn/end` 非 completed |
| cancelled | 无（kill → unknown） | 无（kill → unknown） | `turn/end aborted/user` |
| unknown | kill/超时/无终态 | kill/超时/无终态 | 超时取消/连接中断 |

**业务失败**：Adapter 返回 `COMPLETED` + `final_text`，业务成败由 Agent 报告层（Core/LLM 或规则）判定；Adapter 可提供 `diagnostics.business_success="unknown"`。

**进程退出但结果未知**：一律 `UNKNOWN` + `side_effects_possible=true` + `retry_safe=false`；Core 不得自动重投。

**防止重复副作用**：Core 的 Delivery 状态机是权威；Adapter 本地幂等日志是第二道防线；`unknown` 必须对账，不得重发。

**映射到 AgentReportEnvelope**：`kind=final`，`outcome=completed/failed`，`content.text=final_text`，`diagnostics`（建议扩展）携带 execution_id/session/exit_code/usage/failure_class。

**Dispatcher 选择 Adapter**：按 Agent Directory 中的 `runtime_kind` 选择工厂；同一逻辑 Agent 可有多个 runtime 绑定，但必须显式配置，不能自动互换。

### 8.2 Dispatcher 应承担的职责

1. 校验整批目标合法性与可用性（复用阶段 0 契约）。
2. 原子创建 Task + 初始 Delivery + intent fingerprint。
3. 发送前持久化 `Delivery.state=sending`。
4. 调用 Adapter `start_task`，持久化 `execution_id`、`native_session_id`。
5. 轮询/等待执行，更新 Execution 状态；`confirmed` 只表示已接受。
6. 将 ExecutionResult 转换为 AgentReportEnvelope 并交给报告层。
7. `recover_inflight`：把本模块 stale `sending`/`running` 转为 `unknown`，不重发。
8. 按 runtime 限制并发（Relay=1；Codex/Claude 可并行但受账号配额约束）。
9. 不代替 Agent 决定业务成败；不做 LLM 总结。

### 8.3 TaskDelivery 建议状态机

```
Delivery:  pending ──► sending ──► confirmed（Agent 已接受，带 execution_id）
                         │              │
                         ├──► failed（明确未接受）
                         └──► unknown（发送结果不可证）

Execution: accepted ──► running ──► completed
                          │   ├──► failed
                          │   ├──► cancelled（仅原生取消）
                          └──► unknown（kill/超时/无终态）
```

- `Delivery.confirmed` ≠ `Task.completed`；Task 终态只由 final report 决定。
- `Execution.unknown` 时 Task 保持 `dispatched`，等待对账/人工决策。
- 取消：仅当 Adapter 声明 `supports_native_cancel` 且 Agent 确认时记 `cancelled`；进程 kill 记 `unknown`。
- 超时：Adapter 强制终止 → `unknown`（不是 `failed`）。

### 8.4 阶段 C 首版支持范围

**首批正式接入（Verified）**

1. **Codex CLI**（进程型）——控制面最完整，JSONL/thread/resume/usage 均验证。
2. **Claude Code CLI**（进程型）——session_id/resume/cost/permission 均验证。
3. **DSH**（服务型）——唯一具备原生取消的形态；适合作为“可取消/可恢复”能力基线。

**作为配置变体接入**

4. **Codex-Relay CLI**——复用 Codex CLI Adapter，独立 `CODEX_HOME`、独立认证、`max_concurrency=1`；不并入 Codex CLI Endpoint。

**暂不进入首版**

5. **Codex 桌面端**——无外部控制 API；首版不接入。后续可选路径：
   - 等待官方 app-server/remote-control 对外接口；
   - 仅做 `queue` 追加（需先验证桌面端消费与结果读取）；
   - GUI 自动化作为最后手段，单独评估脆弱性与维护成本。

### 8.5 必须优先解决的阻塞问题

| 优先级 | 问题 | 需要谁决定 |
| --- | --- | --- |
| P0 | Execution 身份与持久化模型（新表/新 Port） | 架构/用户 |
| P0 | unknown 对账与重投策略（何时人工介入） | Supervisor/用户 |
| P0 | Agent 逻辑身份 vs runtime kind vs session 的映射规则 | 架构/用户 |
| P1 | 权限档位与工作区隔离策略（谁能批准写操作） | 用户 |
| P1 | Codex 账号配额/Relay 并发错误的处理策略 | 用户 |
| P1 | 是否支持 Codex 桌面端，以及以何种方式 | Supervisor/用户 |
| P2 | DSH 自控制递归防护 | 架构 |

---

## 9. 交付清单

| 交付物 | 路径 |
| --- | --- |
| 本报告 | `contextflow-research/reports/C-02-agent-adapter-investigation.md` |
| 原型代码 | `contextflow-research/_artifacts/c02-agent-adapter/prototype/` |
| 原型单元测试 | `prototype/tests/test_prototype.py`（13 passed） |
| 真实测试证据 | `_artifacts/c02-agent-adapter/{codex-cli,claude-cli,dsh,codex-relay}/` |
| 原型 E2E 结果 | `_artifacts/c02-agent-adapter/evidence/e2e-*.json`、`idem-*.json` |
| 隔离执行目录 | `_artifacts/c02-agent-adapter/prototype-e2e/` |

**Git 状态**：分支 `feat/cf-core-003-message-store`；本轮只新增 `contextflow-research/_artifacts/c02-agent-adapter/` 与本报告，**未修改** Extension、Bridge、Core Receiver、Message Store、Parser Worker、ParseResult 持久化、阶段 A/B 状态机与数据库契约，**未实现**正式 Dispatcher，未提交 commit。测试产生的 `.superpowers/` 副作用目录已清理。

---

## 10. 附：测试命令摘录（脱敏）

```powershell
# DSH
python dsh_control.py create --path <isolated>
python dsh_control.py prompt --session <sid> --text "创建 hello.txt…"
python dsh_control.py wait --session <sid> --timeout 55
python dsh_control.py history --session <sid> --max-messages 10
python dsh_control.py cancel --session <sid>

# Codex CLI
codex exec --json -C <isolated> -s workspace-write -c approval_policy="never" `
  --skip-git-repo-check -o last.txt "创建 hello.txt…"
codex exec resume <thread-id> --json -c approval_policy="never" "…"
codex queue --thread <thread-id> --message "…"

# Claude Code
claude -p "创建 hello.txt…" --output-format json --permission-mode acceptEdits `
  --allowedTools "Write,Read" --session-id <uuid>
claude -p "…" --resume <uuid> --output-format json --permission-mode acceptEdits

# Codex-Relay
D:\codex\codex-relay.cmd exec --json -C <isolated> -s workspace-write `
  -c approval_policy="never" --skip-git-repo-check "创建 hello.txt…"

# 原型
python -m prototype.cli run --adapter claude-cli --workdir prototype-e2e\claude `
  --task "创建 hello.txt…" --timeout 180 --out evidence\e2e-claude.json
python -m unittest discover -s prototype/tests -t .
```

**证据边界**：以上结论仅代表 2026-11-11 本机环境与所列版本；CLI/桌面端升级后需轻量复测。未验证项已在能力矩阵中标注 Partial/Unverified/Unsupported，未以 Mock 冒充真实执行。
