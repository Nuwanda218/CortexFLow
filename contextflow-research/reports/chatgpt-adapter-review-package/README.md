# ChatGPT Adapter 源码审阅包

本审阅包用于让其他 Agent / ChatGPT 会话快速、准确地理解 ContextFlow 现有 Edge 扩展原型的**真实能力**，而不是历史报告中的设想。

## 内容

| 文件 | 用途 |
|---|---|
| `README.md` | 本说明 |
| `file-index.md` | 关键源码文件清单与职责 |
| `critical-code.md` | 与网页交互、通信、消息路由直接相关的**真实代码片段**（含原始路径与行号，token 已脱敏） |
| `test-evidence.md` | 本次审计的测试结果与证据索引 |
| `source/` | 关键源码副本，保留原始相对路径（`source/experiments/...`） |

## 阅读顺序建议

1. 先读审计报告 `chatgpt-adapter-capability-audit.md`（与本包同级，ZIP 根目录）。
2. 再看 `file-index.md` 定位文件。
3. 需要核对实现细节时读 `critical-code.md` 与 `source/` 中的真实源码。
4. 需要判断"哪些结论有实测支撑"时读 `test-evidence.md`。

## 重要声明

- 本包**不包含**任何 Token、Cookie、密钥、浏览器用户数据或登录凭据。
- 源码中的实验用 token 已在 `critical-code.md` 与 `source/` 副本中脱敏为 `dev-token-***MASKED***`。
- 本包描述的是**研究原型**，不是生产实现；能力状态以审计报告的 Capability Matrix 为准。
- 未向真实 ChatGPT 发送任何消息；未调用 ChatGPT 内部 API。

## 目录结构

```text
chatgpt-adapter-review-package/
├── README.md
├── file-index.md
├── critical-code.md
├── test-evidence.md
└── source/
    └── experiments/
        ├── edge-extension-probe/   # manifest/background/content/popup/sidepanel
        ├── local-core-bridge/      # core_server.py / bridge_test.py
        ├── mock-chat-page/         # 模拟页与 prosemirror-like 测试页
        └── playwright-probe/       # audit_repro.py / fake_clipboard_test.py / handoff_test.py
```
