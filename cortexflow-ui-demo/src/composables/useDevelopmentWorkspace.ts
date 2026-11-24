import {
  computed,
  inject,
  nextTick,
  provide,
  ref,
  watch,
  type InjectionKey,
  type Ref,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import type { HomeProject } from "../data/homeProjects";
import {
  projectTree,
  indexTree,
  stageById,
  taskById,
  executionById,
  focusPath,
  type WorkspaceFocus,
} from "../data/workspaceGraph";

function createWorkspace(
  project: HomeProject,
  scroll: Ref<HTMLElement | undefined>,
) {
  const route = useRoute(),
    router = useRouter();
  const focus = ref<WorkspaceFocus>({ level: "project", id: project.id });
  const projectNodes = projectTree(project.id);
  const expandedIds = ref(
    new Set(projectNodes.filter((n) => n.defaultExpanded).map((n) => n.id)),
  );
  const scrollPositions = new Map<string, number>();
  const focusKey = computed(() => `${focus.value.level}:${focus.value.id}`);
  const execution = computed(() =>
    focus.value.level === "execution"
      ? executionById(focus.value.id)
      : undefined,
  );
  const task = computed(() =>
    execution.value
      ? taskById(execution.value.taskId)
      : focus.value.level === "task"
        ? taskById(focus.value.id)
        : undefined,
  );
  const stage = computed(() =>
    task.value
      ? stageById(task.value.stageId)
      : focus.value.level === "stage"
        ? stageById(focus.value.id)
        : undefined,
  );
  const tree = computed(
    () =>
      indexTree(project.id) ?? {
        id: project.id,
        level: "project" as const,
        title: project.name,
        status: "planned" as const,
        children: [],
      },
  );
  const breadcrumbs = computed(() => [
    { level: "project" as const, id: project.id, title: project.name },
    ...(stage.value
      ? [
          {
            level: "stage" as const,
            id: stage.value.id,
            title: stage.value.title,
          },
        ]
      : []),
    ...(task.value
      ? [{ level: "task" as const, id: task.value.id, title: task.value.title }]
      : []),
    ...(execution.value
      ? [
          {
            level: "execution" as const,
            id: execution.value.id,
            title: execution.value.title,
          },
        ]
      : []),
  ]);
  function toggle(id: string) {
    const next = new Set(expandedIds.value);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    expandedIds.value = next;
  }
  function navigate(target: WorkspaceFocus) {
    void router.push(focusPath(project.id, target));
  }
  function home() {
    void router.push("/");
  }
  watch(
    () => route.fullPath,
    async () => {
      if (route.params.projectId !== project.id) return;
      const sid =
        typeof route.params.stageId === "string" ? route.params.stageId : "";
      const tid =
        typeof route.params.taskId === "string" ? route.params.taskId : "";
      const eid =
        typeof route.params.executionId === "string"
          ? route.params.executionId
          : "";
      const s = sid ? stageById(sid) : undefined,
        t = tid ? taskById(tid) : undefined,
        e = eid ? executionById(eid) : undefined;
      if (
        (sid && s?.projectId !== project.id) ||
        (tid && (!s || t?.stageId !== sid)) ||
        (eid && (!t || e?.taskId !== tid))
      ) {
        void router.replace(`/project/${project.id}`);
        return;
      }
      const next: WorkspaceFocus = eid
        ? { level: "execution", id: eid }
        : tid
          ? { level: "task", id: tid }
          : sid
            ? { level: "stage", id: sid }
            : { level: "project", id: project.id };
      const key = `${next.level}:${next.id}`;
      if (key === focusKey.value) return;
      scrollPositions.set(focusKey.value, scroll.value?.scrollTop ?? 0);
      focus.value = next;
      await nextTick();
      if (focusKey.value === key && scroll.value) {
        scroll.value.scrollTop = scrollPositions.get(key) ?? 0;
        const heading = scroll.value.querySelector<HTMLElement>("h1");
        heading?.setAttribute("tabindex", "-1");
        heading?.focus({ preventScroll: true });
      }
    },
    { immediate: true },
  );
  return {
    project,
    focus,
    focusKey,
    projectNodes,
    expandedIds,
    tree,
    breadcrumbs,
    stage,
    task,
    execution,
    toggle,
    navigate,
    home,
  };
}
type DevelopmentWorkspace = ReturnType<typeof createWorkspace>;
const key: InjectionKey<DevelopmentWorkspace> = Symbol("DevelopmentWorkspace");
export function provideDevelopmentWorkspace(
  project: HomeProject,
  scroll: Ref<HTMLElement | undefined>,
) {
  const state = createWorkspace(project, scroll);
  provide(key, state);
  return state;
}
export function useDevelopmentWorkspace() {
  const state = inject(key);
  if (!state) throw new Error("Development workspace provider is missing");
  return state;
}
