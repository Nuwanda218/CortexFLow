<script setup lang="ts">
import type { ActivityStatus } from "../data/workspaceGraph";
defineProps<{ status: ActivityStatus }>();
const labels = {
  completed: "已完成",
  current: "正在推进",
  planned: "计划中",
  implemented: "已实现 · 待验证",
  failed: "失败",
  unknown: "状态未知",
};
</script>
<template>
  <span class="workspace-activity-state" :class="`activity-${status}`"
    ><span class="activity-dot" aria-hidden="true" />{{ labels[status] }}</span
  >
</template>
