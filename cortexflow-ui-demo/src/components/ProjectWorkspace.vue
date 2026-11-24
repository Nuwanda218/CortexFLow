<script setup lang="ts">
import { ref } from "vue";
import { PanelsTopLeft } from "lucide-vue-next";
import type { HomeProject } from "../data/homeProjects";
import ProjectOverview from "./ProjectOverview.vue";
import "../project-overview.css";
defineProps<{ project: HomeProject }>();
const scroll = ref<HTMLElement>();
function overview() {
  if (scroll.value) scroll.value.scrollTop = 0;
}
</script>
<template>
  <main
    :id="`panel-${project.id}`"
    class="project-overview-shell"
    role="tabpanel"
    :aria-labelledby="`tab-${project.id}`"
    tabindex="-1"
  >
    <nav class="project-local-nav" :aria-label="`${project.name} 项目导航`">
      <button aria-current="page" @click="overview">
        <PanelsTopLeft :size="15" aria-hidden="true" />概览
      </button>
    </nav>
    <div ref="scroll" class="project-overview-scroll">
      <ProjectOverview :project="project" />
    </div>
  </main>
</template>
