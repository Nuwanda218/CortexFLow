<script setup lang="ts">
import "../sequence.css";
import { computed, nextTick, ref, watch } from "vue";
import {
  ArrowDown,
  ArrowLeftRight,
  ArrowUpRight,
  Box,
  Check,
  CircleDashed,
  Command,
  MessageSquare,
  Sparkles,
  Terminal,
} from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
import { mockMessages } from "../data/mockMessages";
import type { CollaborationMessage, ParticipantId } from "../types";
import StatusBadge from "./StatusBadge.vue";

const {
  agents,
  selectedTask,
  agentFilter,
  filterAgent,
  filteredTasks,
  conversationTasks,
  selectTask,
} = useWorkspace();
const scope = ref<"conversation" | "task">("conversation");
const selectedMessageId = ref("");
const scroll = ref<HTMLElement>();
const participants = [
  {
    id: "supervisor",
    name: "ChatGPT",
    subtitle: "Supervisor · 未连接",
    icon: MessageSquare,
  },
  {
    id: "core",
    name: "CortexFlow Core",
    subtitle: "Mock · 消息路由",
    icon: Box,
  },
  ...agents.map((a) => ({
    id: a.id,
    name: a.name,
    subtitle: a.configured ? "已配置 · 在线未知" : "未配置",
    icon: a.id === "codex" ? Command : a.id === "claude" ? Sparkles : Terminal,
  })),
];
const visibleMessages = computed(() => {
  const ids = new Set(filteredTasks.value.map((t) => t.id));
  return mockMessages.filter(
    (m) =>
      ids.has(m.taskId) &&
      (scope.value === "conversation" || m.taskId === selectedTask.value?.id),
  );
});
const selectedMessage = computed(() =>
  visibleMessages.value.find((m) => m.id === selectedMessageId.value),
);
function position(id: ParticipantId) {
  return participants.findIndex((p) => p.id === id) * 200 + 100;
}
function labelPosition(m: CollaborationMessage) {
  const first = Math.min(position(m.from), position(m.to)) / 10;
  const last = Math.max(position(m.from), position(m.to)) / 10;
  return m.kind === "observation"
    ? { left: `calc(${first}% - 64px)`, width: "128px" }
    : { left: `${first}%`, width: `${last - first}%` };
}
function name(id: ParticipantId) {
  return id === "supervisor"
    ? "ChatGPT Supervisor"
    : participants.find((p) => p.id === id)?.name;
}
function selectMessage(message: CollaborationMessage) {
  selectedMessageId.value = message.id;
  selectTask(message.taskId);
}
watch(
  [visibleMessages, () => selectedTask.value?.id],
  async () => {
    if (selectedMessage.value?.taskId !== selectedTask.value?.id)
      selectedMessageId.value = "";
    await nextTick();
    const rows = scroll.value?.querySelectorAll<HTMLElement>(
      ".sequence-message.is-task",
    );
    const last = rows?.[rows.length - 1];
    if (last && scroll.value) {
      const rowBounds = last.getBoundingClientRect();
      const frame = scroll.value.getBoundingClientRect();
      const header = scroll.value
        .querySelector(".sequence-participants")!
        .getBoundingClientRect();
      if (rowBounds.bottom > frame.bottom - 14) {
        scroll.value.scrollTop += rowBounds.bottom - frame.bottom + 14;
      } else if (rowBounds.top < header.bottom + 8) {
        scroll.value.scrollTop -= header.bottom + 8 - rowBounds.top;
      }
    }
  },
  { immediate: true },
);
</script>

