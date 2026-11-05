# CF-DEV-003 开发报告：wait_for_reply（基于实时快照的回复等待与关联）

- 日期：2026-11-05
- 扩展版本：v0.3.0（新增轻量 `get_generation` 接口 + 消息 `message_id` 字段）
- 自动化证据：`_artifacts/cfdev003-evidence.json`（**11/11 通过**）
- 真实页面验证：待用户执行（见第 6 节）

## 1. 目标与范围

在**不发送任何消息、不修改 DOM** 的前提下，让外部 Python 程序能够：

> 等待指定会话的下一条新回复，回复完成后返回其结构化内容。

按 Coordinator 评审要求实现，核心是**基线消息 ID 机制**，而不是"页面空闲即返回"。

## 2. 架构与数据流

```
Python wait_for_reply
   │ 1) get_snapshot  → 记录 baseline message_ids
   │ 2) 轮询 get_generation（轻量：只读状态属性，不解析正文）
   │ 3) state==completed 时 get_snapshot → 找"不在 baseline 里的 assistant 消息"
   ▼
bridge_server.py → background.js（严格 session 路由）→ content.js（实时读 DOM）
```

**分层原则**（与 Coordinator 一致）：扩展只描述网页状态，`wait_for_reply` 的业务语义在 Python 侧实现，Content Script 不承担任务关联逻辑。

## 3. 修改的文件

| 文件 | 变更 |
|---|---|
| `contextflow-extension/content.js` | 新增 `cf:read_generation`（轻量状态读取，读前后 session 校验）；消息对象新增 `message_id` 字段；版本 0.3.0 |
| `contextflow-extension/background.js` | 新增 `get_generation` 请求类型；抽出共享 `resolveTargetTab` 严格路由；版本 0.3.0 |
| `contextflow-extension/bridge_server.py` | 白名单新增 `get_generation` |
| `experiments/playwright-probe/cfdev003_wait.py` | **新增**：`BridgeClient`（持久连接、请求 ID 匹配）+ `wait_for_reply` + CLI |
| `experiments/playwright-probe/cfdev003_test.py` | **新增**：11 项自动化测试（mock 页） |
| `_artifacts/cfdev003-mock/mock.html` | **新增**：可控时序的 ChatGPT-like mock（`window.__mock`） |

## 4. 接口与算法

### 4.1 新增扩展接口 `get_generation`

```json
// 请求
{"id": "req-1", "type": "get_generation", "session_id": "<uuid>"}
// 响应
{"id": "req-1", "type": "result", "success": true,
 "data": {"tab_id": 101, "session_id": "<uuid>",
          "generation": {"state": "generating", "evidence": {"turn_states": ["complete","in_progress"], "stop_button": true}},
          "message_count": 10, "last_message_id": "<uuid>",
          "last_role": "assistant", "last_status": "in_progress",
          "at": "..."}}
```

只读 `data-talvt-turn-state` 与停止按钮，**不解析消息正文**，适合高频轮询。

### 4.2 `wait_for_reply` 算法

```
baseline = get_snapshot(session)          # 记录 baseline_ids 与 target_tab_id
loop (poll=0.8s, timeout=120s):
    gen = get_generation(session)
    if gen.session_id != session:          → SESSION_CHANGED
    if gen.state == "completed":
        snap = get_snapshot(session)
        new = [assistant msgs whose ids ∉ baseline_ids]
        if new and any(status=="complete"):
            return last complete new message
    sleep(poll)
→ TIMEOUT + last_state
```

**关键点**：
- 页面基线时已 `completed` 时**不会**返回旧回复（T1 验证）；
- 读取前后 session 校验；路由失败时区分"标签页已切换"（SESSION_CHANGED）与"标签页已关闭"（SESSION_NOT_FOUND）；
- 超时返回 `TIMEOUT` + 最后观察到的状态，不返回任何消息。

### 4.3 CLI

```powershell
python cfdev003_wait.py --list
python cfdev003_wait.py --session <uuid> [--timeout 120] [--poll 0.8] [--json]
```

## 5. 自动化测试结果（mock 页，11/11）

| # | 场景 | 结果 |
|---|---|---|
| T1 | 基线已 completed，无新消息 | PASS：2s 内不返回；新回复到达后返回**新**回复（不含 OLD） |
| T2 | 正常等待 | PASS：返回新回复正文，ID 不在基线中 |
| T3 | 无回复超时 | PASS：`TIMEOUT` + `last_state` |
| T4 | 等待中切换会话 | PASS：`SESSION_CHANGED` |
| T5 | 回复长期 in_progress | PASS：不误判完成；完成后返回 |
| T6 | 回复被重新生成 | PASS：返回**新** message_id（`e313…` → `0ff5…`） |
| T7 | 多条新回复 | PASS：返回最后一条 + `multiple: true` |
| T8 | 标签页关闭 | PASS：`SESSION_NOT_FOUND` |

调试中修复的问题：
1. `get_snapshot` 返回 `{tab_id, captured_at, snapshot:{...}}`，初版直接读 `data["messages"]` 导致永远为空 → 加 `_snap_parts` 解包；
2. 消息对象缺少单数 `message_id` 字段 → content.js 补充，wait 侧兼容；
3. 会话切换时路由先报 `SESSION_NOT_FOUND` → 按 `target_tab_id` 复查 list_sessions，区分 SESSION_CHANGED / SESSION_NOT_FOUND。

## 6. 真实 ChatGPT 页面验证（待用户执行）

1. 重新加载扩展（v0.3.0）并**刷新 ChatGPT 标签页**；
2. 保持 bridge 与 `bridge_client.html` 打开；
3. 运行：
   ```powershell
   cd D:\CortexFlow\contextflow-research\experiments\playwright-probe
   python cfdev003_wait.py --session <会话ID> --timeout 120
   ```
4. 看到 `[wait] baseline …` 后，在 ChatGPT 页面发送一条消息；
5. 预期：CLI 打印 `[wait] OK in …ms` 与完整回复内容。

## 7. 已知限制

- bridge 页面必须常开（v1 限制）；
- 轮询间隔默认 0.8s，长回复可能多等一拍；
- 只读取当前已渲染消息（懒加载历史不可见）；
- 多条新回复时返回最后一条（`multiple` 标记提示歧义）；
- 重新生成场景已验证 ID 会变化，但未验证"同一 ID 内容被替换"的极端情况。

## 8. 下一步（CF-DEV-004 前置）

1. 真实页验证本报告第 6 节；
2. 发送链路前，先在真实页验证"扩展隔离世界 → 合成 paste → 编辑器"并加**回读校验**；
3. 按 Coordinator 建议拆分 `prepare_message` 与 `send_message`，发送非幂等、禁止自动重试。
