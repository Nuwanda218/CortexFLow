<script setup lang="ts">
import { ref } from "vue";
import DemoDialog from "./DemoDialog.vue";
import { useProjectTabs } from "../composables/useProjectTabs";
const { createProject } = useProjectTabs();
const name = ref(""),
  description = ref(""),
  error = ref("");
const emit = defineEmits<{ close: []; created: [id: string] }>();
function submit() {
  const result = createProject(name.value, description.value);
  if (typeof result === "string") {
    error.value = result;
    return;
  }
  emit("created", result.id);
}
</script>
<template>
  <DemoDialog title="新建项目" @close="emit('close')"
    ><form class="home-project-form" @submit.prevent="submit">
      <label class="form-label" for="home-project-name">项目名称</label
      ><input
        id="home-project-name"
        v-model="name"
        class="form-input"
        required
        maxlength="64"
        autocomplete="off"
        :aria-invalid="!!error"
        :aria-describedby="error ? 'project-create-error' : undefined"
        @input="error = ''"
      /><label class="form-label" for="home-project-description">项目概况</label
      ><textarea
        id="home-project-description"
        v-model="description"
        maxlength="160"
        rows="3"
        placeholder="项目的简要用途"
      />
      <p
        v-if="error"
        id="project-create-error"
        class="home-form-error"
        role="alert"
      >
        {{ error }}
      </p>
      <div class="dialog-actions">
        <button type="button" class="button secondary" @click="emit('close')">
          取消</button
        ><button type="submit" class="button primary" :disabled="!name.trim()">
          创建项目
        </button>
      </div>
    </form></DemoDialog
  >
</template>
