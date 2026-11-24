import { computed, ref, watch } from "vue";
import {
  mockAgents,
  mockProjects,
  mockTasks,
  MOCK_SNAPSHOT,
} from "../data/mock";
import { mockWorkflows } from "../data/mockWorkflows";
import type { Status, Task } from "../types";

const projects = ref(structuredClone(mockProjects));
const tasks = ref(structuredClone(mockTasks));
const workflows = ref(structuredClone(mockWorkflows));
const workflowFocus = ref<Record<string, string>>({});
const workflowExpanded = ref<Record<string, boolean>>({});
const projectId = ref("cortex");
const conversationId = ref("ui");
const selectedId = ref("CF-024");
const detailOpen = ref(false);
const agentFilter = ref("");
const statusFilter = ref<Status | "">("");
const search = ref("");
const sidebarOpen = ref(true);
const toast = ref("");
let toastTimer: ReturnType<typeof setTimeout>;
const project = computed(() =>
  projects.value.find((p) => p.id === projectId.value)!,
);
const conversation = computed(() =>
  project.value.conversations.find((c) => c.id === conversationId.value),
);
const conversationTasks = computed(() =>
  tasks.value.filter((t) => t.conversationId === conversationId.value),
);
const filteredTasks = computed(() =>
  conversationTasks.value
    .filter(
      (t) =>
        (!statusFilter.value || t.status === statusFilter.value) &&
        (!agentFilter.value || t.agentId === agentFilter.value) &&
        (!search.value ||
          `${t.title} ${t.id}`
            .toLowerCase()
            .includes(search.value.toLowerCase())),
    )
    .sort((a, b) => b.updated.localeCompare(a.updated)),
);
const selectedTask = computed(() =>
  filteredTasks.value.find((t) => t.id === selectedId.value),
);
const selectedAgent = computed(() =>
  mockAgents.find((a) => a.id === selectedTask.value?.agentId),
);
watch(filteredTasks, (visible) => {
  if (!visible.some((t) => t.id === selectedId.value)) {
    selectedId.value = visible[0]?.id ?? "";
    detailOpen.value = false;
  }
});
export function useWorkspace() {
  function notify(message: string) {
    toast.value = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.value = "";
    }, 3600);
  }
  function clearFilters() {
    agentFilter.value = "";
    statusFilter.value = "";
    search.value = "";
  }
  function selectConversation(pid: string, cid: string) {
    projectId.value = pid;
    conversationId.value = cid;
    clearFilters();
    detailOpen.value = false;
    selectedId.value =
      tasks.value.find((t) => t.conversationId === cid)?.id ?? "";
  }
  function selectProject(pid: string) {
    selectConversation(
      pid,
      projects.value.find((p) => p.id === pid)?.conversations[0]?.id ?? "",
    );
  }
  function selectTask(id: string) {
    selectedId.value = id;
    detailOpen.value = true;
  }
  function filterAgent(id: string) {
    agentFilter.value = agentFilter.value === id ? "" : id;
    detailOpen.value = false;
  }
  function create(kind: "project" | "conversation", name: string) {
    const id = `mock-${crypto.randomUUID()}`;
    if (kind === "project") {
      projects.value.push({
        id,
        name: name.trim(),
        initials: name.trim().slice(0, 1).toUpperCase(),
        description: "新建的 Mock 项目 · 仅在本次页面会话中保留",
        conversations: [],
      });
      selectProject(id);
    } else {
      project.value.conversations.push({
        id,
        title: name.trim(),
        description: "新建的 Mock 对话 · 尚未产生任务",
      });
      selectConversation(projectId.value, id);
    }
    notify(
      `已创建 Mock ${kind === "project" ? "项目" : "对话"}，刷新后恢复演示数据`,
    );
  }
  function reset() {
    projects.value = structuredClone(mockProjects);
    tasks.value = structuredClone(mockTasks);
    workflows.value = structuredClone(mockWorkflows);
    workflowFocus.value = {};
    workflowExpanded.value = {};
    selectConversation("cortex", "ui");
    notify("已恢复初始 Mock 数据");
  }
  function createMockTask(input: {
    title: string;
    description: string;
    agentId: string;
  }) {
    if (!conversation.value || !mockAgents.some((a) => a.id === input.agentId))
      return;
    const id = `MOCK-${crypto.randomUUID().slice(0, 8).toUpperCase()}`;
    const time = MOCK_SNAPSHOT.replace(" · ", " ");
    const task: Task = {
      id,
      conversationId: conversationId.value,
      title: input.title.trim(),
      description: input.description.trim(),
      agentId: input.agentId,
      status: "waiting",
      created: time,
      updated: time,
      outcome: "本地 Mock 补充任务，尚未派发",
      report: { status: "none" },
      review: { status: "none", note: "尚未进入审核" },
      execution: {
        id: `EX-${id}`,
        stages: [
          "消息接收",
          "指令解析",
          "任务创建",
          "派发",
          "执行",
          "报告回传",
          "审核",
        ].map((name) => ({
          name,
          status: "waiting",
          time: "—",
          note: "仅在前端创建的 Mock 任务，尚未提交 Core。",
        })),
      },
    };
    tasks.value.push(task);
    return task;
  }
  return {
    tasks,
    workflows,
    workflowFocus,
    workflowExpanded,
    createMockTask,
    projects,
    agents: mockAgents,
    projectId,
    conversationId,
    project,
    conversation,
    conversationTasks,
    filteredTasks,
    selectedTask,
    selectedAgent,
    selectedId,
    detailOpen,
    agentFilter,
    statusFilter,
    search,
    sidebarOpen,
    toast,
    notify,
    clearFilters,
    selectConversation,
    selectProject,
    selectTask,
    filterAgent,
    create,
    reset,
  };
}
