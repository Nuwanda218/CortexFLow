import { homeProjects } from "./homeProjects";
import {
  projectDevelopment,
  type DevelopmentNode,
  type DevelopmentStatus,
} from "./projectDevelopment";

/** One linked, fictional workspace snapshot. No record here is a real dispatch or acceptance. */
export type WorkspaceLevel = "project" | "stage" | "task" | "execution";
export interface WorkspaceFocus {
  level: WorkspaceLevel;
  id: string;
}
export type ActivityStatus = DevelopmentStatus | "failed" | "unknown";
export interface WorkspaceProject {
  id: string;
  title: string;
  description: string;
  stageIds: string[];
  origin: DevelopmentNode;
}
export interface WorkspaceStage {
  id: string;
  projectId: string;
  title: string;
  goal: string;
  status: DevelopmentStatus;
  updatedAt: string | null;
  taskIds: string[];
  defaultExpanded: boolean;
}
export interface WorkTask {
  id: string;
  stageId: string;
  title: string;
  description: string;
  status: DevelopmentStatus;
  date: string | null;
  contextMarkdown: string;
  executionIds: string[];
}
export interface RecordedExecution {
  id: string;
  taskId: string;
  title: string;
  agent: string;
  status: ActivityStatus;
  startedAt: string | null;
  endedAt: string | null;
  prompt: string;
  steps: { time: string; title: string; detail?: string }[] | null;
  reportMarkdown: string | null;
  references: { kind: "file" | "commit"; label: string }[];
}
export interface DevelopmentIndexNode extends WorkspaceFocus {
  title: string;
  status: ActivityStatus;
  children: DevelopmentIndexNode[];
}

export const workspaceProjects: WorkspaceProject[] = [];
export const workspaceStages: WorkspaceStage[] = [];
export const workTasks: WorkTask[] = [];
export const recordedExecutions: RecordedExecution[] = [];

// Reuse the previous curated history as seed material, then derive all pages from these entities.
for (const source of projectDevelopment) {
  const project = homeProjects.find((p) => p.id === source.projectId)!;
  const stages = source.nodes.filter((n) => n.kind === "stage");
  workspaceProjects.push({
    id: project.id,
    title: project.name,
    description: project.description,
    stageIds: stages.map((s) => s.id),
    origin: source.nodes.find((n) => n.kind === "origin")!,
  });
  for (const stage of stages) {
    const children = stage.children ?? [];
    workspaceStages.push({
      id: stage.id,
      projectId: project.id,
      title: stage.title,
      goal: stage.description ?? "按项目方向逐步明确本阶段的工作。",
      status: stage.status,
      updatedAt: null,
      taskIds: children.map((t) => t.id),
      defaultExpanded: !!stage.defaultExpanded,
    });
    for (const task of children) {
      workTasks.push({
        id: task.id,
        stageId: stage.id,
        title: task.title,
        description: task.description ?? task.title,
        status: task.status,
        date: task.date,
        executionIds: [],
        contextMarkdown: `## 工作目标\n\n${task.description ?? task.title}\n\n## 当前记录\n\n${task.status === "implemented" ? "已有能力实现记录；真实链路验证边界仍需保留。" : "按已整理的项目上下文继续推进。"}\n\n${task.children?.length ? "### 组成工作\n\n" + task.children.map((c) => "- " + c.title).join("\n") : "后续根据实际工作补充问题与结论。"}`,
      });
    }
  }
}
const current = workspaceStages.find((s) => s.id === "cf-current")!;
current.title = "当前阶段：项目工作空间设计";
current.goal =
  "建立 Project、Stage、Task、Execution 四级上下文，支持逐层深入与随时回到上层。";
current.updatedAt = "2026-11-24";
current.taskIds = [
  "cf-task-home",
  "cf-task-overview",
  "cf-task-layer-context",
  "cf-task-pages",
  "cf-task-executions",
  "cf-task-context-worker",
];
const future = workspaceStages.find((s) => s.id === "cf-future")!;
future.title = "后续规划：工程知识服务集成";
future.goal =
  "在工作空间的信息结构明确后，再讨论工程知识与上下文维护的服务边界。";
