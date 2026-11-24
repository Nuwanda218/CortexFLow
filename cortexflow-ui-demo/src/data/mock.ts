import type { Agent, Execution, Project, Status, Task } from "../types";

/** Fixed, fictional snapshots. No connection to the real Core or CLI processes. */
export const MOCK_SNAPSHOT = "2026-11-22 · 14:32";
export const mockAgents: Agent[] = [
  {
    id: "codex",
    name: "Codex CLI",
    subtitle: "代码实现与验证",
    configured: true,
    version: "演示配置 · 本地 CLI",
  },
  {
    id: "claude",
    name: "Claude CLI",
    subtitle: "分析与方案设计",
    configured: true,
    version: "演示配置 · 本地 CLI",
  },
  {
    id: "dsh",
    name: "dsh",
    subtitle: "研究与资料整理",
    configured: false,
    version: "未配置 · 演示记录仍可查看",
  },
];
export const mockProjects: Project[] = [
  {
    id: "cortex",
    name: "CortexFlow",
    initials: "C",
    description: "让工具承担协调，让人专注思考。",
    conversations: [
      {
        id: "ui",
        title: "工作台界面设计",
        description: "桌面工作台的布局、状态与交互验证",
      },
      {
        id: "adapters",
        title: "Agent 接入方案",
        description: "探索不同 CLI 的适配与报告约定",
      },
      {
        id: "ideas",
        title: "下一步想法",
        description: "从一个想法开始新的协作",
      },
    ],
  },
  {
    id: "weihua",
    name: "卫画助手",
    initials: "W",
    description: "把传统文化的知识，带入日常对话。",
    conversations: [
      {
        id: "retrieval",
        title: "知识库检索优化",
        description: "检索质量与来源可追溯性",
      },
      {
        id: "content",
        title: "内容与体验探索",
        description: "梳理年画知识的呈现方式",
      },
    ],
  },
];
const names = [
  "消息接收",
  "指令解析",
  "任务创建",
  "派发",
  "执行",
  "报告回传",
  "审核",
];
function execution(
  id: string,
  states: Status[],
  notes: Record<number, string> = {},
): Execution {
  return {
    id,
    stages: names.map((name, i) => ({
      name,
      status: states[i] ?? "waiting",
      time:
        states[i] === "completed"
          ? `14:${String(12 + i * 2).padStart(2, "0")}`
          : states[i] === "waiting"
            ? "—"
            : "14:32",
      note:
        notes[i] ??
        (states[i] === "completed"
          ? `${name}已完成（Mock 快照）`
          : states[i] === "running"
            ? `${name}处理中（Mock 快照）`
            : "等待前序阶段完成"),
    })),
  };
}
const done: Status[] = Array(7).fill("completed");
const active: Status[] = [
  "completed",
  "completed",
  "completed",
  "completed",
  "running",
  "waiting",
  "waiting",
];
const queued: Status[] = [
  "completed",
  "completed",
  "completed",
  "waiting",
  "waiting",
  "waiting",
  "waiting",
];
const taskSnapshots: Task[] = [
  {
    id: "CF-024",
    conversationId: "ui",
    title: "搭建工作台应用外壳",
    description:
      "实现统一导航、项目与对话切换，建立独立的聊天容器与 Agent 工作区。",
    agentId: "codex",
    status: "running",
    created: "2026-11-22 14:12",
    updated: "2026-11-22 14:32",
    outcome: "正在实现项目与对话导航",
    report: { status: "pending" },
    review: { status: "pending", note: "等待执行完成并回传报告" },
    execution: execution("EX-024-01", active, {
      4: "应用外壳与项目导航实现中；此为固定 Mock 快照，不会真实执行。",
    }),
  },
  {
    id: "CF-023",
    conversationId: "ui",
    title: "梳理任务详情的信息层级",
    description:
      "围绕任务来源、执行阶段、报告与审核，建立可快速浏览的任务详情结构。",
    agentId: "claude",
    status: "completed",
    created: "2026-11-22 13:40",
    updated: "2026-11-22 14:24",
    outcome: "详情方案已完成并通过审核",
    report: {
      status: "available",
      title: "任务详情信息结构",
      body: "Mock 设计报告\n\n1. 概览：任务目标、目标 Agent、来源对话和当前状态。\n2. 执行：以七个阶段呈现任务经过，保留每个阶段的状态与关键事件。\n3. 交付：将报告生成与审核结论分别表达，避免执行完成被理解为审核通过。\n\n演示结论：采用右侧详情面板，保持任务列表上下文。\n\n此报告完全为前端演示数据，不代表真实 Agent 产物。",
    },
    review: {
      status: "approved",
      note: "Mock 审核：信息层级清晰，可以进入视觉设计。",
    },
    execution: execution("EX-023-01", done),
  },
  {
    id: "CF-022",
    conversationId: "ui",
    title: "整理桌面端交互参考",
    description: "汇总导航、空状态与紧凑任务列表的交互参考。",
    agentId: "dsh",
    status: "waiting",
    created: "2026-11-22 13:35",
    updated: "2026-11-22 14:16",
    outcome: "等待目标 Agent 配置",
    report: { status: "none" },
    review: { status: "none", note: "尚未进入审核" },
    execution: execution("EX-022-01", queued, {
      3: "目标 dsh 未配置，尚未派发。",
    }),
  },
  {
    id: "CF-021",
    conversationId: "ui",
    title: "验证报告回传格式",
    description: "检查示例报告是否包含摘要、产物和验证说明。",
    agentId: "codex",
    status: "failed",
    created: "2026-11-22 13:20",
    updated: "2026-11-22 14:22",
    outcome: "报告格式校验失败",
    report: { status: "none" },
    review: { status: "none", note: "报告校验未通过，未进入审核" },
    execution: execution(
      "EX-021-01",
      [
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "failed",
        "waiting",
      ],
      {
        5: "Mock 报告缺少 artifacts 字段。请检查报告结构后再派发；本 Demo 不执行重试。",
      },
    ),
  },
  {
    id: "CF-020",
    conversationId: "ui",
    title: "同步 Agent 执行快照",
    description: "展示未获取到最终执行结果时的状态表达。",
    agentId: "claude",
    status: "unknown",
    created: "2026-11-22 13:05",
    updated: "2026-11-22 14:10",
    outcome: "无法确认执行结果",
    report: { status: "pending" },
    review: { status: "none", note: "结果未知，无法开始审核" },
    execution: execution(
      "EX-020-01",
      [
        "completed",
        "completed",
        "completed",
        "completed",
        "unknown",
        "unknown",
        "waiting",
      ],
      {
        4: "未收到最终状态。未知不代表失败，也不能确认任务仍在执行。",
        5: "报告是否生成尚未确认。",
      },
    ),
  },
  {
    id: "CF-019",
    conversationId: "ui",
    title: "检查窄窗口的布局适配",
    description: "检查侧栏收起、列表滚动和详情抽屉的可用性。",
    agentId: "codex",
    status: "completed",
    created: "2026-11-22 12:45",
    updated: "2026-11-22 14:24",
    outcome: "执行已完成，等待审核",
    report: {
      status: "available",
      title: "窄窗口布局检查",
      body: "Mock 报告\n\n建议小窗口使用浮层详情，主任务列表保持可滚动。执行已完成，审核阶段仍等待人工确认。",
    },
    review: { status: "pending", note: "执行完成不等同于审核通过。" },
    execution: execution("EX-019-01", [...done.slice(0, 6), "waiting"]),
  },
  {
    id: "CF-018",
    conversationId: "adapters",
    title: "验证 dsh 适配器",
    description: "梳理 dsh 的接入配置与任务派发约定。",
    agentId: "dsh",
    status: "waiting",
    created: "2026-11-22 13:10",
    updated: "2026-11-22 14:16",
    outcome: "等待配置",
    report: { status: "none" },
    review: { status: "none", note: "尚未进入审核" },
    execution: execution("EX-018-01", queued),
  },
  {
    id: "CF-017",
    conversationId: "adapters",
    title: "定义 CLI 报告契约",
    description: "整理结构化报告的必要字段。",
    agentId: "claude",
    status: "completed",
    created: "2026-11-22 12:30",
    updated: "2026-11-22 14:24",
    outcome: "契约草案已审核",
    report: {
      status: "available",
      title: "报告契约草案",
      body: "Mock 报告\n\n建议字段：summary、artifacts、validation。此示例不是 Core 的正式接口协议。",
    },
    review: { status: "approved", note: "Mock 审核：草案通过。" },
    execution: execution("EX-017-01", done),
  },
  {
    id: "WH-008",
    conversationId: "retrieval",
    title: "整理年画知识的引用来源",
    description: "检查演示检索结果的引用表达。",
    agentId: "claude",
    status: "running",
    created: "2026-11-22 13:30",
    updated: "2026-11-22 14:32",
    outcome: "整理来源中",
    report: { status: "pending" },
    review: { status: "pending", note: "等待报告" },
    execution: execution("EX-008-01", active),
  },
  {
    id: "WH-007",
    conversationId: "retrieval",
    title: "实现检索结果卡片",
    description: "为知识条目建立统一的结果布局。",
    agentId: "codex",
    status: "completed",
    created: "2026-11-22 12:30",
    updated: "2026-11-22 14:24",
    outcome: "卡片布局已审核",
    report: {
      status: "available",
      title: "检索卡片布局",
      body: "Mock 报告\n\n卡片包含标题、内容摘要和来源。所有内容仅供布局讨论。",
    },
    review: { status: "approved", note: "Mock 审核通过。" },
    execution: execution("EX-007-01", done),
  },
];

// Derive coherent event times inside each snapshot's creation/update interval.
// Waiting stages have no event timestamp; unknown remains unknown.
export const mockTasks: Task[] = taskSnapshots.map((task) => {
  const start = new Date(
    task.created.replace(" ", "T") + ":00+08:00",
  ).getTime();
  const end = new Date(task.updated.replace(" ", "T") + ":00+08:00").getTime();
  const observed = task.execution.stages.filter(
    (stage) => stage.status !== "waiting",
  );
  observed.forEach((stage, index) => {
    const instant =
      start + ((end - start) * index) / Math.max(observed.length - 1, 1);
    stage.time = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Shanghai",
    }).format(instant);
  });
  return task;
});