<template>
  <section
    class="sequence-section"
    :data-scope="scope"
    aria-labelledby="sequence-title"
  >
    <header class="sequence-heading">
      <div class="sequence-heading-title">
        <ArrowLeftRight :size="16" />
        <h2 id="sequence-title">协作消息时序</h2>
        <span class="mock-inline">MOCK</span>
      </div>
      <div class="sequence-scope" aria-label="消息展示范围">
        <button
          :class="{ active: scope === 'conversation' }"
          :aria-pressed="scope === 'conversation'"
          aria-label="查看当前对话的消息"
          @click="scope = 'conversation'"
        >
          当前对话</button
        ><button
          :class="{ active: scope === 'task' }"
          :aria-pressed="scope === 'task'"
          aria-label="仅查看选中任务的消息"
          @click="scope = 'task'"
        >
          选中任务
        </button>
      </div>
    </header>
    <div class="sequence-subheading">
      <span
        >{{ visibleMessages.length }} 条 Mock 消息与注记<span
          v-if="scope === 'task' && selectedTask"
        >
          · {{ selectedTask.id }}</span
        ></span
      ><span><ArrowDown :size="12" />时间从上到下</span>
    </div>
    <div
      ref="scroll"
      class="sequence-scroll"
      tabindex="0"
      aria-label="协作消息泳道，可滚动查看"
    >
      <div class="sequence-canvas">
        <div class="sequence-participants">
          <span class="sequence-time-header">时间</span>
          <div class="sequence-participant-grid">
            <div
              v-for="p in participants"
              :key="p.id"
              class="sequence-participant"
              :class="{
                linked: selectedTask?.agentId === p.id,
                filtered: agentFilter === p.id,
              }"
            >
              <button
                v-if="agents.some((a) => a.id === p.id)"
                class="participant-button"
                :aria-label="`筛选 Agent：${p.name}`"
                :aria-pressed="agentFilter === p.id"
                :data-linked="selectedTask?.agentId === p.id"
                @click="filterAgent(p.id)"
              >
                <span class="participant-name"
                  ><component :is="p.icon" :size="15" />{{ p.name }}</span
                ><span class="participant-subtitle"
                  ><component
                    :is="
                      agents.find((a) => a.id === p.id)?.configured
                        ? Check
                        : CircleDashed
                    "
                    :size="10"
                  />{{ p.subtitle }}</span
                >
              </button>
              <div v-else class="participant-button participant-static">
                <span class="participant-name"
                  ><component :is="p.icon" :size="15" />{{ p.name }}</span
                ><span class="participant-subtitle">{{ p.subtitle }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-if="visibleMessages.length" class="sequence-rows">
          <div
            v-for="message in visibleMessages"
            :key="message.id"
            class="sequence-message"
            :class="[
              message.status,
              {
                'is-task': selectedTask?.id === message.taskId,
                'is-message': selectedMessageId === message.id,
              },
            ]"
            :data-time="message.time"
            :data-task="message.taskId"
            :data-kind="message.kind"
          >
            <time class="sequence-time">{{ message.time }}</time>
            <div class="message-track">
              <div class="message-lifelines" aria-hidden="true">
                <span
                  v-for="p in participants"
                  :key="p.id"
                  :class="{ linked: selectedTask?.agentId === p.id }"
                />
              </div>
              <svg
                v-if="message.kind !== 'observation'"
                class="message-line"
                viewBox="0 0 1000 58"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <defs>
                  <marker
                    :id="`arrow-${message.id}`"
                    viewBox="0 0 8 8"
                    refX="7"
                    refY="4"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path
                      d="M1 1 L7 4 L1 7"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.3"
                    />
                  </marker>
                </defs>
                <line
                  class="message-arrow"
                  :x1="position(message.from)"
                  :x2="position(message.to)"
                  y1="42"
                  y2="42"
                  :marker-end="`url(#arrow-${message.id})`"
                  :stroke-dasharray="
                    message.kind === 'pending' ||
                    position(message.to) < position(message.from)
                      ? '5 4'
                      : undefined
                  "
                />
                <rect
                  :x="position(message.from) - 3"
                  y="39"
                  width="6"
                  height="6"
                  rx="2"
                />
              </svg>
              <button
                class="sequence-message-button"
                :class="{ 'observation-note': message.kind === 'observation' }"
                :style="labelPosition(message)"
                :aria-label="`查看消息：${message.taskId} ${message.label}，${name(message.from)}${message.kind === 'observation' ? '的本地观测' : `发送至 ${name(message.to)}`}`"
                :title="`${name(message.from)} → ${name(message.to)}：${message.body}`"
                @click="selectMessage(message)"
              >
                <span>{{ message.label }}</span
                ><small>{{ message.taskId }}</small>
              </button>
            </div>
          </div>
        </div>
        <div v-else class="sequence-empty">
          <ArrowLeftRight :size="24" /><strong>{{
            conversationTasks.length
              ? "没有匹配的消息记录"
              : "这个对话还没有消息记录"
          }}</strong>
          <p>选择其他任务或对话，查看 Mock 协作过程。</p>
        </div>
      </div>
    </div>
    <div v-if="selectedMessage" class="sequence-message-preview">
      <div>
        <span
          >{{ name(selectedMessage.from)
          }}<span class="preview-arrow">{{
            selectedMessage.kind === "observation" ? "· 本地观测" : "→"
          }}</span
          ><template v-if="selectedMessage.kind !== 'observation'">{{
            name(selectedMessage.to)
          }}</template></span
        ><StatusBadge :status="selectedMessage.status" compact />
      </div>
      <p>{{ selectedMessage.body }}</p>
    </div>
    <footer class="sequence-legend">
      <span><i class="legend-line" />消息发送</span
      ><span><i class="legend-line dashed" />回传 / 待派发</span
      ><span class="sequence-legend-hint"
        >点击消息查看任务<ArrowUpRight :size="11"
      /></span>
    </footer>
    <p class="sequence-disclaimer">
      全部为 Mock 记录；待派发未发送，未知为观测注记。Agent 间经 Core
      路由，不表示自动交接。
    </p>
  </section>
</template>