future.taskIds = [];
// Replace only the current phase's old task seeds, retaining the historical capability boundaries.
for (let i = workTasks.length - 1; i >= 0; i--)
  if (
    workTasks[i]!.stageId === current.id ||
    workTasks[i]!.stageId === future.id
  )
    workTasks.splice(i, 1);
const currentTasks: WorkTask[] = [
  {
    id: "cf-task-home",
    stageId: current.id,
    title: "项目首页设计",
    description: "项目列表和应用内标签页。",
    status: "completed",
    date: "2026-11-24",
    executionIds: [],
    contextMarkdown:
      "## 结果\n\n项目首页采用纵向列表，显示名称、概况、最近 Task 和日期。\n\n- 首页固定保留。\n- 项目标签去重打开，独立保存页面状态。\n\n## 后续维护\n\n保持入口精简，不把首页扩展成统计后台。",
  },
  {
    id: "cf-task-overview",
    stageId: current.id,
    title: "项目概览设计",
    description: "用纵向开发树恢复项目上下文。",
    status: "completed",
    date: "2026-11-24",
    executionIds: [],
    contextMarkdown:
      "## 概览约定\n\n项目名称、一句话目标和纵向开发历史构成默认概览。历史阶段适当折叠，当前工作默认展开。\n\n## 保留边界\n\n未知日期不补造，已实现但待验证的能力不标成正式验收完成。",
  },
  {
    id: "cf-task-layer-context",
    stageId: current.id,
    title: "分层上下文设计",
    description: "整理四级实体和上下文边界。",
    status: "implemented",
    date: null,
    executionIds: [],
    contextMarkdown:
      "## 层级边界\n\nProject 表达长期目标，Stage 表达阶段工作，Task 表达一个独立问题，Execution 表达一次 Agent 工作记录。\n\n同一个 Task 可以有多次实施，实施结束不自动完成产品 Task。\n\n## 待确认\n\n继续通过实际浏览验证导航密度。",
  },
  {
    id: "cf-task-pages",
    stageId: current.id,
    title: "阶段与任务页面设计",
    description: "验证统一索引、四级页面及导航的连贯性。",
    status: "current",
    date: null,
    executionIds: [
      "exec-pages-research",
      "exec-pages-prototype",
      "exec-pages-review",
    ],
    contextMarkdown:
      '## 任务目标与需求\n\n让用户在同一个项目标签中，从项目整体深入到某次实施，并随时恢复上层视角。\n\n- 全局标签只负责切换项目。\n- 面包屑直接返回任意上级。\n- 各项目保留当前层次、展开状态和滚动位置。\n\n## 已确定的设计\n\n使用同一棵开发树驱动 `DevelopmentNavigator`。深入时收缩上层，保留直接上级、同层邻居和直接下层。浏览焦点和实际开发进度分别表达。\n\n```ts\ntype WorkspaceLevel = "project" | "stage" | "task" | "execution";\n```\n\n## 当前进展\n\n已经整理关联模型和页面原型。下方多次实施分别记录调研、原型和复核，不将它们合并成一个产品 Task。\n\n## 遗留问题与下一步\n\n1. 继续体验动态索引在不同尺度下的信息密度。\n2. 根据反馈调整长文档和窄窗口的阅读方式。\n3. 本轮不接真实 Agent 派发与上下文服务。',
  },
  {
    id: "cf-task-executions",
    stageId: current.id,
    title: "实施记录设计",
    description: "保存委托、明确过程与原始报告。",
    status: "planned",
    date: null,
    executionIds: [],
    contextMarkdown:
      "## 下一步\n\n明确每次实施的记录边界。原始委托与报告应保留；缺失过程标为未记录。\n\n不根据报告反推不存在的工具步骤。",
  },
  {
    id: "cf-task-context-worker",
    stageId: current.id,
    title: "上下文维护机制集成",
    description: "后续定义 Context Worker 的维护职责。",
    status: "planned",
    date: null,
    executionIds: [],
    contextMarkdown:
      "## 目标\n\n后续讨论独立 Context Worker 如何维护任务上下文。\n\n## 当前边界\n\n本轮仅使用 Mock Markdown，不实现自动总结、真实服务或后台任务。",
  },
];
workTasks.push(...currentTasks);
recordedExecutions.push(
  {
    id: "exec-pages-research",
    taskId: "cf-task-pages",
    title: "现有能力调研",
    agent: "Claude CLI",
    status: "completed",
    startedAt: "2026-11-24 09:00",
    endedAt: "2026-11-24 09:25",
    prompt:
      "【Mock 工作委托】\n检查现有项目首页、标签状态和开发树，提出最小的四级导航方案。保留原组件，不接真实后端。",
    steps: [
      {
        time: "09:05",
        title: "阅读页面与状态模块",
        detail: "记录项目标签和树状态的复用边界。",
      },
      {
        time: "09:20",
        title: "整理实体归属",
        detail: "区分产品 Work Task 与单次实施。",
      },
    ],
    reportMarkdown:
      "# Mock 调研报告\n\n## 结论\n\n采用单一关联模型，并在项目标签内部切换四级页面。\n\n- 项目之间保持独立状态。\n- 实施记录通过 `taskId` 归属任务。\n- 过程缺失时不补造记录。",
    references: [],
  },
  {
    id: "exec-pages-prototype",
    taskId: "cf-task-pages",
    title: "阶段页面原型实现",
    agent: "Codex CLI",
    status: "completed",
    startedAt: "2026-11-24 10:00",
    endedAt: "2026-11-24 10:40",
    prompt:
      "【Mock 工作委托】\n实现 Stage、Task 和 Execution 的最小浏览版本。\n\n要求：\n1. 使用统一 DevelopmentNavigator。\n2. 名称点击与展开操作分开。\n3. 复用项目标签，不创建层级标签。\n4. 保留 Markdown、原始委托和明确记录的过程。\n5. 所有数据为 Mock，不派发真实任务。",
    steps: [
      {
        time: "10:05",
        title: "读取组件与数据",
        detail: "工具：文件读取。核对现有项目外壳和 Mock 结构。",
      },
      {
        time: "10:15",
        title: "调整关联模型",
        detail: "工具：文件编辑。将阶段、任务和实施记录分开建模。",
      },
      {
        time: "10:30",
        title: "检查导航原型",
        detail: "工具：浏览器。检查面包屑和同层切换。",
      },
      {
        time: "10:38",
        title: "记录验证结果",
        detail: "工具：构建与交互检查。此条仅为 Mock 过程记录。",
      },
    ],
    reportMarkdown:
      "# Mock 执行报告\n\n## 实现结果\n\n完成四级原型的数据关联与浏览入口示例。\n\n- Stage 只展示目标和 Task 开发树。\n- Task 展示可维护的 Markdown 上下文。\n- Execution 保留独立委托、过程和报告。\n\n### 相关文件\n\n`src/components/DevelopmentNavigator.vue`\n\n`src/components/ExecutionOverview.vue`\n\n## 验证记录\n\n```sh\nnpm run build\nnpm test\n```\n\n此处是模拟 Agent 的原始汇报内容，**不是本仓库真实测试或验收结论**。实际检查结果以 Demo 的验证文档为准。\n\n> 缺失的过程不会由报告自动补全。",
    references: [
      {
        kind: "file",
        label: "src/components/DevelopmentNavigator.vue（示例引用）",
      },
      {
        kind: "file",
        label: "src/components/ExecutionOverview.vue（示例引用）",
      },
      { kind: "commit", label: "Mock 提交：workspace-navigation-sample" },
    ],
  },
  {
    id: "exec-pages-review",
    taskId: "cf-task-pages",
    title: "记录复核",
    agent: "Claude CLI",
    status: "unknown",
    startedAt: "2026-11-24 11:00",
    endedAt: null,
    prompt: "【Mock 工作委托】\n复核阶段、任务和实施记录的归属与页面一致性。",
    steps: null,
    reportMarkdown:
      "# Mock 复核报告\n\n## 已记录意见\n\nTask 与 Execution 的实体边界应保留。可继续调整索引的信息密度。\n\n## 记录限制\n\n工具过程未保存，最终执行状态也未确认。报告存在不能用于推测缺失的执行步骤或终态。",
    references: [],
  },
);

