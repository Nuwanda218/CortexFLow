<script setup lang="ts">
import { ref } from "vue";
import { Check, ChevronDown, ChevronRight, Copy } from "lucide-vue-next";
import { useDevelopmentWorkspace } from "../composables/useDevelopmentWorkspace";
import ActivityState from "./ActivityState.vue";
import MarkdownView from "./MarkdownView.vue";
const { execution } = useDevelopmentWorkspace();
const promptOpen = ref(false),
  copyState = ref("");
async function copy() {
  if (!execution.value) return;
  try {
    await navigator.clipboard.writeText(execution.value.prompt);
    copyState.value = "已复制";
  } catch {
    copyState.value = "复制未成功，可展开后手动选择";
  }
}
</script>
<template>
  <article
    v-if="execution"
    class="workspace-document-page workspace-execution-page"
  >
    <header class="workspace-page-heading">
      <h1>{{ execution.title }}</h1>
      <div class="workspace-meta-line">
        <span>{{ execution.agent }}</span
        ><ActivityState :status="execution.status" />
      </div>
      <dl class="execution-times">
        <div>
          <dt>开始</dt>
          <dd>{{ execution.startedAt ?? "未记录" }}</dd>
        </div>
        <div>
          <dt>结束</dt>
          <dd>{{ execution.endedAt ?? "未记录" }}</dd>
        </div>
      </dl>
    </header>
    <section class="execution-section-block">
      <div class="workspace-section-heading">
        <button
          class="execution-prompt-toggle"
          :aria-label="`${promptOpen ? '收起' : '展开'}原始工作委托`"
          :aria-expanded="promptOpen"
          @click="promptOpen = !promptOpen"
        >
          <component
            :is="promptOpen ? ChevronDown : ChevronRight"
            :size="15"
          />原始工作委托</button
        ><button
          class="text-button"
          aria-label="复制原始工作委托"
          @click="copy"
        >
          <Check v-if="copyState === '已复制'" :size="13" /><Copy
            v-else
            :size="13"
          />复制</button
        ><span class="copy-state" role="status">{{ copyState }}</span>
      </div>
      <pre v-if="promptOpen" class="execution-prompt">{{
        execution.prompt
      }}</pre>
    </section>
    <section class="execution-section-block" aria-label="执行过程">
      <h2>执行过程</h2>
      <ol v-if="execution.steps?.length" class="execution-process-list">
        <li v-for="(step, index) in execution.steps" :key="index">
          <time>{{ step.time }}</time>
          <div>
            <strong>{{ step.title }}</strong>
            <p v-if="step.detail">{{ step.detail }}</p>
          </div>
        </li>
      </ol>
      <p v-else class="workspace-empty-record">未记录</p>
    </section>
    <section class="execution-section-block">
      <h2>原始执行报告</h2>
      <MarkdownView
        v-if="execution.reportMarkdown"
        class="execution-report-markdown"
        :source="execution.reportMarkdown"
      />
      <p v-else class="workspace-empty-record">未记录</p>
    </section>
    <section
      v-if="execution.references.length"
      class="execution-section-block execution-references"
    >
      <h2>产出与引用</h2>
      <ul>
        <li v-for="ref in execution.references" :key="ref.label">
          <span>{{ ref.kind === "commit" ? "Git" : "文件" }}</span
          ><code>{{ ref.label }}</code>
        </li>
      </ul>
    </section>
  </article>
</template>
