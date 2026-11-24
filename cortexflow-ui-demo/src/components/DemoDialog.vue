<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { X } from "lucide-vue-next";
const props = defineProps<{ title: string }>();
const emit = defineEmits<{ close: [] }>();
const root = ref<HTMLElement>();
const previousFocus = document.activeElement as HTMLElement | null;
function keydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.stopPropagation();
    emit("close");
  }
  if (e.key === "Tab") {
    const nodes = [
      ...root.value!.querySelectorAll<HTMLElement>(
        "button, input, textarea, select, a[href]",
      ),
    ].filter((n) => !(n as HTMLButtonElement).disabled);
    const first = nodes[0],
      last = nodes.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  }
}
onMounted(async () => {
  await nextTick();
  (
    root.value?.querySelector("input") ?? root.value?.querySelector("button")
  )?.focus();
});
onBeforeUnmount(() => previousFocus?.focus());
</script>
<template>
  <div class="modal-backdrop" @click.self="$emit('close')" @keydown="keydown">
    <section
      ref="root"
      class="demo-dialog"
      role="dialog"
      aria-modal="true"
      :aria-label="props.title"
    >
      <header>
        <h2>{{ title }}</h2>
        <button
          class="icon-button"
          aria-label="关闭弹窗"
          @click="$emit('close')"
        >
          <X :size="18" />
        </button>
      </header>
      <slot />
    </section>
  </div>
</template>
