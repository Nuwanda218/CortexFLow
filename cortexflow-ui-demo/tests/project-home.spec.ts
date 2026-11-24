import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("home shows five project rows without the old navigation or dashboard decorations", async ({
  page,
}) => {
  await expect(
    page.getByRole("tab", { name: "全部项目", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".project-home-row")).toHaveCount(5);
  for (const name of [
    "CortexFlow",
    "LoongArch-CPU-2027",
    "uxuescript",
    "nuwanda.space",
    "writing_reading",
  ]) {
    await expect(
      page.getByRole("button", { name: `打开项目：${name}`, exact: true }),
    ).toBeVisible();
  }
  await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(0);
  await expect(
    page.getByRole("complementary", { name: "项目导航" }),
  ).toHaveCount(0);
  await expect(
    page.locator(".status-badge, progress, meter, .mock-pill"),
  ).toHaveCount(0);
  const row = page.locator(".project-home-row").first();
  await expect(row.locator(".project-home-name")).toHaveText("CortexFlow");
  await expect(row.locator(".project-home-description")).not.toBeEmpty();
  await expect(row.locator("time")).toHaveAttribute("datetime", "2026-11-24");
  await expect(row.locator(".project-home-latest")).toContainText(
    "整理桌面客户端结构",
  );
});

test("search filters projects and offers a clear empty result", async ({
  page,
}) => {
  await page.getByRole("searchbox", { name: "搜索项目" }).fill("LOONG");
  await expect(page.locator(".project-home-row")).toHaveCount(1);
  await page.getByRole("searchbox", { name: "搜索项目" }).fill("不存在的项目");
  await expect(page.getByText("未找到项目", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "清空搜索", exact: true })
    .first()
    .click();
  await expect(page.locator(".project-home-row")).toHaveCount(5);
});

test("projects open separate internal tabs and an already open project is focused once", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  await expect(
    page.getByRole("tab", { name: "CortexFlow", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await page
    .getByRole("button", { name: "打开项目：LoongArch-CPU-2027", exact: true })
    .click();
  await expect(page.getByRole("tab")).toHaveCount(3);
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  await expect(page.getByRole("tab")).toHaveCount(3);
  await expect(
    page.getByRole("tab", { name: "CortexFlow", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("tabpanel", { name: "CortexFlow", exact: true }),
  ).toBeVisible();
});

test("switching retains project page instances and the home search", async ({
  page,
}) => {
  await page.getByRole("searchbox", { name: "搜索项目" }).fill("Cortex");
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  const panel = await page
    .getByRole("tabpanel", { name: "CortexFlow", exact: true })
    .elementHandle();
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await expect(page.getByRole("searchbox", { name: "搜索项目" })).toHaveValue(
    "Cortex",
  );
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  expect(
    await panel!.evaluate((el) => el === document.getElementById(el.id)),
  ).toBe(true);
});

test("closing a background tab keeps the active page, closing the last project returns home", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await page
    .getByRole("button", { name: "打开项目：uxuescript", exact: true })
    .click();
  await page
    .getByRole("button", { name: "关闭项目：CortexFlow", exact: true })
    .click();
  await expect(
    page.getByRole("tab", { name: "uxuescript", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page
    .getByRole("button", { name: "关闭项目：uxuescript", exact: true })
    .click();
  await expect(page.getByRole("tab")).toHaveCount(1);
  await expect(
    page.getByRole("tab", { name: "全部项目", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("button", { name: "关闭项目：首页" }),
  ).toHaveCount(0);
});

test("keyboard tab navigation and Delete keep home permanently available", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  await page
    .getByRole("tab", { name: "CortexFlow", exact: true })
    .press("ArrowLeft");
  await expect(
    page.getByRole("tab", { name: "全部项目", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Delete");
  await expect(page.getByRole("tab")).toHaveCount(2);
  await page.keyboard.press("End");
  await expect(
    page.getByRole("tab", { name: "CortexFlow", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Delete");
  await expect(
    page.getByRole("tab", { name: "全部项目", exact: true }),
  ).toBeFocused();
  await expect(page.getByRole("tab")).toHaveCount(1);
});

test("opening a project moves keyboard focus out of the hidden home panel", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  await expect(
    page.getByRole("tab", { name: "CortexFlow", exact: true }),
  ).toBeFocused();
});

test("new project opens a tab and duplicate names are rejected", async ({
  page,
}) => {
  await page.getByRole("button", { name: "新建项目", exact: true }).click();
  await page.getByLabel("项目名称", { exact: true }).fill("CortexFlow");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("项目名称已存在");
  await page.getByLabel("项目名称", { exact: true }).fill("新的项目");
  await page
    .getByLabel("项目概况", { exact: true })
    .fill("一个用于验证的项目。");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "新的项目", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await expect(page.locator(".project-home-row")).toHaveCount(6);
  await expect(
    page.getByRole("button", { name: "打开项目：新的项目", exact: true }),
  ).toContainText("一个用于验证的项目。");
});

test("legacy routes redirect to home and project routes open their matching tab", async ({
  page,
}) => {
  await page.goto("/#/work");
  await expect(
    page.getByRole("tab", { name: "全部项目", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.goto("/#/project/loongarch");
  await expect(
    page.getByRole("tab", { name: "LoongArch-CPU-2027", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.goto("/#/project/unknown");
  await expect(
    page.getByRole("tab", { name: "全部项目", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
});

test("renders desktop and narrow screenshots without external requests or runtime errors", async ({
  page,
}) => {
  const errors: string[] = [],
    external: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("request", (r) => {
    if (
      !r.url().startsWith("http://127.0.0.1:5176") &&
      !r.url().startsWith("data:")
    )
      external.push(r.url());
  });
  await page.goto("/");
  await page.screenshot({ path: "screenshots/project-home-1440.png" });
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.screenshot({ path: "screenshots/project-home-1600.png" });
  await page
    .getByRole("button", { name: "打开项目：CortexFlow", exact: true })
    .click();
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await page
    .getByRole("button", { name: "打开项目：LoongArch-CPU-2027", exact: true })
    .click();
  await page.screenshot({ path: "screenshots/project-tabs.png" });
  await page.getByRole("tab", { name: "全部项目", exact: true }).click();
  await page.setViewportSize({ width: 900, height: 900 });
  await page.screenshot({ path: "screenshots/project-home-narrow.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
});
