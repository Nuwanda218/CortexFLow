<script setup lang="ts">
import { ref } from "vue";
import {
  ChevronDown,
  ChevronRight,
  FolderClosed,
  Plus,
  MessageSquare,
  SquarePen,
  ArrowUpRight,
  CircleHelp,
  Layers2,
} from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
const {
  projects,
  projectId,
  conversationId,
  selectProject,
  selectConversation,
} = useWorkspace();
const collapsed = ref<string[]>([]);
const emit = defineEmits<{
  create: [kind: "project" | "conversation"];
  about: [];
}>();
function toggle(id: string) {
  collapsed.value = collapsed.value.includes(id)
    ? collapsed.value.filter((x) => x !== id)
    : [...collapsed.value, id];
}
function choose(id: string) {
  collapsed.value = collapsed.value.filter((x) => x !== id);
  selectProject(id);
}
</script>
<template>
  <aside class="project-sidebar" aria-label="项目导航">
    <button
      class="new-conversation"
      aria-label="新建对话"
      @click="emit('create', 'conversation')"
    >
      <SquarePen :size="17" /><span>新建对话</span><Plus :size="15" />
    </button>
    <div class="sidebar-heading">
      <span>项目空间</span
      ><button
        class="icon-button small"
        aria-label="新建项目"
        @click="emit('create', 'project')"
      >
        <Plus :size="16" />
      </button>
    </div>
    <div class="project-tree">
      <section v-for="p in projects" :key="p.id" class="project-group">
        <div class="project-row" :class="{ active: projectId === p.id }">
          <button
            class="tree-toggle"
            :aria-label="`${collapsed.includes(p.id) ? '展开' : '折叠'}项目：${p.name}`"
            :aria-expanded="!collapsed.includes(p.id)"
            @click="toggle(p.id)"
          >
            <component
              :is="collapsed.includes(p.id) ? ChevronRight : ChevronDown"
              :size="13"
            />
          </button>
          <button
            class="project-select"
            :aria-label="`选择项目：${p.name}`"
            :aria-current="projectId === p.id ? 'true' : undefined"
            @click="choose(p.id)"
          >
            <FolderClosed :size="17" /><span>{{ p.name }}</span>
          </button>
          <button
            v-if="projectId === p.id"
            class="icon-button small project-add"
            aria-label="为当前项目新建对话"
            @click="emit('create', 'conversation')"
          >
            <Plus :size="14" />
          </button>
        </div>
        <div v-if="!collapsed.includes(p.id)" class="conversation-list">
          <button
            v-for="c in p.conversations"
            :key="c.id"
            class="conversation-item"
            :class="{ selected: conversationId === c.id }"
            :aria-label="`选择对话：${c.title}`"
            :aria-current="conversationId === c.id ? 'true' : undefined"
            @click="selectConversation(p.id, c.id)"
          >
            <MessageSquare :size="14" /><span>{{ c.title }}</span
            ><span v-if="conversationId === c.id" class="selected-dot" />
          </button>
          <p v-if="!p.conversations.length" class="tree-empty">暂无对话</p>
        </div>
      </section>
    </div>
    <div class="sidebar-bottom">
      <div class="local-notice">
        <span class="local-symbol"><Layers2 :size="19" /></span>
        <div>
          <strong>本地设计原型</strong>
          <p>使用 Mock 数据，无真实执行</p>
        </div>
      </div>
      <button class="sidebar-help" @click="emit('about')">
        <CircleHelp :size="16" />关于此 Demo<ArrowUpRight :size="14" />
      </button>
      <div class="sidebar-version">
        <span>本地原型 · v0.1</span><span>Mock 数据</span>
      </div>
    </div>
  </aside>
</template>
