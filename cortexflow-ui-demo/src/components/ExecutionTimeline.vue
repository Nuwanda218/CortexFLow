<script setup lang="ts">
import {
  Check,
  CircleDashed,
  CircleHelp,
  LoaderCircle,
  X,
  ArrowUpRight,
} from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
const { selectedTask, detailOpen } = useWorkspace();
const icons = {
  completed: Check,
  running: LoaderCircle,
  waiting: CircleDashed,
  failed: X,
  unknown: CircleHelp,
};
const labels = {
  completed: "已完成",
  running: "处理中",
  waiting: "等待",
  failed: "失败",
  unknown: "未知",
};
</script>
<template>
  <section
    v-if="selectedTask"
    class="execution-section"
    aria-labelledby="execution-title"
  >
    <div class="section-heading">
      <div>
        <h2 id="execution-title">
          执行记录 <span class="mono muted">{{ selectedTask.id }}</span><span
            class="execution-task-title"
            >{{ selectedTask.title }}</span
          >
        </h2>
      </div>
      <button class="text-button" @click="detailOpen = true">
        查看详情<ArrowUpRight :size="13" />
      </button>
    </div>
    <ol class="execution-timeline">
      <li
        v-for="(stage, i) in selectedTask.execution.stages"
        :key="stage.name"
        class="execution-stage"
        :class="stage.status"
        :title="stage.note"
      >
        <span class="stage-symbol"
          ><component :is="icons[stage.status]" :size="14" /></span
        ><span class="stage-name">{{ stage.name }}</span
        ><span class="stage-status"
          >{{ labels[stage.status]
          }}<span class="stage-time"> · {{ stage.time }}</span></span
        ><span v-if="i < 6" class="stage-connector" />
      </li>
    </ol>
    <p class="execution-note">
      <span class="tiny-dot" :class="selectedTask.status" />{{
        selectedTask.outcome
      }}<span class="mono">{{ selectedTask.execution.id }}</span>
    </p>
  </section>
</template>
