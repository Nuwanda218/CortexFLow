<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { FolderClosed, House, X } from "lucide-vue-next";
import { useProjectTabs } from "../composables/useProjectTabs";
const { openProjects, activeId } = useProjectTabs();
const emit = defineEmits<{ select: [id: string]; close: [id: string] }>();
const strip = ref<HTMLElement>();
const tabs = computed(() => [
  { id: "home", name: "全部项目" },
  ...openProjects.value,
]);
function focusActive() {
  strip.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
}
async function closeTab(id: string) {
  emit("close", id);
  await nextTick();
  focusActive();
}
async function keydown(event: KeyboardEvent, index: number) {
  let next: number | undefined;
  if (event.key === "ArrowRight") next = (index + 1) % tabs.value.length;
  else if (event.key === "ArrowLeft")
    next = (index + tabs.value.length - 1) % tabs.value.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = tabs.value.length - 1;
  else if (event.key === "Delete" && tabs.value[index]?.id !== "home") {
    event.preventDefault();
    await closeTab(tabs.value[index]!.id);
    return;
  }
  if (next !== undefined) {
    event.preventDefault();
    emit("select", tabs.value[next]!.id);
    await nextTick();
    focusActive();
  }
}
watch(activeId, async () => {
  await nextTick();
  if (activeId.value === "home") return;
  strip.value
    ?.querySelector(".project-app-tab.is-active")
    ?.scrollIntoView({ block: "nearest", inline: "nearest" });
});
</script>
<template>
  <div
    ref="strip"
    class="project-tabs-strip"
    role="tablist"
    aria-label="工作空间标签页"
  >
    <div
      v-for="(tab, index) in tabs"
      :key="tab.id"
      class="project-app-tab"
      :class="{
        'is-active': activeId === tab.id,
        'is-home': tab.id === 'home',
      }"
    >
      <button
        :id="`tab-${tab.id}`"
        class="project-tab-select"
        role="tab"
        :aria-selected="activeId === tab.id"
        :aria-controls="`panel-${tab.id}`"
        :tabindex="activeId === tab.id ? 0 : -1"
        :title="tab.name"
        @click="emit('select', tab.id)"
        @keydown="keydown($event, index)"
      >
        <component
          :is="tab.id === 'home' ? House : FolderClosed"
          :size="14"
          aria-hidden="true"
        /><span>{{ tab.name }}</span></button
      ><button
        v-if="tab.id !== 'home'"
        class="project-tab-close"
        :aria-label="`关闭项目：${tab.name}`"
        title="关闭项目（Delete）"
        tabindex="-1"
        @click="closeTab(tab.id)"
      >
        <X :size="13" />
      </button>
    </div>
  </div>
</template>
