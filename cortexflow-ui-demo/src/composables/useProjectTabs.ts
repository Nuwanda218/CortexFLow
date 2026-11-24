import { computed, ref } from "vue";
import { homeProjects, HOME_MOCK_DATE } from "../data/homeProjects";
import type { HomeProject } from "../data/homeProjects";
const projects = ref<HomeProject[]>(structuredClone(homeProjects));
const openIds = ref<string[]>([]);
const activeId = ref("home");
const search = ref("");
const openProjects = computed(() =>
  openIds.value.flatMap((id) => {
    const project = projects.value.find((p) => p.id === id);
    return project ? [project] : [];
  }),
);
const filteredProjects = computed(() => {
  const query = search.value.trim().toLocaleLowerCase();
  return projects.value.filter((p) =>
    `${p.name} ${p.description}`.toLocaleLowerCase().includes(query),
  );
});
function activate(id: string) {
  if (id === "home") {
    activeId.value = "home";
    return true;
  }
  if (!projects.value.some((p) => p.id === id)) return false;
  if (!openIds.value.includes(id)) openIds.value.push(id);
  activeId.value = id;
  return true;
}
function close(id: string) {
  const index = openIds.value.indexOf(id);
  if (index < 0) return;
  openIds.value.splice(index, 1);
  if (activeId.value === id)
    activeId.value = openIds.value[index] ?? openIds.value[index - 1] ?? "home";
}
function createProject(
  name: string,
  description: string,
): HomeProject | string {
  const trimmed = name.trim();
  if (!trimmed) return "请输入项目名称";
  if (
    projects.value.some(
      (p) => p.name.toLocaleLowerCase() === trimmed.toLocaleLowerCase(),
    )
  )
    return "项目名称已存在";
  const project: HomeProject = {
    id: `project-${crypto.randomUUID()}`,
    name: trimmed,
    description: description.trim(),
    modifiedAt: HOME_MOCK_DATE,
    lastTask: null,
  };
  projects.value.unshift(project);
  activate(project.id);
  return project;
}
export function useProjectTabs() {
  return {
    projects,
    openProjects,
    activeId,
    search,
    filteredProjects,
    activate,
    close,
    createProject,
  };
}
