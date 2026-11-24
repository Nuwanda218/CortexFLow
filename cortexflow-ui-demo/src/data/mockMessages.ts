import { mockTasks } from "./mock";
import type { CollaborationMessage, ParticipantId, Task } from "../types";

// Fictional communication records, derived only from explicit Mock stage snapshots.
// A missing result never creates a pretend return message or Agent handoff.
function messagesForTask(task: Task): CollaborationMessage[] {
  const records: CollaborationMessage[] = [];
  const agent = task.agentId as ParticipantId;
  function add(
    from: ParticipantId,
    to: ParticipantId,
    stage: number,
    label: string,
    body: string,
    kind: CollaborationMessage["kind"] = "message",
    status: CollaborationMessage["status"] = "completed",
  ) {
    records.push({
      id: `${task.id}-message-${records.length + 1}`,
      taskId: task.id,
      conversationId: task.conversationId,
      from,
      to,
      time:
        task.execution.stages[stage]!.time === "—"
          ? task.updated.slice(11)
          : task.execution.stages[stage]!.time,
      order: records.length,
      label,
      body,
      kind,
      status,
    });
  }
  add(
    "supervisor",
    "core",
    0,
    "指令下发",
    `Supervisor 提交任务「${task.title}」。${task.description}`,
  );
  const dispatch = task.execution.stages[3]!;
  if (dispatch.status === "completed") {
    add(
      "core",
      agent,
      3,
      "任务派发",
      `Core 向目标 Agent 派发 ${task.id}，携带任务目标与来源对话。`,
    );
  } else {
    add(
      "core",
      agent,
      3,
      "待派发 · 未发送",
      "目标 Agent 尚未配置，消息仍在 Core 等待，未向 Agent 发送。",
      "pending",
      "waiting",
    );
  }
  const execution = task.execution.stages[4]!;
  if (execution.status === "running") {
    add(
      agent,
      "core",
      4,
      "进度回报",
      task.outcome + "。此为已记录的 Mock 进度消息，不代表实时连接。",
      "message",
      "running",
    );
    add(
      "core",
      "supervisor",
      4,
      "进度转交",
      `Core 将 ${task.id} 的阶段进度转交 Supervisor，等待最终报告。`,
      "message",
      "running",
    );
  } else if (execution.status === "unknown") {
    add(
      "core",
      "core",
      4,
      "状态未确认",
      "Core 未收到最终状态。此项为本地观测注记，不表示 Agent 发送过结果，也不表示执行失败。",
      "observation",
      "unknown",
    );
  }
  const report = task.execution.stages[5]!;
  if (report.status === "completed") {
    add(
      agent,
      "core",
      5,
      "报告回传",
      `Agent 提交 Mock 报告「${task.report.title ?? task.title}」，供 Core 记录与转交。`,
    );
    add(
      "core",
      "supervisor",
      5,
      "报告转交",
      "Core 转交报告摘要与产物信息，请 Supervisor 审核。执行完成与审核通过分别记录。",
    );
  } else if (report.status === "failed") {
    add(
      agent,
      "core",
      5,
      "报告校验失败",
      "已接收演示报告，但缺少 artifacts 字段，Core 校验未通过，未进入审核。",
      "message",
      "failed",
    );
  }
  if (task.review.status === "approved") {
    add("supervisor", "core", 6, "审核反馈", task.review.note);
  }
  return records;
}

export const mockMessages: CollaborationMessage[] = mockTasks
  .flatMap(messagesForTask)
  .sort((a, b) => a.time.localeCompare(b.time) || a.order - b.order);
