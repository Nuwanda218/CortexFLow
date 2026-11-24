<script setup lang="ts">
import { computed, ref } from "vue";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  CheckCheck,
  ChevronUp,
  FileCheck2,
  ListTodo,
  Pencil,
  Plus,
  Target,
} from "lucide-vue-next";
import { useWorkflow } from "../composables/useWorkflow";
import { useWorkspace } from "../composables/useWorkspace";
import StatusBadge from "./StatusBadge.vue";
import WorkflowPlanDialog from "./WorkflowPlanDialog.vue";
import WorkflowTaskDialog from "./WorkflowTaskDialog.vue";
const {
  focusedSlot: slot,
  currentSlot,
  slotTasks,
  kind,
  collapse,
  openTask,
  canMove,
  movePlan,
} = useWorkflow();
const { agents, project } = useWorkspace();
const editing = ref(false),
  adding = ref(false);
const completedCount = computed(
  () => slotTasks.value.filter((t) => t.status === "completed").length,
);
const isFuture = computed(
  () => !!slot.value && ["next", "future"].includes(kind(slot.value)),
);
const labels = {
  completed: "阶段结论已确认 · Mock",
  current: "当前阶段",
  next: "近期计划",
  future: "概括方向",
};
</script>
<template>
  <section
    v-if="slot"
    id="workflow-slot-detail"
    class="workflow-slot-detail"
    :aria-label="`阶段详情 ${slot.code}`"
  >
    <header class="workflow-detail-header">
      <div>
        <span class="workflow-slot-code mono">{{ slot.code }}</span>
        <h3>{{ slot.title }}</h3>
        <span class="workflow-detail-state" :class="kind(slot)">{{
          labels[kind(slot)]
        }}</span>
      </div>
      <div class="workflow-detail-actions">
        <template v-if="isFuture"
          ><button
            class="icon-button small"
            aria-label="前移规划阶段"
            title="仅调整未开始阶段的顺序"
            :disabled="!canMove(-1)"
            @click="movePlan(-1)"
          >
            <ArrowUp :size="13" /></button
          ><button
            class="icon-button small"
            aria-label="后移规划阶段"
            title="仅调整未开始阶段的顺序"
            :disabled="!canMove(1)"
            @click="movePlan(1)"
          >
            <ArrowDown :size="13" /></button
          ><button class="text-button" @click="editing = true">
            <Pencil :size="12" />编辑阶段计划
          </button></template
        ><button
          class="icon-button small"
          aria-label="收起阶段详情"
          @click="collapse"
        >
          <ChevronUp :size="15" />
        </button>
      </div>
    </header>
    <div class="workflow-detail-grid">
      <div class="workflow-slot-context">
        <h4><Target :size="13" />阶段目标</h4>
        <p>{{ slot.goal }}</p>
        <template v-if="slot.completed"
          ><h4><FileCheck2 :size="13" />阶段成果</h4>
          <ul class="workflow-outcomes">
            <li v-for="outcome in slot.outcomes" :key="outcome">
              <CheckCheck :size="12" />{{ outcome }}
            </li>
          </ul>
          <p class="workflow-conclusion">{{ slot.summary }}</p></template
        ><template v-else-if="slot.plan.length"
          ><h4>推进计划</h4>
          <ul>
            <li v-for="item in slot.plan" :key="item">{{ item }}</li>
          </ul></template
        ><template v-if="slot.constraints.length"
          ><h4>关键约束</h4>
          <ul class="workflow-constraints">
            <li v-for="item in slot.constraints" :key="item">{{ item }}</li>
          </ul></template
        >
      </div>
      <div class="workflow-slot-work">
        <div class="workflow-tasks-heading">
          <h4>
            <ListTodo :size="13" />阶段任务<span v-if="slotTasks.length">{{
              slotTasks.length
            }}</span>
          </h4>
          <span v-if="slotTasks.length" class="workflow-task-progress"
            >{{ completedCount }} / {{ slotTasks.length }} 执行已完成</span
          ><button
            v-if="slot.id === currentSlot?.id"
            class="text-button"
            @click="adding = true"
          >
            <Plus :size="12" />新增补充任务
          </button>
        </div>
        <div
          v-if="slotTasks.length"
          class="workflow-task-list"
          tabindex="0"
          aria-label="阶段关联任务"
        >
          <button
            v-for="task in slotTasks"
            :key="task.id"
            class="workflow-task"
            :data-task-id="task.id"
            :data-status="task.status"
            :aria-label="`查看阶段任务 ${task.id}`"
            :title="`${task.title} · ${task.outcome}`"
            @click="openTask(task.id)"
          >
            <span class="workflow-task-main"
              ><span class="workflow-task-title">{{ task.title }}</span
              ><small
                ><span class="mono">{{ task.id }}</span
                ><span>{{
                  agents.find((a) => a.id === task.agentId)?.name
                }}</span
                ><span>{{
                  project.conversations.find(
                    (c) => c.id === task.conversationId,
                  )?.title
                }}</span></small
              ><span v-if="slot.completed" class="workflow-task-result">{{
                task.outcome
              }}</span></span
            ><StatusBadge :status="task.status" /><ArrowUpRight :size="13" />
          </button>
        </div>
        <div v-else class="workflow-unplanned">
          <ListTodo :size="22" />
          <p>
            {{
              slot.completed
                ? "阶段结论已记录，暂无关联执行条目。"
                : "具体任务将在进入该阶段后确定。"
            }}
          </p>
          <span>{{
            slot.completed
              ? "结论与执行条目分开保留"
              : "保留方向，接近当前阶段时再逐步细化"
          }}</span>
        </div>
        <p class="workflow-evaluation-note">
          {{
            slot.completed
              ? "已执行的任务与汇报保持可追溯。"
              : "Task 执行结束不代表 Slot 完成；阶段目标由用户与 Coordinator 共同评价。"
          }}
        </p>
      </div>
    </div>
    <WorkflowPlanDialog
      v-if="editing"
      @close="editing = false"
    /><WorkflowTaskDialog v-if="adding" @close="adding = false" />
  </section>
</template>
