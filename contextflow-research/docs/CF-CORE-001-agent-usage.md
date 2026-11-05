# CF-CORE-001 Agent 使用指南

本指南面向使用稳定 Endpoint ID 的调用者。底层 Adapter 原始接口仍见 `agent-usage.md`。

## 最小示例

从 `contextflow-research` 目录运行：

```python
import asyncio

from contextflow_core import (
    EndpointRegistry,
    EndpointService,
    WebSocketChatGPTAdapter,
)


async def main():
    adapter = WebSocketChatGPTAdapter()
    service = EndpointService(adapter, EndpointRegistry())
    try:
        sessions = await service.discover()
        target = next(item for item in sessions if item.bindable)

        bound = await service.bind(target.native_session_id)
        endpoint_id = bound.endpoint.endpoint_id

        resolution = await service.resolve(endpoint_id)
        print(resolution.to_dict())

        snapshot = await service.read(endpoint_id)
        print(snapshot.to_dict())
    finally:
        await adapter.close()


asyncio.run(main())
```

Endpoint 注册表默认位置：

- Windows：`%LOCALAPPDATA%\ContextFlow\endpoints.json`
- 设置 `CONTEXTFLOW_ENDPOINT_REGISTRY` 可以覆盖路径。

## 接口语义

### `discover()`

返回当前 Adapter 能实时探测到的打开页面。只有 `ready=true`、具有稳定原生 Session ID 和 `tab_id` 的记录才有 `bindable=true`。

### `bind(session_id)`

- 只进行原生 Session ID 精确匹配。
- 重复绑定返回原 Endpoint ID，`created=false`。
- 零匹配为 `SESSION_NOT_FOUND`。
- 同一 Session 多标签页为 `AMBIGUOUS_SESSION`。
- 不会按标题、URL 相似度或活动标签页替换目标。

### `resolve(endpoint_id)`

返回三态之一：

- `available`：唯一可访问位置。
- `offline`：页面关闭、Bridge 断开或内容脚本不可用；绑定仍保留。
- `ambiguous`：同一原生 Session 出现在多个标签页；不自动选择。

`tab_id` 只存在于本次 Resolution 中，不属于 Endpoint 持久身份。

### `read(endpoint_id)`

先重新解析，再以 `session_id + tab_id` 双重约束读取。只读请求发生路由竞态或短暂断线时最多重新解析一次。

消息字段：

```text
message_id / message_ids
role / content / status
turn_group_id / turn_position
evidence.turn_key / dom_order / adapter_status
```

`turn_group_id` 是当前 DOM 中的结构证据。Assistant 的 `turn_position` 可以是 `2`，不能假设恒为 `1`。

所有快照固定声明：

```text
history_scope = rendered_dom
absence_means_deleted = false
```

## 运行要求

1. 启动 `contextflow-extension/bridge_server.py --port 8766`。
2. Edge 已加载 ContextFlow Adapter。
3. 打开带 token 的本地 relay 页：

```text
http://127.0.0.1:8766/bridge_client.html?token=dev-token-cf-exp-002
```

4. 目标 ChatGPT 会话必须至少在一个标签页打开。

## 真实验证脚本

```powershell
python experiments/playwright-probe/cfcore001_real.py `
  --phase before `
  --session <native-session-id>

# 精确刷新目标 ChatGPT 标签页后
python experiments/playwright-probe/cfcore001_real.py `
  --phase after `
  --session <native-session-id>
```

脚本只记录消息结构和长度，不把消息正文写入证据文件。

## 不在本阶段的能力

- 不自动打开侧边栏目录中的离线会话。
- 不处理发送 Operation 生命周期。
- 不承诺 User/Assistant 因果关联。
- 不修复 `wait_for_reply` 的懒加载误关联问题。
