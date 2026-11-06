# CF-CORE-003：阶段 A 运行与人工验收

阶段 A 只接收、校验和保存 Supervisor 消息，不解析或执行任务。入口为：

```bash
python -B -m contextflow_core.receiver --help
```

## 运行条件与配置

从 `contextflow-research` 运行。Python 标准库提供 SQLite；websockets、FastAPI、uvicorn 复用项目已有依赖，无需为本阶段安装新依赖。

| 配置 | 用途 |
|---|---|
| `CONTEXTFLOW_BRIDGE_URI` / `--uri` | Bridge 的 `/ws` 地址；必须显式配置 |
| `CF_BRIDGE_TOKEN` | 与 Bridge 一致的 token；URI 没有 token 参数时自动附加 |
| `CONTEXTFLOW_BRIDGE_ORIGIN` / `--origin` | 可选 Origin；默认由 URI 的主机派生 |
| `CONTEXTFLOW_ENDPOINT_REGISTRY` / `--registry` | 现有 Endpoint Registry 路径 |
| `CONTEXTFLOW_MESSAGE_STORE` / `--database` | SQLite 路径；默认在默认 Registry 同目录的 `messages.sqlite3` |
| `--busy-timeout` | SQLite 锁等待，默认 5 秒，允许 0 |
| `--identity-timeout` | 稳定来源查询，默认 5 秒 |
| `--ack-timeout` | 发送确认等待，默认 5 秒 |
| `--connection-timeout` | 建立 Bridge 连接，默认 5 秒 |
| `--once` | 第一条 accepted 确认发送后退出，包括 duplicate |

URI 可沿用已有带 token 的形式，但推荐通过环境变量提供 token，不在代码、报告或命令记录中写真实值。重复 token 参数、嵌入的 userinfo、无 token、无效 URI 会被拒绝，URI 异常不输出凭据。

`--registry` 必须与 Bridge 使用同一份绑定文件；自定义路径时建议同时显式传入 `--database`。程序不接受 SQLite `:memory:` 或 URI 内存模式，不把内存事务承诺为持久接收。

## Bridge 单连接约束

Bridge 现在只允许一个外部 `/ws` 连接，另有一个浏览器 `/ws_page` 连接。第二个外部连接会被关闭，code 为 `4409`，不会抢占现有客户端。

- 正式 Receiver 启动前，应由操作者确认并停止 Mock Receiver 和独立 RPC/调试客户端。
- Mock 先连接时，正式 Receiver 会报 `CORE_CONNECTION_REJECTED`，不会假装接管。
- Endpoint 发现/绑定等旧 RPC 工具应在 Receiver 启动前使用，并正常关闭连接。
- Receiver 运行时可以另开 SQLite 查询连接；限制针对 WebSocket，不是 SQLite 连接。
- A 尚未实现 E 的通用单连接 RPC 多路复用。不要通过第二个外部客户端绕过约束。

Envelope、消息 type、accepted/duplicate/conflict 语义未改变。正式 Receiver 回显提交中已有的 `request_id`，Bridge 检查当前尝试，避免旧 ack 误确认另一个 Envelope。旧 Mock 不带该字段仍可单独运行，但不提供正式 Receiver 的尝试级确认保障。

## 启动示例（Git Bash）

以下地址和端口只是本地配置示例，不是程序写死的部署配置。已有端口被占用时，不能直接重启或杀掉未知进程；使用空闲端口，或由操作者先确认既有服务的处理方式。

```bash
cd /d/CortexFlow/contextflow-research
export BRIDGE_PORT=8767
export CONTEXTFLOW_BRIDGE_URI="ws://127.0.0.1:${BRIDGE_PORT}/ws"
export CONTEXTFLOW_ENDPOINT_REGISTRY="${LOCALAPPDATA}/ContextFlow/endpoints.json"
export CONTEXTFLOW_MESSAGE_STORE="${LOCALAPPDATA}/ContextFlow/messages.sqlite3"
read -rs -p 'Bridge token: ' CF_BRIDGE_TOKEN
printf '\n'
export CF_BRIDGE_TOKEN
```

在 Bridge 终端使用同一配置启动：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B contextflow-extension/bridge_server.py \
  --port "$BRIDGE_PORT" --registry "$CONTEXTFLOW_ENDPOINT_REGISTRY"
```

在 Edge/Chrome 中加载现有 ContextFlow 扩展，打开对应的本地 `bridge_client.html` 页面，并确认只有一个适用的 Bridge 页面。多份 Bridge 页面会影响扩展选择，应由操作者处理，不自动关闭用户页面。

检查 health：

```bash
curl -s "http://127.0.0.1:${BRIDGE_PORT}/health"
```

进入正式接收前，期望 `page_connected=true`、`external_clients=0`。缺少页面连接时，启动 Receiver 本身不能让 ChatGPT Extension 链路就绪。

### 先完成已有 Endpoint 绑定

已有绑定可直接复用；不要编辑 Registry JSON。需要新绑定时，在 Receiver 尚未运行、目标会话已打开的条件下使用已有 API：

```bash
export CF_SUPERVISOR_SESSION='<ChatGPT URL /c/ 后的原生会话 ID>'
PYTHONDONTWRITEBYTECODE=1 python -B - <<'PY'
import asyncio
import os
from contextflow_core import EndpointRegistry, EndpointService, WebSocketChatGPTAdapter
from contextflow_core.receiver import bridge_connection_settings

