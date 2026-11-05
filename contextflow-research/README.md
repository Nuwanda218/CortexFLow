# ContextFlow Research — Coordinator Adapter 技术可行性探索（CF-EXP-001）

本目录是 **一次性技术调查 + 最小原型 + 实验验证** 的产物，不是 ContextFlow 的正式实现，也不包含任何生产代码。

- 主报告：[`reports/feasibility-report.md`](reports/feasibility-report.md)
- 实验记录：[`reports/experiment-log.md`](reports/experiment-log.md)
- 方案对比：[`reports/architecture-comparison.md`](reports/architecture-comparison.md)
- 未解决问题：[`reports/unresolved-questions.md`](reports/unresolved-questions.md)
- 官方文档索引：[`references/official-docs.md`](references/official-docs.md)
- 原始证据：[`_artifacts/`](_artifacts/)（JSON 结果、日志、截图）

## 一句话结论

**第一版最值得采用：用户日常 Edge 中的 ChatGPT 原生会话 + 最小权限 MV3 扩展（会话观察、URL 手动绑定、用户显式确认的抓取/输入）+ 本地 Python Core（`127.0.0.1` WebSocket）；Electron 留作二期容器候选；官方 MCP/Tunnel 作为长期语义协作方向。**

## 目录结构

```text
contextflow-research/
├── README.md                      # 本文件
├── reports/
│   ├── feasibility-report.md      # 主报告（执行摘要 + 10 问回答 + 推荐路线）
│   ├── experiment-log.md          # 逐实验记录（目的/环境/步骤/结果/证据/结论/局限）
│   ├── architecture-comparison.md # 方案 A–E 多维对比与评分
│   └── unresolved-questions.md    # 未解决问题与补证方式
├── experiments/
│   ├── edge-extension-probe/      # MV3 扩展原型（manifest/background/content/popup/sidepanel）
│   ├── mock-chat-page/            # 本地模拟聊天页（两会话、SPA、流式）
│   ├── local-core-bridge/         # FastAPI + WebSocket 本地 Core 与协议测试
│   ├── playwright-probe/          # Edge 扩展端到端测试 + 通用 Playwright 能力测试 + iframe 测试
│   └── electron-probe/            # Electron 双栏最小应用 + 探针
├── references/
│   ├── official-docs.md           # 官方文档索引（按结论组织）
│   └── electron-vs-extension-feasibility.md  # Electron vs 扩展的专项调研
└── _artifacts/                    # 证据：JSON 结果、日志、截图、临时 profile
```

## 环境

| 项 | 值 |
|---|---|
| OS | Windows 11 Build 26200 |
| Edge | 156.0.4314.58 |
| Python | 3.10.11（fastapi/uvicorn/websockets/requests/playwright 1.64.1） |
| Node | 24.15.0 |
| Electron | 45.2.2 |

## 如何复现

### 1) 启动本地模拟页与 Core

```powershell
cd experiments\mock-chat-page
python serve.py --port 8787          # http://127.0.0.1:8787/mock/chat/session-a

cd experiments\local-core-bridge
python core_server.py --port 8765    # ws://127.0.0.1:8765/ws
```

### 2) 协议测试（无需浏览器）

```powershell
cd experiments\local-core-bridge
python bridge_test.py --port 8765    # 期望 13/13
```

### 3) Edge 扩展端到端（Playwright 驱动无头 Edge）

```powershell
cd experiments\playwright-probe
python playwright_probe.py           # 期望 25/25
python playwright_generic.py         # 期望 12/12
python iframe_probe.py               # 期望 4/4
```

### 4) Electron 双栏探针

```powershell
cd experiments\electron-probe
.\node_modules\electron\dist\electron.exe . --probe --no-sandbox --disable-gpu --disable-crash-reporter --noerrdialogs
```

> 本机注意：Electron 默认 `%APPDATA%\Electron` 被安全软件拒绝访问会导致崩溃；`main.js` 已改用 `_artifacts/electron-userdata`。

### 5) 手动加载扩展（需要用户操作）

1. Edge 打开 `edge://extensions` → 开启“开发人员模式”；
2. “加载解压缩的扩展” → 选择 `experiments/edge-extension-probe`；
3. 打开 `http://127.0.0.1:8787/mock/chat/session-a`，点击工具栏图标查看 popup；
4. 点击 “Open side panel” 查看侧边栏（需用户手势）。

## 证据强度约定

本仓库严格区分：**写了代码（L1）< 静态检查（L2）< 模拟页运行成功（L3）< 真实目标页运行成功（L4）< 官方文档支持（L5）**。
所有“真实 ChatGPT 页面”相关结论均未达到 L4，报告中已逐条标注。

## 明确未做的事

- 未访问用户真实 ChatGPT 账号或私人会话；未提取 Cookie/Token；未使用用户真实 Edge profile 启动自动化。
- 未调用 ChatGPT 内部 API；未绕过 Cloudflare 或任何安全策略。
- 未在真实 ChatGPT 页面执行消息发送/回复提取（平台规则未确认，任务要求先核实）。
- 未实现 Workflow/Slot/Task 状态机、Worker 调度、数据库、正式 UI（不属于本任务）。
