<script setup lang="ts">
import {
  ArrowUpRight,
  ChevronRight,
  FolderClosed,
  MessageSquare,
  PanelsTopLeft,
  Plug,
  ShieldCheck,
} from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
const { project, conversation } = useWorkspace();
</script>
<template>
  <main id="main" tabindex="-1" class="workspace chat-workspace">
    <div class="workspace-breadcrumb">
      <FolderClosed :size="14" /><span>{{ project.name }}</span
      ><ChevronRight :size="13" /><span>{{
        conversation?.title ?? "项目概览"
      }}</span
      ><span class="breadcrumb-end">聊天空间</span>
    </div>
    <section class="webview-host" aria-label="ChatGPT WebView 内容容器">
      <div class="webview-top">
        <span
          ><MessageSquare :size="15" />ChatGPT<span class="mini-neutral"
            >原生 WebView</span
          ></span
        ><span><span class="tiny-dot" />未连接</span>
      </div>
      <div class="webview-center">
        <span class="chat-symbol"
          ><MessageSquare :size="30" stroke-width="1.5" /></span
        ><span class="placeholder-tag">WEBVIEW HOST</span>
        <h1>ChatGPT 原生对话</h1>
        <p class="chat-subtitle">
          当前对话：{{ conversation?.title ?? "尚未选择对话" }}
        </p>
        <div class="placeholder-badge">
          <Plug :size="14" />WebView Integration Placeholder
        </div>
        <p class="chat-explanation">
          这里将由独立 WebView 加载 ChatGPT 原生页面。<br />当前为内容容器占位，尚未连接任何服务。
        </p>
        <RouterLink class="button primary" to="/work"
          ><PanelsTopLeft :size="15" />前往工作空间<ArrowUpRight :size="14"
        /></RouterLink>
        <div class="chat-context">
          <span class="context-initial">{{ project.initials }}</span>
          <div>
            <strong>{{ project.name }}</strong><span>{{
              conversation?.title ?? "尚未选择对话"
            }}</span>
          </div>
        </div>
      </div>
      <footer class="webview-footer">
        <ShieldCheck :size="14" /><span
          >Mock Demo · 不加载 ChatGPT 页面，不发送消息</span
        ><span>独立宿主边界</span>
      </footer>
    </section>
  </main>
</template>
