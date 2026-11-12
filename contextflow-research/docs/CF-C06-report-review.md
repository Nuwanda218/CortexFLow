# C-06 执行报告与 Supervisor 审核

从已有的 C-05 Delivery 开始，`TaskReportService.generate_report(delivery_id)` 将已保存的执行结果转为 `AgentReportEnvelope`。Agent 正文保存在 Message Store，执行事实和本地文件引用单独保存。报告包含 Task、Delivery、Execution 和 Agent 的标识。重复生成返回同一份报告。

## 回传与审核

已有 Core 调用方可用正在连接 Bridge 的 `CoreReceiver` 发送报告：

```python
report = await reports.generate_report(delivery_id)
report = await reports.send_report(report.report_id, receiver)
```

也可以在 Receiver 启动时显式指定一个待回传的 Delivery。使用与阶段 A/B 相同的 SQLite、Endpoint Registry 和 Bridge 配置：

```powershell
python -m contextflow_core.receiver --database <SQLite路径> --registry <Endpoint注册表路径> --return-delivery-id <DeliveryId>
```

Bridge 只允许一个 Core Receiver 连接。使用该启动参数时，Receiver 回传后继续监听审核。报告的回传目标来自 Task 的原始 Message。Core 和 Bridge 均核对 Endpoint 与 Session，浏览器确认该 Session 出现新消息后才记录送达。已有草稿会保留。大输出仅回传节选，全文仍在本地。

审核由授权调用方显式发起。扩展内部可从受信任的扩展页面调用下列消息；后台探测当前页面，并从 C-01 绑定中取得审核来源。Core 通过 `submit_report_review` 控制动作再次核对原 Endpoint、原 Session、Task、报告和随报告送达的审核码。现有 Bridge 是受信任的本地中转，审核码应留在原会话和授权调用方之间。审核入口目前是结构化接口。

```javascript
await chrome.runtime.sendMessage({
  type: "cf:supervisor_review_report",
  report_id: "<ReportId>",
  task_id: "<TaskId>",
  review_code: "<报告中的审核码>",
  decision: "accept",
  note: ""
});
```

审核决定也支持 `request_changes` 和 `reject`。相同决定可重放，已保存的决定和意见保留。送达、Supervisor 审核和用户最终验收彼此独立；C-06 只写审核记录。要求返工时保留原 Task、Execution 和报告，供下一步显式处理。

## 恢复与验证

- 回传先写入 `sending`。确定未发送的情况可在问题解除后显式再调。结果不明则保存 `unknown`。原发送调用已经停止时，可用 `recover_report_send(report_id)` 将遗留的 `sending` 结算为 `unknown`；这两种不确定情况均保留报告供人工核对。
- Agent 执行结果为 `unknown` 或尚未保存时，先走 C-05 对账。能生成正式报告的终态为 `finished`、`failed` 和 `cancelled`。
- 本地端到端测试使用 Fake Parser、Fake Agent、真实本地 Bridge 和模拟浏览器客户端，覆盖指令入库、执行、报告先存后发、原 Session 审核以及不确定回执。实际 ChatGPT 页面回传仍待授权验证。
- 未来 Blackboard 可用 Task、Execution 和 Report ID 关联共享成果。协作派生任务可复用 C-04 的 `parent_task_id`、`requester_agent_id` 和 `origin_report_id`。
