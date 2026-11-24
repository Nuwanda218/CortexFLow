<script setup lang="ts">
import {
  Circle,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  LoaderCircle,
} from "lucide-vue-next";
import type { Status } from "../types";
defineProps<{ status: Status; compact?: boolean }>();
const labels = {
  waiting: "等待中",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  unknown: "未知",
};
const icons = {
  waiting: CircleDashed,
  running: LoaderCircle,
  completed: CircleCheck,
  failed: CircleX,
  unknown: CircleHelp,
};
</script>
<template>
  <span
    class="status-badge"
    :class="[status, { compact }]"
    :title="labels[status]"
    ><component
      :is="icons[status] || Circle"
      :size="13"
      aria-hidden="true"
    /><span>{{ labels[status] }}</span></span
  >
</template>
