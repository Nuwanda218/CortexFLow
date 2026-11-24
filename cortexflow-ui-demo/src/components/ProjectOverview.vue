<script setup lang="ts">
import { computed, ref } from "vue";
import type { HomeProject } from "../data/homeProjects";
import { projectDevelopment } from "../data/projectDevelopment";
import type { DevelopmentNode } from "../data/projectDevelopment";
import DevelopmentTreeNode from "./DevelopmentTreeNode.vue";
const props = defineProps<{ project: HomeProject }>();
const nodes = computed(
  () =>
    projectDevelopment.find((p) => p.projectId === props.project.id)?.nodes ??
    [],
);
function defaults(items: DevelopmentNode[]): string[] {
  return items.flatMap((n) => [
    ...(n.defaultExpanded ? [n.id] : []),
    ...defaults(n.children ?? []),
  ]);
}
const expandedIds = ref(new Set(defaults(nodes.value)));
function toggle(id: string) {
  const next = new Set(expandedIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expandedIds.value = next;
}
</script>
<template>
  <div class="project-overview-content">
    <header class="project-overview-heading">
      <h1>{{ project.name }}</h1>
      <p>{{ project.description }}</p>
    </header>
    <section
      class="project-development"
      :aria-label="`${project.name} 开发流程`"
    >
      <h2>开发流程</h2>
      <ol v-if="nodes.length" class="development-tree">
        <DevelopmentTreeNode
          v-for="node in nodes"
          :key="node.id"
          :node="node"
          :expanded-ids="expandedIds"
          @toggle="toggle"
        />
      </ol>
      <p v-else class="development-empty">暂无开发记录</p>
    </section>
  </div>
</template>
