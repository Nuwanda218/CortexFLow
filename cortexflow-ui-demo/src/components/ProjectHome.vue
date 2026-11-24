<script setup lang="ts">
import {
  CalendarDays,
  ChevronRight,
  FolderClosed,
  ListTodo,
  Plus,
  Search,
  X,
} from "lucide-vue-next";
import { useProjectTabs } from "../composables/useProjectTabs";
const { filteredProjects, search } = useProjectTabs();
defineEmits<{ open: [id: string]; create: [] }>();
</script>
<template>
  <main
    id="panel-home"
    class="project-home-panel"
    role="tabpanel"
    aria-labelledby="tab-home"
    tabindex="-1"
  >
    <div class="project-home-content">
      <h1 class="home-sr-only">项目</h1>
      <div class="project-home-toolbar">
        <div class="project-search">
          <Search :size="17" aria-hidden="true" /><input
            v-model="search"
            type="search"
            aria-label="搜索项目"
            placeholder="搜索项目…"
            autocomplete="off"
          /><button
            v-if="search"
            class="icon-button small"
            aria-label="清空搜索"
            @click="search = ''"
          >
            <X :size="14" />
          </button>
        </div>
        <button
          class="button primary home-create-button"
          @click="$emit('create')"
        >
          <Plus :size="16" />新建项目
        </button>
      </div>
      <div class="project-home-list" aria-label="项目列表">
        <button
          v-for="project in filteredProjects"
          :key="project.id"
          class="project-home-row"
          :aria-label="`打开项目：${project.name}`"
          :aria-describedby="`description-${project.id} task-${project.id} date-${project.id}`"
          @click="$emit('open', project.id)"
        >
          <FolderClosed
            class="project-home-folder"
            :size="20"
            aria-hidden="true"
          /><span class="project-home-summary"
            ><span class="project-home-name">{{ project.name }}</span
            ><span
              :id="`description-${project.id}`"
              class="project-home-description"
              >{{ project.description || "—" }}</span
            ></span
          ><span
            class="project-home-latest"
            :id="`task-${project.id}`"
            :title="project.lastTask ?? '暂无 Task'"
            ><ListTodo :size="14" aria-hidden="true" /><span
              ><span class="home-sr-only">最近 Task：</span
              >{{ project.lastTask ?? "—" }}</span
            ></span
          ><span class="project-home-date"
            ><CalendarDays :size="14" aria-hidden="true" /><time
              :datetime="project.modifiedAt"
              :id="`date-${project.id}`"
              ><span class="home-sr-only">最近修改：</span
              >{{ project.modifiedAt }}</time
            ></span
          ><ChevronRight
            class="project-home-chevron"
            :size="16"
            aria-hidden="true"
          />
        </button>
      </div>
      <div v-if="!filteredProjects.length" class="project-home-empty">
        <Search :size="24" />
        <h2>未找到项目</h2>
        <button class="text-button" @click="search = ''">清空搜索</button>
      </div>
    </div>
  </main>
</template>
