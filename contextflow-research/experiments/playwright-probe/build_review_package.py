#!/usr/bin/env python3
"""Build the ChatGPT adapter review package (source copies + critical code excerpts).

- Copies key source files under review-package/source/ preserving relative paths.
- Generates critical-code.md with real excerpts and line numbers.
- No secrets are copied: the dev token is masked in the generated excerpt file.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # contextflow-research
PKG = ROOT / "reports" / "chatgpt-adapter-review-package"
SRC = PKG / "source"

FILES = [
    "experiments/edge-extension-probe/manifest.json",
    "experiments/edge-extension-probe/background.js",
    "experiments/edge-extension-probe/content.js",
    "experiments/edge-extension-probe/popup.js",
    "experiments/edge-extension-probe/sidepanel.js",
    "experiments/local-core-bridge/core_server.py",
    "experiments/local-core-bridge/bridge_test.py",
    "experiments/playwright-probe/audit_repro.py",
    "experiments/playwright-probe/fake_clipboard_test.py",
    "experiments/playwright-probe/handoff_test.py",
    "experiments/mock-chat-page/prosemirror-like.html",
    "experiments/mock-chat-page/index.html",
    "experiments/mock-chat-page/app.js",
    "experiments/mock-chat-page/serve.py",
]

SNIPPETS = [
    ("experiments/edge-extension-probe/manifest.json", 7, 22, "扩展权限与 host_permissions"),
    ("experiments/edge-extension-probe/background.js", 15, 18, "Core 地址与实验 token（已脱敏）"),
    ("experiments/edge-extension-probe/background.js", 56, 103, "WebSocket 连接、重连退避、发送"),
    ("experiments/edge-extension-probe/background.js", 105, 125, "Core 命令处理与 session_ref 路由（含字段不匹配 bug）"),
    ("experiments/edge-extension-probe/background.js", 143, 151, "SPA 导航事件监听"),
    ("experiments/edge-extension-probe/background.js", 162, 170, "页面状态上报与缓存"),
    ("experiments/edge-extension-probe/background.js", 193, 217, "chrome.scripting MAIN world 探测"),
    ("experiments/edge-extension-probe/content.js", 83, 111, "页面分类、URL 会话引用、mock 会话"),
    ("experiments/edge-extension-probe/content.js", 113, 149, "readPageState：消息/生成状态解析（仅 mock 契约）"),
    ("experiments/edge-extension-probe/content.js", 208, 231, "假剪贴板：合成 paste 注入"),
    ("experiments/edge-extension-probe/content.js", 232, 244, "Core 通知条渲染"),
    ("experiments/local-core-bridge/core_server.py", 62, 73, "Origin 白名单与 token 校验"),
    ("experiments/local-core-bridge/core_server.py", 121, 147, "notify 入队/广播"),
    ("experiments/local-core-bridge/core_server.py", 150, 193, "WebSocket 端点：鉴权、welcome、pending 重放"),
    ("experiments/edge-extension-probe/sidepanel.js", 62, 77, "用户确认式交接：写剪贴板"),
]


def mask(line: str) -> str:
    # Mask the experiment-only token if it appears in an excerpt.
    return line.replace("dev-token-cf-exp-001", "dev-token-***MASKED***")


def main() -> int:
    shutil.rmtree(PKG, ignore_errors=True)
    (PKG).mkdir(parents=True, exist_ok=True)
    for rel in FILES:
        src = ROOT / rel
        dst = SRC / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        # Mask the experiment-only shared token in the review package copies.
        text = text.replace("dev-token-cf-exp-001", "dev-token-***MASKED***")
        dst.write_text(text, encoding="utf-8")

    lines_out = [
        "# Critical Code Excerpts",
        "",
        "> 所有片段均来自 `source/` 下的真实源码（与仓库同路径），行号对应原始文件。",
        "> 实验用 token 已脱敏为 `dev-token-***MASKED***`。",
        "",
    ]
    for rel, start, end, title in SNIPPETS:
        path = ROOT / rel
        all_lines = path.read_text(encoding="utf-8").splitlines()
        excerpt = all_lines[start - 1:end]
        lang = "json" if rel.endswith(".json") else "javascript" if rel.endswith(".js") else "python"
        lines_out.append(f"## {title}")
        lines_out.append("")
        lines_out.append(f"`{rel}` 第 {start}–{end} 行")
        lines_out.append("")
        lines_out.append(f"```{lang}")
        for i, ln in enumerate(excerpt, start=start):
            lines_out.append(f"{i:4d} | {mask(ln)}")
        lines_out.append("```")
        lines_out.append("")
    (PKG / "critical-code.md").write_text("\n".join(lines_out), encoding="utf-8")
    print(f"package built at {PKG}")
    print(f"files copied: {len(FILES)}, snippets: {len(SNIPPETS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
