/** Curated MOCK context, not a Git log, official phase definition or acceptance record.
 * Historical capability boundaries follow docs/04-architecture/implementation-status.md.
 * Unknown dates stay null. Other projects contain illustrative goals, not invented results.
 */
export type DevelopmentStatus =
  "completed" | "current" | "implemented" | "planned";
export interface DevelopmentNode {
  id: string;
  kind: "origin" | "stage" | "task" | "branch";
  title: string;
  description?: string;
  date: string | null;
  status: DevelopmentStatus;
  defaultExpanded?: boolean;
  childrenMode?: "sequence" | "parallel";
  children?: DevelopmentNode[];
}
export interface ProjectDevelopment {
  projectId: string;
  nodes: DevelopmentNode[];
}
function task(
  id: string,
  title: string,
  status: DevelopmentStatus,
  description?: string,
  date: string | null = null,
): DevelopmentNode {
  return { id, kind: "task", title, status, description, date };
}
function origin(projectId: string): DevelopmentNode {
  return {
    id: `${projectId}-origin`,
    kind: "origin",
    title: "项目建立",
    description: "确立项目方向。",
    date: null,
    status: "completed",
  };
}
export const projectDevelopment: ProjectDevelopment[] = [
  {
    projectId: "cortex",
    nodes: [
      origin("cortex"),
      {
        id: "cf-phase-a",
        kind: "stage",
        title: "阶段 A：基础通信设施",
        status: "implemented",
        date: null,
        description: "通信与会话绑定已有实现；真实页面通路待验证。",
        children: [
          task(
            "cf-supervisor",
            "Supervisor 会话连接",
            "implemented",
            "已有会话绑定与显式提交实现。",
          ),
          task(
            "cf-bridge",
            "Bridge 通信",
            "implemented",
            "已有 Bridge 和本地模拟验证，真实网页联通待确认。",
          ),
        ],
      },
      {
        id: "cf-phase-b",
        kind: "stage",
        title: "阶段 B：消息处理",
        status: "implemented",
        date: null,
        description: "持久化与解析已有实现，保留真实来源的验证边界。",
        children: [
          task(
            "cf-message-store",
            "消息持久化",
            "implemented",
            "接收、幂等与存储已有实现。",
          ),
          task(
            "cf-parser",
            "指令解析器",
            "implemented",
            "已有解析契约与 Provider 适配，真实链路仍需核实。",
          ),
        ],
      },
      {
        id: "cf-phase-c",
        kind: "stage",
        title: "阶段 C：Agent 执行闭环",
        status: "implemented",
        date: null,
        description: "运行、派发、追踪与报告已有实现；真实任务闭环待验证。",
        childrenMode: "parallel",
        children: [
          task(
            "cf-runtime",
            "Agent Runtime",
            "implemented",
            "已有多种 CLI 运行适配，终态证据与恢复能力各有边界。",
          ),
          task(
            "cf-delivery",
            "Task / Delivery",
            "implemented",
            "已有任务创建和显式派发，不等于自动多 Agent 调度。",
          ),
          task(
            "cf-tracking",
            "执行追踪",
            "implemented",
            "已有执行关联、状态观察和恢复对账。",
          ),
          {
            id: "cf-report-branch",
            kind: "branch",
            title: "报告回传与审核",
            status: "implemented",
            date: null,
            description: "本地模拟闭环已有覆盖，真实 ChatGPT 会话回传待验。",
            childrenMode: "parallel",
            children: [
              task(
                "cf-report",
                "报告回传",
                "implemented",
                "保留原 Supervisor 身份和报告记录。",
              ),
              task(
                "cf-review",
                "结构化审核",
                "implemented",
                "审核与执行状态分开记录。",
              ),
            ],
          },
        ],
      },
      {
        id: "cf-current",
        kind: "stage",
        title: "当前工作：CortexFlow 前端 Demo",
        status: "current",
        date: null,
        defaultExpanded: true,
        description: "从项目入口到上下文恢复，逐步收敛工作台体验。",
        children: [
          task(
            "cf-task-home",
            "项目首页设计",
            "completed",
            "项目列表与应用内标签页已交付。",
            "2026-11-24",
          ),
          task(
            "cf-task-overview",
            "项目概览设计",
            "current",
            "以开发主线呈现历史、当前工作与下一步方向。",
          ),
          task(
            "cf-task-interaction",
            "后续交互设计",
            "planned",
            "按需讨论具体 Task 页面与项目内部交互。",
          ),
        ],
      },
      {
        id: "cf-future",
        kind: "stage",
        title: "未来规划",
        status: "planned",
        date: null,
        defaultExpanded: true,
        childrenMode: "parallel",
        children: [
          task(
            "cf-desktop",
            "桌面应用集成",
            "planned",
            "确定独立桌面宿主与原生内容容器。",
          ),
          task(
            "cf-real-validation",
            "真实任务闭环验证",
            "planned",
            "验证实际消息、执行、报告与审核链路。",
          ),
          task(
            "cf-multi-agent",
            "多 Agent 协作",
            "planned",
            "在验证边界清晰后逐步细化协作方式。",
          ),
        ],
      },
    ],
  },
  {
    projectId: "loongarch",
    nodes: [
      origin("loongarch"),
      {
        id: "la-current",
        kind: "stage",
        title: "验证目标与实现边界",
        status: "current",
        date: null,
        defaultExpanded: true,
        description: "先明确处理器设计范围与验证重点。",
        children: [
          task(
            "la-scope",
            "验证目标梳理",
            "current",
            "梳理流水线、异常和关键指令的覆盖边界。",
          ),
          task(
            "la-tests",
            "异常处理测试计划",
            "planned",
            "确定代表性测试场景与观察点。",
          ),
        ],
      },
      {
        id: "la-implementation",
        kind: "stage",
        title: "处理器实现与验证",
        status: "planned",
        date: null,
        defaultExpanded: true,
        childrenMode: "parallel",
        children: [
          task(
            "la-pipeline",
            "流水线验证",
            "planned",
            "逐步明确执行、转发与冲突处理的验证项。",
          ),
          task(
            "la-exceptions",
            "异常处理",
            "planned",
            "结合既定范围补充测试。",
          ),
        ],
      },
      {
        id: "la-delivery",
        kind: "stage",
        title: "系统联调与结果整理",
        status: "planned",
        date: null,
        description: "后续根据验证结果继续细化。",
      },
    ],
  },
  {
    projectId: "uxuescript",
    nodes: [
      origin("uxuescript"),
      {
        id: "ux-current",
        kind: "stage",
        title: "课程加载与运行稳定性",
        status: "current",
        date: null,
        defaultExpanded: true,
        children: [
          task(
            "ux-loading",
            "课程列表加载逻辑",
            "current",
            "明确加载与异常恢复的交互边界。",
          ),
        ],
      },
      {
        id: "ux-next",
        kind: "stage",
        title: "回归验证与分发",
        status: "planned",
        date: null,
        description: "按实际改动确定检查范围。",
      },
    ],
  },
  {
    projectId: "nuwanda-space",
    nodes: [
      origin("nuwanda-space"),
      {
        id: "site-current",
        kind: "stage",
        title: "文章呈现",
        status: "current",
        date: null,
        defaultExpanded: true,
        children: [
          task(
            "site-articles",
            "首页文章列表",
            "current",
            "梳理阅读入口与文字层级。",
          ),
        ],
      },
      {
        id: "site-next",
        kind: "stage",
        title: "内容组织与发布体验",
        status: "planned",
        date: null,
        description: "围绕具体阅读场景继续细化。",
      },
    ],
  },
  {
    projectId: "writing-reading",
    nodes: [
      origin("writing-reading"),
      {
        id: "wr-current",
        kind: "stage",
        title: "阅读与写作整理",
        status: "current",
        date: null,
        defaultExpanded: true,
        children: [
          task("wr-reading", "梳理阅读主题", "current"),
          task("wr-outline", "沉淀写作大纲", "planned"),
        ],
      },
      {
        id: "wr-next",
        kind: "stage",
        title: "持续积累与回顾",
        status: "planned",
        date: null,
      },
    ],
  },
];
