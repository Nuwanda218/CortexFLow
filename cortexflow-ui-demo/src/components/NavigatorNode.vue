<script setup lang="ts">
import type {
  DevelopmentIndexNode,
  WorkspaceFocus,
} from "../data/workspaceGraph";
defineProps<{
  node: DevelopmentIndexNode;
  focus: WorkspaceFocus;
  root?: boolean;
}>();
defineEmits<{ navigate: [focus: WorkspaceFocus] }>();
</script>
<template>
  <li
    class="navigator-node"
    :class="[{ 'navigator-root': root }, `activity-${node.status}`]"
  >
    <button
      :class="{
        'is-focused': focus.level === node.level && focus.id === node.id,
      }"
      :aria-label="`浏览：${node.title}`"
      :aria-current="
        focus.level === node.level && focus.id === node.id ? 'page' : undefined
      "
      :title="node.title"
      @click="$emit('navigate', { level: node.level, id: node.id })"
    >
      <span class="activity-dot" aria-hidden="true" /><span>{{
        node.title
      }}</span>
    </button>
    <ol v-if="node.children.length">
      <NavigatorNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :focus="focus"
        @navigate="$emit('navigate', $event)"
      />
    </ol>
  </li>
</template>
