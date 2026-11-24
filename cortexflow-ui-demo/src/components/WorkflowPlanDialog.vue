<script setup lang="ts">
import { ref } from "vue";
import DemoDialog from "./DemoDialog.vue";
import { useWorkflow } from "../composables/useWorkflow";
const { focusedSlot, savePlan } = useWorkflow();
const title = ref(focusedSlot.value!.title),
  goal = ref(focusedSlot.value!.goal),
  plan = ref(focusedSlot.value!.plan.join("\n"));
const emit = defineEmits<{ close: [] }>();
function save() {
  if (!title.value.trim() || !goal.value.trim()) return;
  savePlan({ title: title.value, goal: goal.value, plan: plan.value });
  emit("close");
}
</script>
<template>
  <DemoDialog title="编辑后续阶段 · Mock" @close="$emit('close')"
    ><form class="workflow-form" @submit.prevent="save">
      <p class="dialog-description">
        只更新当前有效计划，已有任务与执行结果保留。
      </p>
      <label for="slot-title">阶段名称</label
      ><input
        id="slot-title"
        v-model="title"
        required
        maxlength="40"
        class="form-input"
      /><label for="slot-goal">阶段目标</label
      ><textarea
        id="slot-goal"
        v-model="goal"
        required
        maxlength="240"
        rows="3"
      /><label for="slot-plan">初步计划（每行一条，可留空）</label
      ><textarea id="slot-plan" v-model="plan" maxlength="600" rows="4" />
      <p class="form-help">
        仅修改本地 Mock 规划，刷新或重置后恢复。不会改变当前执行阶段。
      </p>
      <div class="dialog-actions">
        <button type="button" class="button secondary" @click="$emit('close')">
          取消</button
        ><button
          class="button primary"
          :disabled="!title.trim() || !goal.trim()"
        >
          保存计划
        </button>
      </div>
    </form></DemoDialog
  >
</template>
