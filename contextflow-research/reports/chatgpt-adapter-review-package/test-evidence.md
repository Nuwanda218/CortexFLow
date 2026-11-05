# 测试结果与证据索引

> 所有结果来自 2026-11-05 本次审计的实际执行，不引用历史报告结论。
> 环境：Windows 11 Build 26200 / Microsoft Edge 156.0.4314.58 / Python 3.10.11 / Playwright 1.64.1 / FastAPI 0.139.2 / uvicorn 0.51.0。

## 1. 审计复现（`audit_repro.py`）— 19/19 通过

| 编号 | 测试 | 结果 | 关键证据 |
|---|---|---|---|
| A1-01 | MV3 扩展加载、Service Worker 存在 | PASS | extension id `ecdmenhoofjafenfmeflnkfomcffoohl` |
| A2-01/02/03 | mock 页页面识别、会话引用、消息统计 | PASS | kind=mock-chat、session-a、2 条消息 |
| A3-01/02 | SPA pushState 导航检测 | PASS | `webNavigation.onHistoryStateUpdated` 1 条；content 跟随 session-b |
| A4-01/02/03 | mock 页消息读取 | PASS | 4 条、lastStatus=complete、user/assistant 各 2 |
| A5-01~04 | Core↔扩展双向通信 | PASS | WS connected；Core 可见客户端；推送 delivered=1；DOM 通知条渲染 |
| A6-01 | `session_ref` 路由 | PASS（验证 bug 存在） | 请求 session-b，通知实际投到活动标签页 session-a |
| A7-01/02 | 断线重连 | PASS | Core 停止→disconnected；重启→connected（attempts=3） |
| A8-01 | 假剪贴板注入 | PASS | 文本 `audit fake paste` 进入 ProseMirror-like 编辑器 |
| A9-01/02 | 真实 chatgpt.com 公开页 | PASS | content script 运行；kind=chatgpt；session=none；messages=0 |

**证据文件**：`contextflow-research/_artifacts/audit-repro-evidence.json`

## 2. Core 协议测试（`bridge_test.py`）— 13/13 通过

| 编号 | 测试 | 结果 |
|---|---|---|
| D-01a/b | 连接 welcome、WS 事件入库 | PASS |
| D-02a/b | Core→扩展推送、payload 形状 | PASS |
| D-03a | 非法消息不杀连接 | PASS |
| D-03b | 重复消息被接受（无去重） | PASS（记录风险） |
| D-03c/d | 离线入队、重连补发（replayed=true） | PASS |
| D-03e/f/g/h | 错误 token 拒绝、http(s) Origin 拒绝、扩展 Origin 接受、缺 token 拒绝 | PASS |

**证据文件**：`contextflow-research/_artifacts/bridge-test-results.json`

## 3. 假剪贴板跨世界测试（`fake_clipboard_test.py`）— 6/6 通过

- content script（隔离世界）派发合成 `ClipboardEvent('paste')` + `DataTransfer`；
- 页面（ProseMirror-like）收到事件，`hasDataTransfer=true`，文本插入；
- 重复注入正常；
- OS 剪贴板哨兵值前后不变。

**证据文件**：`contextflow-research/_artifacts/fake-clipboard-evidence.json`

## 4. 四态交接测试（`handoff_test.py`）— 7/7 通过

- Core 推送 `worker_completed` → 侧边栏出现"待处理"；
- 点击"复制汇报"→ 剪贴板得到格式化 Markdown；
- 点击"已确认交接"→ 状态 confirmed；
- 全程未打开/触碰 ChatGPT 页面。

**证据文件**：`contextflow-research/_artifacts/handoff-evidence.json`

## 5. 本次未执行的测试

| 测试 | 原因 |
|---|---|
| 真实登录态 ChatGPT 页面上的扩展读取/注入 | 需用户授权与登录态；本次仅在公开挑战页验证 content script 可运行 |
| 向真实 ChatGPT 发送消息 | 按任务要求，未获明确授权，不执行 |
| Service Worker 自然休眠/强制终止复测 | 历史曾验证，本次未复现 → 标记 IMPLEMENTED_UNVERIFIED |
| 多 Project / 重命名 / 移动会话后的 URL 变化 | 需用户操作，本次未执行 |

## 6. 证据文件位置

所有原始证据位于 `contextflow-research/_artifacts/`：

| 文件 | 内容 |
|---|---|
| `audit-repro-evidence.json` | 本次审计 19 项复现结果 |
| `bridge-test-results.json` | Core 协议 13 项结果 |
| `fake-clipboard-evidence.json` | 假剪贴板跨世界 6 项结果 |
| `handoff-evidence.json` | 四态交接 7 项结果 |
| `core-server.log` / `core-events.jsonl` | Core 运行日志与事件流（含脱敏后的测试事件） |
