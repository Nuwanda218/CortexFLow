<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import {
  Search,
  SlidersHorizontal,
  ArrowUpRight,
  ListTodo,
  X,
  ArrowDown,
  Command,
  Sparkles,
  Terminal,
} from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
import StatusBadge from "./StatusBadge.vue";
const {
  agents,
  conversationTasks,
  filteredTasks,
  selectedId,
  selectTask,
  agentFilter,
  statusFilter,
  search,
  clearFilters,
} = useWorkspace();
const table = ref<HTMLElement>();
watch([selectedId, filteredTasks], async () => {
  await nextTick();
  const frame = table.value;
  if (!frame) return;
  const row = [...frame.querySelectorAll<HTMLElement>(".task-row")].find(
    (el) => el.dataset.taskId === selectedId.value,
  );
  const header = frame.querySelector(".task-table-header");
  if (!row || !header) return;
  const bounds = row.getBoundingClientRect(),
    viewport = frame.getBoundingClientRect(),
    top = header.getBoundingClientRect().bottom;
  if (bounds.bottom > viewport.bottom)
    frame.scrollTop += bounds.bottom - viewport.bottom + 2;
  else if (bounds.top < top) frame.scrollTop -= top - bounds.top + 2;
});
</script>
<template>
  <section class="tasks-section" aria-labelledby="tasks-title">
    <div class="section-heading task-heading">
      <div>
        <h2 id="tasks-title">
          对话任务
          <span class="count-badge">{{ conversationTasks.length }}</span>
        </h2>
      </div>
      <span class="subtle-label">固定演示快照</span>
    </div>
    <div class="task-toolbar">
      <div class="search-field">
        <Search :size="15" /><input
          v-model="search"
          aria-label="搜索任务"
          placeholder="搜索任务或 ID…"
        /><button
          v-if="search"
          class="icon-button small"
          aria-label="清空搜索"
          @click="search = ''"
        >
          <X :size="13" />
        </button>
      </div>
      <div class="filter-control">
        <SlidersHorizontal :size="14" /><select
          v-model="statusFilter"
          aria-label="筛选任务状态"
        >
          <option value="">全部状态</option>
          <option value="running">执行中</option>
          <option value="completed">已完成</option>
          <option value="waiting">等待中</option>
          <option value="failed">失败</option>
          <option value="unknown">未知</option>
        </select>
      </div>
      <button
        v-if="agentFilter || statusFilter || search"
        class="text-button clear-filter"
        @click="clearFilters"
      >
        清除筛选<X :size="12" /></button
      ><span class="toolbar-count">{{ filteredTasks.length }} 个任务</span>
    </div>
    <div v-if="agentFilter" class="active-filter">
      <span>Agent：{{ agents.find((a) => a.id === agentFilter)?.name }}</span
      ><button
        class="icon-button small"
        aria-label="移除 Agent 筛选"
        @click="agentFilter = ''"
      >
        <X :size="12" />
      </button>
    </div>
    <div
      v-if="filteredTasks.length"
      class="task-table"
      ref="table"
      role="group"
      aria-label="对话任务"
      tabindex="0"
    >
      <div class="task-table-header task-grid" aria-hidden="true">
        <span>任务名称</span><span>Agent</span><span>状态</span
        ><span class="created-column">创建时间</span
        ><span>最近更新 <ArrowDown :size="11" /></span><span />
      </div>
      <button
        v-for="task in filteredTasks"
        :key="task.id"
        class="task-row task-grid"
        :data-task-id="task.id"
        :class="{ selected: selectedId === task.id }"
        :aria-label="`${task.id} ${task.title}`"
        :aria-pressed="selectedId === task.id"
        @click="selectTask(task.id)"
      >
        <span class="task-name"
          ><span class="task-title">{{ task.title }}</span
          ><span class="task-id">{{ task.id }}</span></span
        >
        <span class="agent-cell"
          ><span class="agent-mini" :class="task.agentId"
            ><component
              :is="
                task.agentId === 'codex'
                  ? Command
                  : task.agentId === 'claude'
                    ? Sparkles
                    : Terminal
              "
              :size="12"
              aria-hidden="true" /></span
          >{{ agents.find((a) => a.id === task.agentId)?.name }}</span
        >
        <StatusBadge :status="task.status" /><span
          class="time-cell created-column"
          >{{ task.created.slice(11) }}</span
        ><span class="time-cell">{{ task.updated.slice(11) }}</span
        ><ArrowUpRight class="row-arrow" :size="15" />
      </button>
    </div>
    <div v-else class="empty-state task-empty">
      <span class="empty-icon"><ListTodo :size="25" /></span>
      <h3>
        {{ conversationTasks.length ? "没有匹配的任务" : "这个对话还没有任务" }}
      </h3>
      <p>
        {{
          conversationTasks.length
            ? "试试其他关键词，或清除当前筛选。"
            : "未来由 Supervisor 对话产生的任务，会出现在这里。"
        }}
      </p>
      <button
        v-if="conversationTasks.length"
        class="button secondary"
        @click="clearFilters"
      >
        清除筛选</button
      ><span v-else class="subtle-label">Mock Demo · 不会创建真实任务</span>
    </div>
  </section>
</template>
