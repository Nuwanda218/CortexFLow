<script setup lang="ts">
import { useDevelopmentWorkspace } from "../composables/useDevelopmentWorkspace";
import DevelopmentTreeNode from "./DevelopmentTreeNode.vue";
const { project, projectNodes, expandedIds, toggle, navigate } =
  useDevelopmentWorkspace();
</script>
<template>
  <div class="project-overview-content workspace-project-page">
    <header class="project-overview-heading">
      <h1>{{ project.name }}</h1>
      <p>{{ project.description }}</p>
    </header>
    <section
      class="project-development"
      :aria-label="`${project.name} 开发流程`"
    >
      <h2>开发流程</h2>
      <ol v-if="projectNodes.length" class="development-tree">
        <DevelopmentTreeNode
          v-for="node in projectNodes"
          :key="node.id"
          :node="node"
          :expanded-ids="expandedIds"
          @toggle="toggle"
          @navigate="navigate"
        />
      </ol>
      <p v-else class="development-empty">暂无开发记录</p>
    </section>
  </div>
</template>