export function projectById(id: string) {
  return workspaceProjects.find((p) => p.id === id);
}
export function stageById(id: string) {
  return workspaceStages.find((s) => s.id === id);
}
export function taskById(id: string) {
  return workTasks.find((t) => t.id === id);
}
export function executionById(id: string) {
  return recordedExecutions.find((e) => e.id === id);
}
export function stageTasks(id: string) {
  return (stageById(id)?.taskIds ?? []).flatMap((id) => {
    const t = taskById(id);
    return t ? [t] : [];
  });
}
export function taskExecutions(id: string) {
  return (taskById(id)?.executionIds ?? []).flatMap((id) => {
    const e = executionById(id);
    return e ? [e] : [];
  });
}
export function focusPath(projectId: string, focus: WorkspaceFocus) {
  const root = `/project/${encodeURIComponent(projectId)}`;
  if (focus.level === "project") return root;
  const execution =
    focus.level === "execution" ? executionById(focus.id) : undefined;
  const task = execution
    ? taskById(execution.taskId)
    : focus.level === "task"
      ? taskById(focus.id)
      : undefined;
  const stage = task ? stageById(task.stageId) : stageById(focus.id);
  if (!stage || stage.projectId !== projectId) return root;
  const stagePath = `${root}/stage/${stage.id}`;
  return task
    ? `${stagePath}/task/${task.id}${execution ? `/execution/${execution.id}` : ""}`
    : stagePath;
}
export function indexTree(projectId: string): DevelopmentIndexNode | undefined {
  const project = projectById(projectId);
  if (!project) return;
  return {
    level: "project",
    id: project.id,
    title: project.title,
    status: "current",
    children: project.stageIds.flatMap((id) => {
      const stage = stageById(id);
      if (!stage) return [];
      return [
        {
          level: "stage" as const,
          id: stage.id,
          title: stage.title,
          status: stage.status,
          children: stageTasks(id).map((task) => ({
            level: "task" as const,
            id: task.id,
            title: task.title,
            status: task.status,
            children: taskExecutions(task.id).map((execution) => ({
              level: "execution" as const,
              id: execution.id,
              title: execution.title,
              status: execution.status,
              children: [],
            })),
          })),
        },
      ];
    }),
  };
}
export function taskTree(id: string): DevelopmentNode[] {
  return stageTasks(id).map((t) => ({
    id: t.id,
    kind: "task",
    title: t.title,
    description: t.description,
    date: t.date,
    status: t.status,
    target: { level: "task", id: t.id },
  }));
}
export function projectTree(projectId: string): DevelopmentNode[] {
  const project = projectById(projectId);
  if (!project) return [];
  return [
    project.origin,
    ...project.stageIds.flatMap((id) => {
      const stage = stageById(id);
      if (!stage) return [];
      return [
        {
          id: stage.id,
          kind: "stage" as const,
          title: stage.title,
          description: stage.goal,
          date: stage.updatedAt,
          status: stage.status,
          defaultExpanded: stage.defaultExpanded,
          target: { level: "stage" as const, id: stage.id },
          children: taskTree(stage.id),
        },
      ];
    }),
  ];
}
