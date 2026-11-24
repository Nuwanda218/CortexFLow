<script setup lang="ts">
import { ChevronRight } from "lucide-vue-next";
import { useDevelopmentWorkspace } from "../composables/useDevelopmentWorkspace";
const { breadcrumbs, navigate, home } = useDevelopmentWorkspace();
</script>
<template>
  <nav class="workspace-breadcrumbs" aria-label="层级路径">
    <button @click="home">全部项目</button
    ><template v-for="(item, index) in breadcrumbs" :key="item.id"
      ><ChevronRight :size="12" aria-hidden="true" /><button
        v-if="index < breadcrumbs.length - 1"
        :title="item.title"
        @click="navigate(item)"
      >
        {{ item.title }}</button
      ><span v-else aria-current="page" :title="item.title">{{
        item.title
      }}</span></template
    ><span class="workspace-mock-label">Mock</span>
  </nav>
</template>
