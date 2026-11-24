import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/#/project/cortex?demo=overview");
});

test("project opens its overview with three demo tabs and only one internal navigation entry", async ({
  page,
}) => {
  await expect(page.getByRole("tab")).toHaveCount(3);
  await expect(
    page.getByRole("tab", { name: "全部项目", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "CortexFlow", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("tab", { name: "LoongArch-CPU-2027", exact: true }),
  ).toHaveAttribute("aria-selected", "false");
  const panel = page.getByRole("tabpanel", { name: "CortexFlow", exact: true });
  await expect(panel.getByRole("navigation")).toContainText("概览");
  await expect(panel.getByRole("navigation").getByRole("button")).toHaveCount(
    1,
  );
  await expect(
    panel.getByRole("heading", { name: "CortexFlow", exact: true }),
  ).toBeVisible();
  await expect(panel.locator(".development-tree")).toBeVisible();
  await expect(
    panel.locator(
      ".task-detail, .workflow-section, .sequence-section, .task-table",
    ),
  ).toHaveCount(0);
});

test("current phase is expanded, history collapses and implementation is not called accepted", async ({
  page,
}) => {
  const history = page.getByRole("button", {
    name: "展开阶段：阶段 A：基础通信设施",
    exact: true,
  });
  await expect(history).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("button", {
      name: "收起阶段：当前工作：CortexFlow 前端 Demo",
      exact: true,
    }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("项目概览设计", { exact: true })).toBeVisible();
  await history.click();
  await expect(
    page.getByText("Supervisor 会话连接", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('[data-node-id="cf-phase-a"] > .development-row'),
  ).toContainText("已实现 · 待验证");
  await page
    .getByRole("button", {
      name: "收起阶段：阶段 A：基础通信设施",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Supervisor 会话连接", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.locator('[data-node-id="cf-task-home"] > .development-row'),
  ).toContainText("2026-11-24");
});

test("projects retain independent expansion and scroll position when tabs switch", async ({
  page,
}) => {
  await page
    .getByRole("button", {
      name: "展开阶段：阶段 C：Agent 执行闭环",
      exact: true,
    })
    .click();
  const cortex = page.getByRole("tabpanel", {
    name: "CortexFlow",
    exact: true,
  });
  const scroll = cortex.locator(".project-overview-scroll");
  await scroll.evaluate((el) => {
    el.scrollTop = 280;
  });
  const before = await scroll.evaluate((el) => el.scrollTop);
  expect(before).toBeGreaterThan(0);
  await page
    .getByRole("tab", { name: "LoongArch-CPU-2027", exact: true })
    .click();
  const loong = page.getByRole("tabpanel", {
    name: "LoongArch-CPU-2027",
    exact: true,
  });
  await expect(loong.locator(".development-tree")).toContainText(
    "验证目标与实现边界",
  );
  await expect(loong.getByText("Bridge 通信", { exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "CortexFlow", exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "收起阶段：阶段 C：Agent 执行闭环",
      exact: true,
    }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect.poll(() => scroll.evaluate((el) => el.scrollTop)).toBe(before);
});

test("future paths are dashed and nodes expose no unrequested deep interaction", async ({
  page,
}) => {
  await expect(page.locator('[data-node-id="cf-future"]')).toHaveClass(
    /node-planned/,
  );
  expect(
    await page
      .locator('[data-node-id="cf-future"]')
      .evaluate((el) => getComputedStyle(el, "::before").borderLeftStyle),
  ).toBe("dashed");
  await expect(
    page.getByText("真实任务闭环验证", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('[data-node-id="cf-task-overview"]').getByRole("button"),
  ).toHaveCount(0);
  await page
    .getByRole("button", {
      name: "展开阶段：阶段 C：Agent 执行闭环",
      exact: true,
    })
    .click();
  await expect(page.getByText("Agent Runtime", { exact: true })).toBeVisible();
});

test("overview screenshots, narrow layout and runtime errors", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.screenshot({ path: "screenshots/project-overview-1440.png" });
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.screenshot({ path: "screenshots/project-overview-1600.png" });
  await page
    .getByRole("button", {
      name: "展开阶段：阶段 C：Agent 执行闭环",
      exact: true,
    })
    .click();
  await page
    .getByRole("tabpanel", { name: "CortexFlow", exact: true })
    .locator(".project-overview-scroll")
    .evaluate((el) => {
      el.scrollTop = 240;
    });
  await page.screenshot({ path: "screenshots/project-overview-expanded.png" });
  await page
    .getByRole("tab", { name: "LoongArch-CPU-2027", exact: true })
    .click();
  await page.screenshot({ path: "screenshots/project-overview-loongarch.png" });
  await page.setViewportSize({ width: 900, height: 900 });
  await page.screenshot({ path: "screenshots/project-overview-narrow.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
