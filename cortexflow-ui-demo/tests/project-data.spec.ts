import { test, expect } from "@playwright/test";
import { homeProjects } from "../src/data/homeProjects";
import {
  projectDevelopment,
  type DevelopmentNode,
} from "../src/data/projectDevelopment";

test("home records have unique project identity and exactly the requested four display fields", () => {
  expect(homeProjects.map((p) => p.name)).toEqual([
    "CortexFlow",
    "LoongArch-CPU-2027",
    "uxuescript",
    "nuwanda.space",
    "writing_reading",
  ]);
  expect(new Set(homeProjects.map((p) => p.id)).size).toBe(5);
  for (const project of homeProjects) {
    expect(Object.keys(project).sort()).toEqual([
      "description",
      "id",
      "lastTask",
      "modifiedAt",
      "name",
    ]);
    expect(project.modifiedAt).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(project.description.length).toBeGreaterThan(0);
  }
});

test("curated development data is project-specific and does not invent history dates or acceptance", () => {
  const flatten = (nodes: DevelopmentNode[]): DevelopmentNode[] =>
    nodes.flatMap((n) => [n, ...flatten(n.children ?? [])]);
  expect(projectDevelopment.map((p) => p.projectId)).toEqual(
    homeProjects.map((p) => p.id),
  );
  const all = projectDevelopment.flatMap((p) => flatten(p.nodes));
  expect(new Set(all.map((n) => n.id)).size).toBe(all.length);
  for (const tree of projectDevelopment) {
    expect(
      tree.nodes.filter((n) => n.kind === "stage" && n.status === "current"),
    ).toHaveLength(1);
  }
  for (const node of all) {
    if (node.id === "cf-task-home") expect(node.date).toBe("2026-11-24");
    else expect(node.date).toBeNull();
  }
  for (const id of ["cf-phase-a", "cf-phase-b", "cf-phase-c"]) {
    expect(all.find((n) => n.id === id)!.status).toBe("implemented");
  }
});

test("Home stays pinned when many project tabs overflow", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 900 });
  await page.goto("/");
  for (const name of [
    "CortexFlow",
    "LoongArch-CPU-2027",
    "uxuescript",
    "nuwanda.space",
    "writing_reading",
  ]) {
    await page.getByRole("tab", { name: "全部项目", exact: true }).click();
    await page
      .getByRole("button", { name: `打开项目：${name}`, exact: true })
      .click();
  }
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await page.getByRole("button", { name: "新建项目", exact: true }).click();
  await page.getByLabel("项目名称", { exact: true }).fill("第六个项目");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await expect
    .poll(() =>
      page.locator(".project-tabs-strip").evaluate((el) => {
        const home = el.querySelector(".is-home")!.getBoundingClientRect();
        const strip = el.getBoundingClientRect();
        return home.left >= strip.left && home.right <= strip.right;
      }),
    )
    .toBe(true);
  await page.screenshot({ path: "screenshots/project-tabs-narrow.png" });
});
