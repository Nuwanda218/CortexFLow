<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import TopNavigation from "./TopNavigation.vue";
import ProjectTabs from "./ProjectTabs.vue";
import ProjectHome from "./ProjectHome.vue";
import ProjectWorkspace from "./ProjectWorkspace.vue";
import NewProjectDialog from "./NewProjectDialog.vue";
import { useProjectTabs } from "../composables/useProjectTabs";
const { activeId, openProjects, activate, close } = useProjectTabs();
const route = useRoute(),
  router = useRouter();
const creating = ref(false);
let overviewDemoPrepared = false;
function path(id: string) {
  return id === "home" ? "/" : `/project/${encodeURIComponent(id)}`;
}
async function select(id: string) {
  if (!activate(id)) return;
  await router.push(path(id));
  await nextTick();
  if (activeId.value === id) document.getElementById(`tab-${id}`)?.focus();
}
function closeTab(id: string) {
  close(id);
  if (route.path !== path(activeId.value))
    void router.replace(path(activeId.value));
}
function created(id: string) {
  creating.value = false;
  select(id);
}
watch(
  () => route.fullPath,
  () => {
    const id =
      typeof route.params.projectId === "string"
        ? route.params.projectId
        : "home";
    if (
      id === "cortex" &&
      route.query.demo === "overview" &&
      !overviewDemoPrepared
    ) {
      activate("cortex");
      activate("loongarch");
      overviewDemoPrepared = true;
    }
    if (!activate(id)) {
      activate("home");
      void router.replace("/");
    }
  },
  { immediate: true },
);
function focusMain() {
  document.getElementById(`panel-${activeId.value}`)?.focus();
}
</script>
<template>
  <div class="project-app-shell">
    <a class="skip-link" href="#main" @click.prevent="focusMain">跳到主内容</a
    ><TopNavigation /><ProjectTabs @select="select" @close="closeTab" />
    <div class="project-app-pages">
      <ProjectHome
        v-show="activeId === 'home'"
        @open="select"
        @create="creating = true"
      /><ProjectWorkspace
        v-for="project in openProjects"
        v-show="activeId === project.id"
        :key="project.id"
        :project="project"
      />
    </div>
    <NewProjectDialog
      v-if="creating"
      @close="creating = false"
      @created="created"
    />
  </div>
</template>
