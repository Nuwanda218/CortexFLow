# CF-CORE-004-B2 使用说明

B2 在 B1 的同一 Worker/Repository 状态机上增加配置驱动 Agent Directory、Anthropic Messages 与 OpenAI-compatible Provider Adapter，以及严格 LLM InstructionParser。它不创建 Task、不投递 Agent、不实现 Project/Role。

## 1. 安装依赖

项目 Python 基线为 3.10：

```powershell
cd D:\CortexFlow\contextflow-research
python -m pip install -r requirements-core-b2.txt
```

已测试组合：

- anthropic 2.6.0（官方 Python SDK）
- httpx2/httpcore2 2.14.0（最新的 2.x 中明确支持 Python 3.10 的版本）
- truststore 0.10.5
- docstring-parser 0.18.0
- opentelemetry-api 1.45.2
- openai 2.53.0（OpenAI-compatible Chat Completions）

`httpx2` 2.15+ 要求 Python 3.11，所以本项目不能让解析器自动选最新 transport。

## 2. Agent Directory

格式见 `CF-CORE-004-B1-usage.md`。规则：

- agent_id 稳定且唯一。
- name/aliases 使用 NFKC+casefold 建索引，任何规范化冲突都拒绝。
- 名称、别名和 ID 不允许外层空白或控制字符。
- 已知离线 Agent 仍可解析为稳定身份；B2 不从浏览器、Endpoint、Session 推断 Agent。
- Parser 的正式结果写 canonical name，不写 AgentId；阶段 C 再解析为 AgentId。

## 3. Provider 配置

### 3.1 Anthropic Messages

在当前 PowerShell 进程中设置；不要写进仓库文件：

```powershell
$env:ANTHROPIC_AUTH_TOKEN = '<provider-token>'
$env:ANTHROPIC_BASE_URL = 'https://<authorized-anthropic-router-host>'
$env:CONTEXTFLOW_LLM_MODEL = '<available-model-id>'
```

运行参数：`--provider anthropic`。

### 3.2 天大信网 OpenAI-compatible DeepSeek

文档确认 Base URL 为 `https://ai.tju.edu.cn/api/v3`，协议为 `/chat/completions`；不要使用错误的 `/v1`，也不使用有已知 custom-tool 缺陷的 `/responses`。

```powershell
$env:CONTEXTFLOW_OPENAI_API_KEY = '<provider-token>'
$env:CONTEXTFLOW_OPENAI_BASE_URL = 'https://ai.tju.edu.cn/api/v3'
$env:CONTEXTFLOW_LLM_MODEL = 'deepseek-v4.1-flash'
```

运行参数：`--provider openai-compatible`。

`tju-llm` 的 `/models` 只返回默认别名，不能据此判断 DeepSeek 不存在；`deepseek-v4.1-flash` 已通过实际 Chat Completions 调用验证。网关限制为 60 次/分钟、3000 次/小时；Adapter读取合法 `Retry-After`，Worker将其持久化并至少等待该时长，不会被本地指数退避上限截短。

### 3.3 共同行为

安全行为：

- `max_retries=0`，只由 B1 Worker 计算持久重试预算。
- Provider 请求有明确 timeout，且不持有 SQLite 写事务。
- 只记录 request_id/model/stop_reason/token counts，不记录密钥、Supervisor 正文或模型原始 JSON。
- 408/409/429/5xx、网络和 timeout 可重试；400/401/403 为终态配置/请求错误。
- 凭据只从环境加载；代码、示例、测试和报告不含实际值。

## 4. 运行真实 Parser Worker

数据库必须已是 v2。命令的所有本地路径均为显式参数：

```powershell
python -B -m contextflow_core.parser_cli `
  --database D:\path\to\messages-v2.sqlite3 `
  --registry D:\path\to\endpoints.json `
  --directory D:\path\to\agents.json `
  --provider openai-compatible `
  --once
```

持续运行时去掉 `--once`。默认参数：30 秒 Lease、10 秒 Parser 截止、5 秒提交余量、3 次总调用意图预算。真实 Provider 的 timeout/模型行为确认后再调整；没有 heartbeat。

## 5. Prompt 与验证

系统规则与 Supervisor 正文分离：Anthropic Adapter 使用独立 content blocks；OpenAI-compatible Adapter 使用 system message 加一个只含候选与 JSON 编码正文的 user payload。模型只能返回：

```json
{
  "status": "parsed",
  "instructions": [
    {
      "target_agent": "Codex",
      "content": "修复 Adapter",
      "source_excerpt": "Codex 修复 Adapter"
    }
  ],
  "reason": null
}
```

或 unresolved + 空 instructions + 非空 reason。

本地验证：

- exact fields、严格 JSON、无重复键/非有限数。
- target 必须唯一解析到可信 Directory，写 canonical name。
- source_excerpt 必须是原文的有序精确子串。
- 原文明确出现的可信 Agent 集合必须和模型输出目标集合相等。
- 任一未知目标不保留其他部分指令，整批 unresolved。
- parsed 使用阶段 0 ParseResult 重新构造并计算原算法 fingerprint。

这能检测明确多目标遗漏和新增目标，不能绝对证明 LLM 没遗漏同一 Agent 的另一条自然语言指令。

## 6. Fake 与真实验收的区别

- Fake Provider 全链路：不联网、确定性，验证 Worker/Parser/SQLite 组合。
- 真实 Provider：验证网关认证、模型、Prompt 和响应兼容；可能产生费用。
- 真实 ChatGPT Extension→Core：还需实际浏览器/Bridge/Receiver 发送链路；不能由前两者推断。

本次交付的 Fake 证据：`_artifacts/cfcore004-b2-e733d663/evidence.json`。

本次真实 Provider 验证分开记录：

- Anthropic Router：`https://ps.air-outer.com` 对 Models 与 Messages 均返回 401 `unauthorized_client_error`，没有把该凭据发送到另一个未获授权 Host。
- 天大信网 DeepSeek：`GET /api/v3/models` 返回 200；使用 `deepseek-v4.1-flash` 的真实两目标解析成功。证据为 `_artifacts/cfcore004-deepseek-f7c6f452/evidence.json`：首次 finalized、第二次 idle、attempt_count=1、目标顺序 Codex→Claude Code、Message仍pending。

两个凭据都曾在对话明文出现，建议在验收后轮换。

## 7. 查询和测试

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
python -B -m pytest tests/core/test_agent_directory.py `
  tests/core/test_anthropic_provider.py `
  tests/core/test_openai_provider.py `
  tests/core/test_instruction_parser.py `
  tests/core/test_parser_integration.py `
  tests/core/test_parser_cli.py -q -p no:cacheprovider
```

全回归：

```powershell
python -B -m pytest tests -q -p no:cacheprovider
```

真实 API 不是默认测试，不在普通 pytest 中产生外部请求或费用。
