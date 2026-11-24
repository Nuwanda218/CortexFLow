<script setup lang="ts">
import { computed } from "vue";
import { useDevelopmentWorkspace } from "../composables/useDevelopmentWorkspace";
import { taskTree } from "../data/workspaceGraph";
import ActivityState from "./ActivityState.vue";
import DevelopmentTreeNode from "./DevelopmentTreeNode.vue";
const { stage, expandedIds, toggle, navigate } = useDevelopmentWorkspace();
const nodes = computed(() => (stage.value ? taskTree(stage.value.id) : []));
</script>
<template>
  <article v-if="stage" class="workspace-document-page workspace-stage-page">
    <header class="workspace-page-heading">
      <h1>{{ stage.title }}</h1>
      <p>{{ stage.goal }}</p>
      <div class="workspace-meta-line">
        <ActivityState :status="stage.status" /><span
          >最近更新 <time>{{ stage.updatedAt ?? "未记录" }}</time></span
        >
      </div>
    </header>
    <section class="project-development">
      <h2>Task 开发树</h2>
      <ol v-if="nodes.length" class="development-tree">
        <DevelopmentTreeNode
          v-for="node in nodes"
          :key="node.id"
          :node="node"
          :expanded-ids="expandedIds"
          @toggle="toggle"
          @navigate="navigate"
        />
      </ol>
      <p v-else class="workspace-empty-record">任务尚未细化</p>
    </section>
  </article>
</template>
