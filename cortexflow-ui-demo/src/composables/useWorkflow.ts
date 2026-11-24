import { computed } from "vue";
import { useWorkspace } from "./useWorkspace";
import type { SlotKind, WorkflowSlot } from "../types";

const store = useWorkspace();
const workflow = computed(() =>
  store.workflows.value.find((w) => w.workspaceId === store.projectId.value),
);
const currentSlot = computed(() =>
  workflow.value?.slots.find((s) => s.id === workflow.value?.currentSlotId),
);
const focusedSlot = computed(
  () =>
    workflow.value?.slots.find(
      (s) => s.id === store.workflowFocus.value[store.projectId.value],
    ) ?? currentSlot.value,
);
const expanded = computed(
  () => store.workflowExpanded.value[store.projectId.value] ?? true,
);
const slotTasks = computed(() =>
  (focusedSlot.value?.taskIds ?? []).flatMap((id) => {
    const task = store.tasks.value.find((t) => t.id === id);
    return task ? [task] : [];
  }),
);
function kind(slot: WorkflowSlot): SlotKind {
  if (slot.id === workflow.value?.currentSlotId) return "current";
  if (slot.completed) return "completed";
  const index = workflow.value!.slots.findIndex(
    (s) => s.id === workflow.value!.currentSlotId,
  );
  return workflow.value!.slots[index + 1]?.id === slot.id ? "next" : "future";
}
function viewSlot(id: string) {
  if (!workflow.value?.slots.some((s) => s.id === id)) return;
  const same = focusedSlot.value?.id === id;
  store.workflowFocus.value[store.projectId.value] = id;
  store.workflowExpanded.value[store.projectId.value] = same
    ? !expanded.value
    : true;
}
function collapse() {
  store.workflowExpanded.value[store.projectId.value] = false;
}
function returnToCurrent() {
  if (!currentSlot.value) return;
  store.workflowFocus.value[store.projectId.value] = currentSlot.value.id;
  store.workflowExpanded.value[store.projectId.value] = true;
}
function openTask(id: string) {
  if (!workflow.value?.slots.some((s) => s.taskIds.includes(id))) return;
  const task = store.tasks.value.find((t) => t.id === id);
  if (
    !task ||
    !store.project.value.conversations.some((c) => c.id === task.conversationId)
  )
    return;
  if (store.conversationId.value !== task.conversationId)
    store.selectConversation(store.projectId.value, task.conversationId);
  else store.clearFilters();
  store.selectTask(id);
}
function savePlan(input: { title: string; goal: string; plan: string }) {
  const slot = focusedSlot.value;
  if (!slot || !["next", "future"].includes(kind(slot))) return;
  slot.title = input.title.trim();
  slot.goal = input.goal.trim();
  slot.plan = input.plan
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  store.notify("已更新本地 Mock 阶段计划，执行记录保持保留");
}
function canMove(direction: number) {
  const w = workflow.value,
    slot = focusedSlot.value;
  if (!w || !slot || !["next", "future"].includes(kind(slot))) return false;
  const index = w.slots.indexOf(slot),
    target = w.slots[index + direction];
  return !!target && ["next", "future"].includes(kind(target));
}
function movePlan(direction: number) {
  if (!canMove(direction)) return;
  const slots = workflow.value!.slots,
    index = slots.indexOf(focusedSlot.value!);
  const slot = slots.splice(index, 1)[0]!;
  slots.splice(index + direction, 0, slot);
  store.notify("已调整后续规划顺序；当前阶段与已有任务未改变");
}
function addSupplement(input: {
  title: string;
  description: string;
  agentId: string;
}) {
  if (focusedSlot.value?.id !== currentSlot.value?.id) return;
  const task = store.createMockTask(input);
  if (!task) return;
  focusedSlot.value!.taskIds.push(task.id);
  openTask(task.id);
  store.notify("已创建新的 Mock 补充任务，尚未派发；原有执行记录保留");
}
export function useWorkflow() {
  return {
    workflow,
    currentSlot,
    focusedSlot,
    expanded,
    slotTasks,
    kind,
    viewSlot,
    collapse,
    returnToCurrent,
    openTask,
    savePlan,
    canMove,
    movePlan,
    addSupplement,
  };
}
