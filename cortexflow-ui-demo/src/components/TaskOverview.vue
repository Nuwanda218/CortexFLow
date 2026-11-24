<script setup lang="ts">
import { computed } from "vue";
import { ArrowUpRight } from "lucide-vue-next";
import { taskExecutions } from "../data/workspaceGraph";
import { useDevelopmentWorkspace } from "../composables/useDevelopmentWorkspace";
import ActivityState from "./ActivityState.vue";
import MarkdownView from "./MarkdownView.vue";
const { task, navigate } = useDevelopmentWorkspace();
const records = computed(() =>
  task.value ? taskExecutions(task.value.id) : [],
);
</script>
<template>
  <article v-if="task" class="workspace-document-page workspace-task-page">
    <header class="workspace-page-heading">
      <h1>{{ task.title }}</h1>
      <div class="workspace-meta-line">
        <ActivityState :status="task.status" />
      </div>
    </header>
    <MarkdownView
      class="task-context-markdown"
      :source="task.contextMarkdown"
    />
    <section class="task-executions">
      <h2>实施记录</h2>
      <div v-if="records.length" class="execution-record-list">
        <button
          v-for="record in records"
          :key="record.id"
          class="execution-record-row"
          :aria-label="`查看实施：${record.title}`"
          @click="navigate({ level: 'execution', id: record.id })"
        >
          <span class="execution-record-name">{{ record.title }}</span
          ><span>{{ record.agent }}</span
          ><time>{{ record.startedAt?.slice(0, 10) ?? "未记录" }}</time
          ><ActivityState :status="record.status" /><ArrowUpRight :size="14" />
        </button>
      </div>
      <p v-else class="workspace-empty-record">暂无实施记录</p>
    </section>
  </article>
</template>
