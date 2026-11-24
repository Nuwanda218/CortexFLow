<script setup lang="ts">
import { computed, ref } from "vue";
import type { HomeProject } from "../data/homeProjects";
import { provideDevelopmentWorkspace } from "../composables/useDevelopmentWorkspace";
import DevelopmentNavigator from "./DevelopmentNavigator.vue";
import WorkspaceBreadcrumbs from "./WorkspaceBreadcrumbs.vue";
import ProjectOverview from "./ProjectOverview.vue";
import StageOverview from "./StageOverview.vue";
import TaskOverview from "./TaskOverview.vue";
import ExecutionOverview from "./ExecutionOverview.vue";
import "../project-overview.css";
import "../workspace-levels.css";
const props = defineProps<{ project: HomeProject }>();
const scroll = ref<HTMLElement>();
const { focus, focusKey } = provideDevelopmentWorkspace(props.project, scroll);
const page = computed(
  () =>
    ({
      project: ProjectOverview,
      stage: StageOverview,
      task: TaskOverview,
      execution: ExecutionOverview,
    })[focus.value.level],
);
</script>
<template>
  <main
    :id="`panel-${project.id}`"
    class="project-four-level-shell"
    role="tabpanel"
    :aria-labelledby="`tab-${project.id}`"
    tabindex="-1"
  >
    <WorkspaceBreadcrumbs />
    <div class="workspace-level-body">
      <DevelopmentNavigator />
      <div ref="scroll" class="project-overview-scroll">
        <KeepAlive><component :is="page" :key="focusKey" /></KeepAlive>
      </div>
    </div>
  </main>
</template>
