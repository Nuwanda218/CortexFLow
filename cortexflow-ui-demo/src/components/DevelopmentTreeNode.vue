<script setup lang="ts">
import { computed } from "vue";
import { ChevronDown, ChevronRight } from "lucide-vue-next";
import type { DevelopmentNode } from "../data/projectDevelopment";
const props = defineProps<{
  node: DevelopmentNode;
  expandedIds: Set<string>;
}>();
defineEmits<{ toggle: [id: string] }>();
const expandable = computed(() => !!props.node.children?.length);
const expanded = computed(() => props.expandedIds.has(props.node.id));
const labels = {
  completed: "已完成",
  current: "当前聚焦",
  implemented: "已实现 · 待验证",
  planned: "计划中",
};
</script>
<template>
  <li
    class="development-node"
    :class="[`node-${node.status}`, `node-${node.kind}`]"
    :data-node-id="node.id"
  >
    <component
      :is="expandable ? 'button' : 'div'"
      class="development-row"
      :class="{ 'is-expandable': expandable }"
      :aria-label="
        expandable
          ? `${expanded ? '收起' : '展开'}阶段：${node.title}`
          : undefined
      "
      :aria-expanded="expandable ? expanded : undefined"
      :aria-controls="expandable ? `children-${node.id}` : undefined"
      @click="expandable && $emit('toggle', node.id)"
    >
      <span class="development-rail" aria-hidden="true"
        ><span class="development-dot"
      /></span>
      <span class="development-copy"
        ><span class="development-name"
          ><component
            v-if="expandable"
            :is="expanded ? ChevronDown : ChevronRight"
            :size="13"
            aria-hidden="true"
          /><span>{{ node.title }}</span></span
        ><span v-if="node.description" class="development-description">{{
          node.description
        }}</span></span
      >
      <time v-if="node.date" class="development-date" :datetime="node.date">{{
        node.date
      }}</time><span
        v-else
        class="development-date"
        :aria-label="node.status === 'planned' ? '计划时间待定' : '日期未记录'"
        >—</span
      >
      <span class="development-status">{{
        node.kind === "origin" ? "已建立" : labels[node.status]
      }}</span>
    </component>
    <ol
      v-if="expandable && expanded"
      :id="`children-${node.id}`"
      class="development-children"
      :class="{ 'children-parallel': node.childrenMode === 'parallel' }"
    >
      <DevelopmentTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :expanded-ids="expandedIds"
        @toggle="$emit('toggle', $event)"
      />
    </ol>
  </li>
</template>
