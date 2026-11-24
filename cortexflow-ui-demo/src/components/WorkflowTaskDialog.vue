<script setup lang="ts">
import { ref } from "vue";
import DemoDialog from "./DemoDialog.vue";
import { useWorkflow } from "../composables/useWorkflow";
import { useWorkspace } from "../composables/useWorkspace";
const { addSupplement } = useWorkflow();
const { agents } = useWorkspace();
const title = ref(""),
  description = ref(""),
  agentId = ref("codex");
const emit = defineEmits<{ close: [] }>();
function submit() {
  if (!title.value.trim() || !description.value.trim()) return;
  addSupplement({
    title: title.value,
    description: description.value,
    agentId: agentId.value,
  });
  emit("close");
}
</script>
<template>
  <DemoDialog title="新增补充任务 · Mock" @close="$emit('close')"
    ><form class="workflow-form" @submit.prevent="submit">
      <p class="dialog-description">
        创建一次新的工作，保留已有 Task 的状态与汇报。
      </p>
      <label for="supplement-title">任务名称</label
      ><input
        id="supplement-title"
        v-model="title"
        required
        maxlength="60"
        class="form-input"
      /><label for="supplement-agent">执行 Agent</label
      ><select id="supplement-agent" v-model="agentId">
        <option v-for="agent in agents" :key="agent.id" :value="agent.id">
          {{ agent.name }}{{ agent.configured ? "" : "（未配置）" }}
        </option></select
      ><label for="supplement-goal">简要目标</label
      ><textarea
        id="supplement-goal"
        v-model="description"
        required
        maxlength="300"
        rows="3"
      />
      <p class="form-help">
        仅新增本地 Mock 任务，初始等待、尚未派发。不向 Core 或 Agent 发送请求。
      </p>
      <div class="dialog-actions">
        <button type="button" class="button secondary" @click="$emit('close')">
          取消</button
        ><button
          class="button primary"
          :disabled="!title.trim() || !description.trim()"
        >
          创建补充任务
        </button>
      </div>
    </form></DemoDialog
  >
</template>
