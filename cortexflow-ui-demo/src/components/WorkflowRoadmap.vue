<script setup lang="ts">
import { Check, Circle, Compass, CornerUpLeft, Flag } from "lucide-vue-next";
import { useWorkflow } from "../composables/useWorkflow";
import { useWorkspace } from "../composables/useWorkspace";
import WorkflowSlotDetail from "./WorkflowSlotDetail.vue";
import "../workflow.css";
const {
  workflow,
  currentSlot,
  focusedSlot,
  expanded,
  kind,
  viewSlot,
  returnToCurrent,
} = useWorkflow();
const { project } = useWorkspace();
const labels = {
  completed: "已完成阶段",
  current: "当前阶段",
  next: "下一阶段",
  future: "远期方向",
};
</script>
<template>
  <section class="workflow-section" aria-label="Workspace 工作路线">
    <template v-if="workflow && currentSlot">
      <header class="workflow-header">
        <div>
          <div class="workflow-eyebrow">
            <Compass :size="14" /><span>工作路线</span
            ><span class="mock-inline">MOCK</span
            ><span class="workflow-workspace">{{ project.name }} · 跨对话</span>
          </div>
          <h2>{{ workflow.goal }}</h2>
        </div>
        <div class="workflow-current-indicator">
          <span class="tiny-dot running" />当前阶段<strong>{{
            currentSlot.code
          }}</strong>
        </div>
      </header>
      <div class="workflow-route" role="group" aria-label="阶段路线">
        <div
          v-for="(slot, index) in workflow.slots"
          :key="slot.id"
          class="workflow-step"
          :class="[
            `slot-${kind(slot)}`,
            {
              'is-current': kind(slot) === 'current',
              'is-viewed': focusedSlot?.id === slot.id && expanded,
            },
          ]"
          :data-slot-code="slot.code"
        >
          <button
            class="workflow-step-button"
            :aria-label="`查看阶段 ${slot.code}：${slot.title}`"
            :aria-expanded="focusedSlot?.id === slot.id && expanded"
            aria-controls="workflow-slot-detail"
            @click="viewSlot(slot.id)"
          >
            <div class="workflow-step-top">
              <span class="workflow-step-marker"
                ><Check v-if="slot.completed" :size="13" /><span
                  v-else-if="kind(slot) === 'current'"
                  class="current-slot-dot" /><Circle v-else :size="11" /></span
              ><span class="mono">{{ slot.code }}</span
              ><span class="workflow-step-state">{{ labels[kind(slot)] }}</span>
            </div>
            <h3>{{ slot.title }}</h3>
            <p>{{ slot.summary }}</p>
            <span v-if="kind(slot) === 'current'" class="workflow-step-meta"
              >{{ slot.taskIds.length }} 个任务 · 逐步推进与评价</span
            ><span v-else-if="slot.completed" class="workflow-step-meta">{{
              slot.outcomes[0] ?? "阶段结论已记录"
            }}</span
            ><span v-else class="workflow-step-meta">{{
              slot.taskIds.length
                ? `${slot.taskIds.length} 个已有任务`
                : "按需细化，不提前填满任务"
            }}</span></button
          ><span
            v-if="index < workflow.slots.length - 1"
            class="workflow-route-connector"
            aria-hidden="true"
          />
        </div>
      </div>
      <div class="workflow-focus-bar">
        <span
          ><Flag :size="12" />{{
            expanded
              ? `正在查看：${focusedSlot?.code} · ${focusedSlot?.title}`
              : "阶段详情已收起"
          }}<small v-if="focusedSlot?.id !== currentSlot.id"
            >查看焦点不会改变当前阶段</small
          ></span
        ><button
          v-if="focusedSlot?.id !== currentSlot.id || !expanded"
          class="text-button"
          @click="returnToCurrent"
        >
          <CornerUpLeft :size="12" />回到当前阶段</button
        ><span v-else class="workflow-focus-hint"
          >先看整体路线，再聚焦眼前工作</span
        >
      </div>
      <WorkflowSlotDetail v-if="expanded && focusedSlot" />
    </template>
    <div v-else class="workflow-no-plan">
      <Compass :size="23" />
      <div>
        <h2>这个工作空间还没有路线图</h2>
        <p>可在后续讨论中明确总体目标与第一个阶段。此 Demo 不自动生成规划。</p>
      </div>
      <span class="mock-inline">MOCK</span>
    </div>
  </section>
</template>
