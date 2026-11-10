# CF-CORE-004-B1 使用与维护说明

B1 提供 SQLite Schema v2、ParseJobRepository、原子领取、Lease/Fencing、持久调用意图预算、正式 ParseResult 提交及 Fake Parser Worker。它不调用真实 LLM，不创建 Task，也不发送 Agent。

## 1. 安全边界

- 普通 `SQLiteMessageStore` 或 Parser Worker **不会自动迁移 v1**。打开已知 v1 返回 `MESSAGE_STORE_MIGRATION_REQUIRED`。
- 迁移现有数据库前必须停止旧 Receiver/Worker，并使用 SQLite 一致性备份；WAL 数据库不能只复制主文件。
- 实现和自动化测试只迁移 tempfile 临时库；没有迁移 `_artifacts/cfcore003-manual.sqlite3` 或默认用户库。
- Fake 结果同样是不可变正式结果；只在隔离演示数据库使用，不能让阶段 C 消费 Fake 数据。

## 2. 显式 v1→v2 维护命令

以下命令会修改指定数据库，必须在已完成停服务和一致性备份后执行：

```powershell
cd D:\CortexFlow\contextflow-research
python -B -m contextflow_core.database_schema migrate `
  --database D:\path\to\messages.sqlite3
```

结果：

- 真正 v1：原子新增 `message_parse_jobs` 和索引，设置 user_version=2；不改写 messages 行及原有索引。
- 已是有效 v2：返回 `already_v2`，不重复迁移。
- 未知版本、损坏 Schema、非空 v0 或对象冲突：拒绝，不接管。

## 3. Fake Worker 隔离运行

必须显式提供数据库、Registry、Agent Directory 与响应文件，不会使用默认数据库。

Agent Directory 示例 `agents.json`：

```json
{
  "schema_version": 1,
  "agents": [
    {
      "agent_id": "codex-local",
      "name": "Codex",
      "aliases": ["OpenAI Codex"],
      "available": true
    },
    {
      "agent_id": "claude-code-local",
      "name": "Claude Code",
      "aliases": ["Claude"],
      "available": true
    }
  ]
}
```

Fake response 示例 `response.json`：

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

运行一次：

```powershell
python -B -m contextflow_core.parser_cli `
  --database D:\isolated\messages.sqlite3 `
  --registry D:\isolated\endpoints.json `
  --directory D:\isolated\agents.json `
  --provider fake `
  --fake-response D:\isolated\response.json `
  --once
```

## 4. 状态和恢复语义

ParseJob 内部状态：

- `ready`：可领取，尚无正式结果。
- `processing`：某 owner/request/generation 持有未过期 Lease。
- `retry_wait`：可重试故障，等待持久 next_attempt_at。
- `completed`：一个正式 parsed/unresolved/failed 已提交。

关键规则：

- 每个 generation 最多登记一次 Parser 调用意图。
- attempt_count 是已提交的调用意图数，不是远端计费次数。
- 过期 owner 的 retry/finalize 被拒绝，即使还没有新 Worker 接管。
- 预算耗尽后，新 owner 可以领取，但只能提交正式 failed，不能再调用 Parser。
- parsed 后 Message 保持 pending；unresolved/最终 failed 与 Message blocked 在同一事务提交。
- completed 不自动重新解析。

## 5. 查询正式结果

阶段 C 不应直接依赖表布局。B1 提供内部窄协议：

```python
stored = await parse_result_reader.get_final_result(message_id)
```

返回 `StoredParseResult | None`：包含来源 MessageId、完整阶段 0 ParseResult、completed_at_ms。不存在或未完成返回 None；损坏记录 fail closed。

只读 SQL 诊断示例：

```sql
SELECT message_id, job_state, claim_generation, attempt_count, max_attempts,
       result_status, intent_fingerprint, completed_at_ms
FROM message_parse_jobs
ORDER BY created_at_ms, message_id;
```

不要用 SQL 查询结果代替 Repository 的严格 codec/指纹校验。

## 6. 测试

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
python -B -m pytest tests/core/test_database_schema.py `
  tests/core/test_parse_result_codec.py `
  tests/core/test_parse_jobs.py `
  tests/core/test_parser_worker.py -q -p no:cacheprovider
```

并发测试使用两个独立 SQLite 连接；故障测试实际使用 trigger、延迟外键、锁、取消、Event、子进程/重开和 `ignore_check_constraints` 篡改，不仅依据测试名称。
