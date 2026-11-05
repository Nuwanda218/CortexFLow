# CF-DEV-004 开发报告（实验 A）：prepare_message 输入框适配

- 日期：2026-11-05
- 扩展版本：v0.4.0
- 范围：**只填入，不发送**（Coordinator 指定的实验 A）
- 自动化证据：`_artifacts/cfdev004-evidence.json`（**12/12 通过**）
- 真实页面验证：见第 6 节

## 1. 目标

让外部程序把文本**安全地**填入指定会话的输入框，并确认内容真的进去了；**绝不触发发送**。

按 Coordinator 要求拆成三个实验，本报告只覆盖实验 A：

| 实验 | 内容 | 状态 |
|---|---|---|
| A | 输入框适配（prepare_message，只填入） | ✅ 本报告 |
| B | 人工确认发送 + wait_for_reply 观察 | 待做 |
| C | 程序化发送 | 待做（非幂等，需独立设计） |

## 2. 接口

```json
// 读取输入框状态
{"id":"r1","type":"get_composer","session_id":"<uuid>"}
→ {"success":true,"data":{"session_id":"...","composer":{
     "found":true,"editable":true,"text":"","text_length":0,"empty":true,
     "selector":"#prompt-textarea","tag":"div"}}}

// 填入（不发送）
{"id":"r2","type":"prepare_message","session_id":"<uuid>","text":"...","force":false}
→ {"success":true,"data":{
     "session_id":"...","prepared":true,"sent":false,"verified":true,
     "method":"paste","draft_replaced":false,
     "composer_text_length":17,"composer_empty":false,"selector":"#prompt-textarea"}}

// 清空（测试/清理工具，不供 Agent 常规使用）
{"id":"r3","type":"clear_composer","session_id":"<uuid>"}
→ {"success":true,"data":{"cleared":true,"composer":{...}}}
```

**语义保证**：
- `prepared:true` 只表示文本已进入输入框，**不代表已发送**；
- `sent` 恒为 `false`（本版本不存在发送路径）；
- `verified:true` 表示回读内容与目标文本一致（按非空行序列比对）。

## 3. 错误码

| 错误码 | 含义 |
|---|---|
| `COMPOSER_UNAVAILABLE` | 找不到输入框或不可编辑 |
| `DRAFT_PRESENT` | 输入框已有用户草稿，未传 `force:true` 时拒绝覆盖 |
| `COMPOSER_MISMATCH` | 填入后回读不一致（含实际内容摘要，便于诊断） |
| `SESSION_CHANGED` | 填入前后会话变化 |
| `SESSION_NOT_FOUND` / `AMBIGUOUS_SESSION` | 路由失败（复用 CF-DEV-002 严格路由） |
| `INVALID_REQUEST` | 缺 text / text 为空 / 缺 session_id |

## 4. 实现要点

### 4.1 输入框定位
`#prompt-textarea` → `.ProseMirror[contenteditable]` → `form [contenteditable]` → `[contenteditable][role=textbox]`，且要求可见（`getClientRects().length > 0`）。

### 4.2 填入策略（带回读校验）
1. **合成 paste 事件**（ProseMirror 原生路径，优先）；
2. 回读不一致 → 清空后改用 `document.execCommand("insertText")`；
3. 仍不一致 → `COMPOSER_MISMATCH`，**绝不发送**。

### 4.3 安全边界
- 填入前检查草稿：非空且未 `force` → `DRAFT_PRESENT`，**不覆盖**；
- 填入前后各校验一次 `session_id`；
- 代码中不存在任何点击发送按钮/提交表单的路径；
- 自动化测试 T8 断言：prepare 后消息数不变。

### 4.4 回读比对
不同编辑器对块级元素换行映射不同（ProseMirror `<p>` vs 普通 contenteditable），因此按**非空行序列**比对；空行保真度不在 v1 保证范围内（已知限制）。

## 5. 自动化测试（mock，12/12）

| # | 场景 | 结果 |
|---|---|---|
| T1 | 空输入框读取 | PASS |
| T2 | 填入并 verified | PASS（method=paste） |
| T3 | 回读一致 | PASS |
| T4 | 已有草稿 → DRAFT_PRESENT 且不覆盖 | PASS |
| T5 | force=true 覆盖草稿 | PASS |
| T6 | 多行 + 代码块保留 | PASS |
| T7 | 中文/emoji/特殊字符保留 | PASS |
| T8 | **未发送**（消息数不变） | PASS |
| T9 | 输入框缺失 → found=false + COMPOSER_UNAVAILABLE | PASS |
| T10 | clear_composer 清空 | PASS |
| T11 | 未知会话 → SESSION_NOT_FOUND | PASS |
| T12 | 空文本 → INVALID_REQUEST | PASS |

## 6. 真实页面验证（待用户手动执行）

自动化环境限制：扩展重载必须通过 `edge://extensions` 操作，bsk 不支持该内部页面；
本机 bsk 在 ChatGPT 标签页上出现 frame 同步失败，因此真实页验证改由用户手动执行。

步骤（扩展已重载为 v0.4.0、bridge 已启动）：

```powershell
cd D:\CortexFlow\contextflow-research\experiments\playwright-probe

# 1) 读取输入框状态（应为 found=true, empty=true）
python cfdev004_prepare.py --session <会话ID> --read

# 2) 填入测试文本（应为 prepared=true, verified=true, sent=false）
python cfdev004_prepare.py --session <会话ID> --text "ContextFlow prepare test"

# 3) 观察 ChatGPT 输入框中出现该文本（不要点发送）

# 4) 再次读取确认
python cfdev004_prepare.py --session <会话ID> --read

# 5) 清空输入框
python cfdev004_prepare.py --session <会话ID> --clear
```

预期：输入框出现文本、消息数不变、清空后为空。测试文本为无害占位符，全程不发送。

## 7. 已知限制

- 空行保真度未保证（按非空行比对）；
- 合成 paste 依赖编辑器对不可信事件的接受度，前端改版可能失效（有 execCommand 回退）；
- 未验证超长文本（>10k 字符）与富文本（图片/附件）；
- 本版本无发送能力，`sent` 恒为 false。

## 8. 下一步（实验 B）

1. 用户在真实页面手动点击发送（或由测试脚本模拟确认）；
2. 发送后立即用 `wait_for_reply`（CF-DEV-003）观察新回复；
3. 验证"填入 → 人工确认 → 等待 → 读取"半自动闭环；
4. 再讨论实验 C（程序化发送）的幂等与去重设计。
