import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";
import AppShell from "./components/AppShell.vue";
import "./styles.css";
import "./project-home.css";
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: AppShell },
    { path: "/project/:projectId", component: AppShell },
    { path: "/project/:projectId/stage/:stageId", component: AppShell },
    {
      path: "/project/:projectId/stage/:stageId/task/:taskId",
      component: AppShell,
    },
    {
      path: "/project/:projectId/stage/:stageId/task/:taskId/execution/:executionId",
      component: AppShell,
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
createApp(AppShell).use(router).mount("#app");
