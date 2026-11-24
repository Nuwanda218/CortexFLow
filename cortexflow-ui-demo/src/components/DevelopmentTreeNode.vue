<script setup lang="ts">
import { computed } from "vue";
import { ChevronDown, ChevronRight } from "lucide-vue-next";
import type { DevelopmentNode } from "../data/projectDevelopment";
const props = defineProps<{
  node: DevelopmentNode;
  expandedIds: Set<string>;
}>();
const emit = defineEmits<{
  toggle: [id: string];
  navigate: [target: NonNullable<DevelopmentNode["target"]>];
}>();
const expandable = computed(() => !!props.node.children?.length);
const expanded = computed(() => props.expandedIds.has(props.node.id));
const labels = {
  completed: "已完成",
  current: "正在推进",
  implemented: "已实现 · 待验证",
  planned: "计划中",
};
function open() {
  if (props.node.target) emit("navigate", props.node.target);
}
</script>
<template>
  <li
    class="development-node"
    :class="[`node-${node.status}`, `node-${node.kind}`]"
    :data-node-id="node.id"
  >
    <div class="development-row">
      <span class="development-rail" aria-hidden="true"
        ><span class="development-dot" /></span
      ><span class="development-copy"
        ><span class="development-name"
          ><button
            v-if="expandable"
            class="development-toggle"
            :aria-label="`${expanded ? '收起' : '展开'}阶段：${node.title}`"
            :aria-expanded="expanded"
            :aria-controls="`children-${node.id}`"
            @click="emit('toggle', node.id)"
          >
            <component
              :is="expanded ? ChevronDown : ChevronRight"
              :size="13"
            /></button
          ><button
            v-if="node.target"
            class="development-open"
            :aria-label="`进入${node.target.level === 'stage' ? '阶段' : '任务'}：${node.title}`"
            @click="open"
          >
            {{ node.title }}</button
          ><span v-else>{{ node.title }}</span></span
        ><span v-if="node.description" class="development-description">{{
          node.description
        }}</span></span
      ><time v-if="node.date" class="development-date" :datetime="node.date">{{
        node.date
      }}</time
      ><span v-else class="development-date" aria-label="日期未记录">—</span
      ><span class="development-status">{{
        node.kind === "origin" ? "已建立" : labels[node.status]
      }}</span>
    </div>
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
        @toggle="emit('toggle', $event)"
        @navigate="emit('navigate', $event)"
      />
    </ol>
  </li>
</template>
