# local-core-bridge — 本地 Python Core 最小通信服务

FastAPI + WebSocket，绑定 `127.0.0.1:8765`，用于验证扩展 ↔ Core 的双向通信与异常路径。**不是生产 Core。**

## 端点

| 端点 | 作用 |
|---|---|
| `GET /health` | 健康检查、在线客户端、pending 数、计数器 |
| `GET /api/events` | 最近接收的事件 |
| `POST /events` | 扩展 HTTP 兜底上报（Origin 白名单 + token） |
| `POST /api/notify` | 测试/Worker 完成 → 推送给扩展；离线则入队 |
| `WS /ws?token=...` | 扩展长连接；连接时补发 pending |

## 安全设计（实验级）

- 仅监听 `127.0.0.1`；
- 共享 token（`CF_CORE_TOKEN`，默认 `dev-token-cf-exp-001`，**仅实验用**）；
- Origin 白名单：`chrome-extension://` / `moz-extension://` / 无 Origin；任何 `http(s)` Origin 拒绝；
- 不接受来自网页内容的字符串直接触发命令执行。

## 运行与测试

```powershell
python core_server.py --port 8765
python bridge_test.py --port 8765     # 期望 13/13
```

## 实测结果

- 双向 WS、离线排队 + 重连补发、非法/重复消息、错误 token、恶意 Origin、缺 token：全部符合预期；
- 浏览器侧全链路见 `../playwright-probe/playwright_probe.py` 的 B-07。

## 已知限制

- 事件仅内存存储，Core 重启即丢失；
- 无消息去重/幂等（重复消息会被接受）；
- token 为硬编码实验值，生产需改为配对时下发的每安装密钥。
