<script setup lang="ts">
import { computed } from "vue";
import { useDevelopmentWorkspace } from "../composables/useDevelopmentWorkspace";
import type { DevelopmentIndexNode } from "../data/workspaceGraph";
import NavigatorNode from "./NavigatorNode.vue";
const { tree, focus, stage, task, navigate } = useDevelopmentWorkspace();
const visibleTree = computed<DevelopmentIndexNode>(() => {
  const root = tree.value;
  if (focus.value.level === "project")
    return {
      ...root,
      children: root.children.map((s) => ({ ...s, children: [] })),
    };
  if (focus.value.level === "stage")
    return {
      ...root,
      children: root.children.map((s) => ({
        ...s,
        children:
          s.id === stage.value?.id
            ? s.children.map((t) => ({ ...t, children: [] }))
            : [],
      })),
    };
  const parent = root.children.find((s) => s.id === stage.value?.id)!;
  if (focus.value.level === "task")
    return {
      ...parent,
      children: parent.children.map((t) => ({
        ...t,
        children: t.id === task.value?.id ? t.children : [],
      })),
    };
  return parent.children.find((t) => t.id === task.value?.id)!;
});
</script>
<template>
  <nav class="development-navigator" aria-label="开发导航">
    <ol class="navigator-tree">
      <NavigatorNode
        :node="visibleTree"
        :focus="focus"
        :root="true"
        @navigate="navigate"
      />
    </ol>
  </nav>
</template>