async def main():
    uri, origin = bridge_connection_settings()
    adapter = WebSocketChatGPTAdapter(uri=uri, origin=origin)
    try:
        result = await EndpointService(adapter, EndpointRegistry()).bind(os.environ['CF_SUPERVISOR_SESSION'])
        print('Endpoint:', result.endpoint.endpoint_id, 'created:', result.created)
    finally:
        await adapter.close()

asyncio.run(main())
PY
```

绑定仅按原生 Session 精确匹配，不按标题或活动标签页猜测。也可参阅 `CF-CORE-001-agent-usage.md`。

### 启动正式 Receiver

在另一终端配置同样的 URI、token、Registry 和数据库路径后运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m contextflow_core.receiver \
  --registry "$CONTEXTFLOW_ENDPOINT_REGISTRY" \
  --database "$CONTEXTFLOW_MESSAGE_STORE"
```

日志只记录状态、Core Message ID、duplicate 和错误码，不记录正文或连接 token。连接结束后程序退出；本阶段不自动重连，也不自动处理已保存 pending 消息。重新启动使用原数据库即可继续接收。

## 真正 ChatGPT Extension 到 SQLite 的人工验收

本轮未完成真实 ChatGPT 验证：既有 CDP 9222/9333 不可连接，既有 Bridge 的 `page_connected=false`。没有安装依赖、重配浏览器或重启既有服务来强行完成。

可在操作者确认环境后按以下步骤验收：

1. 确认使用本分支的 Bridge 和正式 Receiver，Mock 及独立外部 WS 客户端已关闭。
2. 确认 Bridge 页面和扩展已连接，目标 ChatGPT 原生 Session 已有正确绑定。
3. 选一条已有、尚未提交成功的 Assistant 消息。等待生成结束、确认有原生消息 ID 后点击纸飞机。不必向 ChatGPT 发布新文本。
4. 检查网页得到 accepted/已送达，Receiver 日志有 Core Message ID，SQLite 中存在对应 Message。
5. 核对 `envelope.source.endpoint_id/native_session_id/message_id`、正文、submission_id、接收时间和 pending 状态。
6. 再点同一条已 accepted 消息，确认记录数不增加。这主要验证前端缓存，不独立证明服务端幂等；服务端重复与冲突另有自动化测试。
7. 正常停止并重新启动 Receiver，使用原数据库查询，确认原 Message ID 与正文保留。再选另一条未提交消息，可以继续保存。
8. 如先在没有 Receiver 的情况下提交得到 NO_RECEIVER，再启动并重试，应复用该次逻辑提交 ID；此前未落盘时第一次成功的 duplicate 应为 false。
9. 如得到 unknown，不能据此认定没有保存。先查询数据库，再使用原 Envelope/ID 重试。不要为测试清空用户的全部提交缓存。

拒绝确认并非一律证明数据库从未写入：确认超时、断线或 await 取消可能发生在实际 commit 后。原 ID/Envelope 重试会安全返回原 Message 或冲突。

### 使用公共 API 查询

该查询不占用 Bridge 的外部连接，也不会触发 Parser：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B - <<'PY'
import asyncio
import os
from contextflow_core.contracts import MessageKind
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.message_store import SQLiteMessageStore

async def main():
    store = SQLiteMessageStore(os.environ['CONTEXTFLOW_MESSAGE_STORE'], RegistryEndpointIdentityReader())
    try:
        for message in await store.list_pending(MessageKind.SUPERVISOR_INSTRUCTION, 20):
            envelope = message.envelope
            print(message.message_id, envelope.submission_id, envelope.source.endpoint_id,
                  envelope.source.native_session_id, envelope.source.message_id,
                  len(envelope.content.text), message.processing_state.value)
    finally:
        await store.close()

asyncio.run(main())
PY
```

也可通过 `get_message(MessageId(...))`、`get_message_by_submission_id(SubmissionId(...))` 查询。后者是具体 Store 的方法，不改动冻结 Port。

## 自动化验证

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/contracts -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/core/test_message_store.py tests/core/test_endpoint_identity.py tests/core/test_receiver.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/adapter/test_bridge_protocol.py tests/adapter/test_core_receiver_integration.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests -q -p no:cacheprovider
```

Bridge 集成测试启动真实 Bridge、正式 Receiver/CLI 和 SQLite，但 page 端是假 WebSocket，不能当作真实 ChatGPT/扩展验证。测试数据库和进程均隔离；数据库写入/commit/回滚故障注入只用于这些临时数据库，不能照搬到用户生产库。

## 数据与安全边界

- 保存完整原始 Envelope 的字段值与正文；规范化 JSON 的键顺序不是原 WS 字节序。
- 来源核验只证明与当前 Registry 的稳定绑定一致，不认证 ChatGPT 账号、原生消息真实性或任务语义。
- 数据库文件包含明文消息，应按本地敏感数据管理。运行中的备份需要 SQLite 一致性备份方式，不能只复制主文件而忽略 WAL。
- Schema v1 只接受本模块拥有的 messages 表与必要索引定义；未知版本、未知 messages 触发器或改写的约束会拒绝，不自动迁移或覆盖。
- Store 的内部 AgentSubmission 保存由冻结契约要求，不是 Agent Report Intake；本阶段没有 Agent 外部入口、Task 归属核验或报告回传。
