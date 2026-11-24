<script setup lang="ts">
import { ChevronRight, FolderClosed, CircleHelp } from "lucide-vue-next";
import AgentSequence from "./AgentSequence.vue";
import WorkflowRoadmap from "./WorkflowRoadmap.vue";
import TaskList from "./TaskList.vue";
import ExecutionTimeline from "./ExecutionTimeline.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { MOCK_SNAPSHOT } from "../data/mock";
const { project, conversation, conversationTasks } = useWorkspace();
</script>
<template>
  <main id="main" tabindex="-1" class="workspace work-workspace">
    <div class="workspace-breadcrumb">
      <FolderClosed :size="14" /><span>{{ project.name }}</span
      ><ChevronRight :size="13" /><span>{{
        conversation?.title ?? "项目概览"
      }}</span
      ><span class="breadcrumb-end">工作空间</span>
    </div>
    <div class="workspace-scroll">
      <div class="workspace-content">
        <header class="workspace-title">
          <div>
            <h1>{{ conversation?.title ?? project.name }}</h1>
            <p>{{ conversation?.description ?? project.description }}</p>
          </div>
          <span class="snapshot"
            ><span class="tiny-dot" />Mock 快照
            <span>{{ MOCK_SNAPSHOT }}</span></span
          >
        </header>
        <WorkflowRoadmap /><AgentSequence /><TaskList /><ExecutionTimeline
          v-if="conversationTasks.length"
        />
        <footer class="workspace-footer">
          <CircleHelp :size="13" /><span
            >这是独立的前端设计原型。所有任务与状态均为 Mock，不连接真实 Core 或
            Agent。</span
          >
        </footer>
      </div>
    </div>
  </main>
</template>
