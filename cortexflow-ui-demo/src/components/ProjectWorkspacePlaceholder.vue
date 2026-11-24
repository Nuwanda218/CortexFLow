<script setup lang="ts">
import { FolderClosed } from "lucide-vue-next";
import type { HomeProject } from "../data/homeProjects";
defineProps<{ project: HomeProject }>();
// Each open tab owns one mounted instance; only this canvas will change next round.
</script>
<template>
  <main
    :id="`panel-${project.id}`"
    class="project-space-panel"
    role="tabpanel"
    :aria-labelledby="`tab-${project.id}`"
    tabindex="-1"
  >
    <header class="project-space-header">
      <FolderClosed :size="17" />
      <h1>{{ project.name }}</h1>
    </header>
    <section
      class="project-space-canvas"
      :aria-label="`${project.name} 项目内容容器`"
    >
      <span>工作区</span>
    </section>
  </main>
</template>
