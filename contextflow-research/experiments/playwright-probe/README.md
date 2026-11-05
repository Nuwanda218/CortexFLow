# playwright-probe — 浏览器自动化实验

三组脚本，全部使用本机 Edge（`channel="msedge"`，无需下载 Chromium）。

| 脚本 | 作用 | 期望结果 |
|---|---|---|
| `playwright_probe.py` | 加载未打包扩展，端到端验证 B/A 任务（页面识别、SPA 导航、隔离世界、流式状态、扩展内部通信、Core 全链路、popup、SW 生命周期、公开 chatgpt.com） | 25/25 |
| `playwright_generic.py` | 通用能力：启动 Edge、独立 profile、会话 URL、输入/等待/读取、切换/重开、多标签、profile 复用、CDP 连接、人机冲突 | 12/12 |
| `iframe_probe.py` | iframe 嵌入 chatgpt.com 是否可行 | 4/4（被 X-Frame-Options 拒绝） |
| `debug_mainworld.py` | MAIN world 桥接的定位调试脚本（保留作证据） | — |

## 运行

```powershell
python playwright_probe.py
python playwright_generic.py
python iframe_probe.py
```

前置：mock 页（8787）与 Core（8765）已启动。

## 证据

结果写入 `../../_artifacts/playwright-evidence.json`、`playwright-generic-evidence.json`、`iframe-evidence.json`，截图在 `../../_artifacts/screenshots/`。

## 注意

- 未在真实 ChatGPT 登录态下执行任何自动化；
- `playwright_generic.py` 的 profile 复用实验只复制 `Local State` + `Default/Preferences` 骨架，**不复制 cookie 数据库**，也不对真实 profile 直接启动（避免损坏/凭据风险）；
- 无头 Edge 访问 chatgpt.com 会得到 Cloudflare 403 challenge（已记录）。
