<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  CheckCheck,
  ChevronRight,
  Clock3,
  Copy,
  FileText,
  X,
  ArrowUpRight,
  CircleHelp,
  AlertCircle,
} from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
import StatusBadge from "./StatusBadge.vue";
const {
  selectedTask: task,
  selectedAgent: agent,
  project,
  conversation,
  detailOpen,
  notify,
} = useWorkspace();
defineEmits<{ report: [] }>();
const panel = ref<HTMLElement>();
const narrowQuery = window.matchMedia("(max-width: 1100px)");
const isNarrow = ref(narrowQuery.matches);
let returnFocus: HTMLElement | null = null;

async function focusDrawer() {
  await nextTick();
  if (!detailOpen.value || !isNarrow.value || !panel.value) return;
  if (!panel.value.contains(document.activeElement)) {
    returnFocus = document.activeElement as HTMLElement | null;
    panel.value.querySelector<HTMLButtonElement>("button")?.focus();
  }
}
function onViewportChange(event: MediaQueryListEvent) {
  isNarrow.value = event.matches;
  if (event.matches) void focusDrawer();
}
function restoreFocus() {
  if (returnFocus?.isConnected) returnFocus.focus();
  returnFocus = null;
}
watch(
  detailOpen,
  (open) => {
    if (open) void focusDrawer();
    else restoreFocus();
  },
  { flush: "post" },
);
function containFocus(event: KeyboardEvent) {
  if (!isNarrow.value || event.key !== "Tab" || !panel.value) return;
  const controls = [
    ...panel.value.querySelectorAll<HTMLElement>(
      "button, a[href], input, select, [tabindex='0']",
    ),
  ];
  const first = controls[0],
    last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
onMounted(() => narrowQuery.addEventListener("change", onViewportChange));
onBeforeUnmount(() => {
  narrowQuery.removeEventListener("change", onViewportChange);
  restoreFocus();
});
async function copyId() {
  try {
    await navigator.clipboard.writeText(task.value!.id);
    notify("已复制 Mock 任务 ID");
  } catch {
    notify("浏览器未允许复制，可从任务信息中手动选择 ID");
  }
}
</script>
<template>
  <div
    v-if="detailOpen && task"
    class="detail-backdrop"
    @click="detailOpen = false"
  />
  <aside
    v-if="detailOpen && task"
    ref="panel"
    class="task-detail"
    aria-label="任务详情"
    :role="isNarrow ? 'dialog' : 'complementary'"
    :aria-modal="isNarrow || undefined"
    @keydown="containFocus"
  >
    <header class="detail-header">
      <span>任务详情</span><span class="mock-inline">MOCK</span
      ><button
        class="icon-button"
        aria-label="关闭任务详情"
        @click="detailOpen = false"
      >
        <X :size="17" />
      </button>
    </header>
    <div class="detail-scroll">
      <div class="detail-title">
        <span class="mono muted"
          >{{ task.id
          }}<button
            class="icon-button small"
            aria-label="复制任务 ID"
            @click="copyId"
          >
            <Copy :size="12" /></button
        ></span>
        <h2>{{ task.title }}</h2>
        <StatusBadge :status="task.status" />
        <p>{{ task.description }}</p>
      </div>
      <dl class="detail-properties">
        <div>
          <dt>项目</dt>
          <dd>{{ project.name }}</dd>
        </div>
        <div>
          <dt>来源对话</dt>
          <dd class="truncate">{{ conversation?.title }}</dd>
        </div>
        <div>
          <dt>目标 Agent</dt>
          <dd>
            {{ agent?.name
            }}<span class="mini-neutral">{{
              agent?.configured ? "已配置" : "未配置"
            }}</span>
          </dd>
        </div>
        <div>
          <dt>执行记录</dt>
          <dd class="mono">{{ task.execution.id }}</dd>
        </div>
        <div>
          <dt>创建时间</dt>
          <dd>{{ task.created }}</dd>
        </div>
        <div>
          <dt>最近更新</dt>
          <dd>{{ task.updated }}</dd>
        </div>
      </dl>
      <div
        v-if="task.status === 'unknown' || task.status === 'failed'"
        class="state-explanation"
        :class="task.status"
      >
        <component
          :is="task.status === 'unknown' ? CircleHelp : AlertCircle"
          :size="16"
        />
        <div>
          <strong>{{ task.outcome }}</strong>
          <p>
            {{
              task.status === "unknown"
                ? "未收到最终状态，无法判断执行是否结束。此状态不表示失败。"
                : "演示记录中的报告校验未通过，后续阶段尚未开始。"
            }}
          </p>
        </div>
      </div>
      <section class="detail-section">
        <h3><Clock3 :size="15" />关键事件<span>Mock</span></h3>
        <ol class="event-list">
          <li
            v-for="stage in task.execution.stages.filter(
              (s) => s.status !== 'waiting',
            )"
            :key="stage.name"
          >
            <span class="event-dot" :class="stage.status" />
            <div>
              <strong>{{ stage.name }}</strong>
              <p>{{ stage.note }}</p>
            </div>
            <time>{{ stage.time }}</time>
          </li>
        </ol>
      </section>
      <section class="detail-section">
        <h3><FileText :size="15" />执行报告</h3>
        <button
          v-if="task.report.status === 'available'"
          class="report-card"
          aria-label="预览 Mock 报告"
          @click="$emit('report')"
        >
          <span class="report-icon"><FileText :size="19" /></span
          ><span
            ><strong>{{ task.report.title }}</strong
            ><small>Mock 报告 · 点击预览</small></span
          ><ArrowUpRight :size="15" />
        </button>
        <p v-else class="detail-placeholder">
          {{
            task.report.status === "pending" ? "等待报告回传" : "暂无可用报告"
          }}
        </p>
      </section>
      <section class="detail-section">
        <h3>
          <CheckCheck :size="15" />审核状态<span
            :class="task.review.status === 'approved' ? 'success-text' : ''"
            >{{
              task.review.status === "approved"
                ? "已通过"
                : task.review.status === "pending"
                  ? "待审核"
                  : "未开始"
            }}</span
          >
        </h3>
        <p class="review-note">{{ task.review.note }}</p>
      </section>
      <p class="detail-disclaimer">
        所有时间、事件、报告与审核均为 Mock 数据。
      </p>
    </div>
    <footer class="detail-footer">
      <span class="tiny-dot" />仅查看演示记录<ChevronRight :size="13" />
    </footer>
  </aside>
</template>
