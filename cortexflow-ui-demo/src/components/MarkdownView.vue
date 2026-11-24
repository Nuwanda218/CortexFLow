<script setup lang="ts">
import { computed } from "vue";
import MarkdownIt from "markdown-it";
const props = defineProps<{ source: string }>();
const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  breaks: false,
}).disable("image");
const html = computed(() => markdown.render(props.source));
</script>
<template><div class="workspace-markdown" v-html="html" /></template>
