import { test, expect } from "@playwright/test";
const demo = "/#/project/cortex?demo=overview";
async function fullScreenshot(
  page: import("@playwright/test").Page,
  name: string,
) {
  const scroll = page
    .getByRole("tabpanel", { name: "CortexFlow", exact: true })
    .locator(".project-overview-scroll");
  const size = await scroll.evaluate((el) => ({
    top: el.scrollTop,
    height: el.scrollHeight,
  }));
  await scroll.evaluate((el) => {
    el.scrollTop = 0;
  });
  const style = await page.addStyleTag({
    content: `.project-app-shell { height:auto!important; overflow:visible!important; } .project-app-pages { flex:none!important; overflow:visible!important; } .project-four-level-shell { height:auto!important; overflow:visible!important; } .workspace-level-body { min-height:${size.height}px!important; overflow:visible!important; } .project-overview-scroll, .development-navigator { overflow:visible!important; }`,
  });
  await page.screenshot({
    path: `screenshots/${name}-full.png`,
    fullPage: true,
  });
  await style.evaluate((el) => el.remove());
  await scroll.evaluate((el, top) => {
    el.scrollTop = top;
  }, size.top);
}
async function stage(page: import("@playwright/test").Page) {
  await page
    .getByRole("button", {
      name: "进入阶段：当前阶段：项目工作空间设计",
      exact: true,
    })
    .click();
}
async function task(page: import("@playwright/test").Page) {
  await stage(page);
  await page
    .getByRole("button", { name: "进入任务：阶段与任务页面设计", exact: true })
    .click();
}
test.beforeEach(async ({ page }) => {
  await page.goto(demo);
});

test("one project tab contains the complete four-level chain and clickable breadcrumbs", async ({
  page,
}) => {
  await expect(page.getByRole("tab")).toHaveCount(3);
  await expect(page.locator(".workspace-breadcrumbs:visible")).toContainText(
    "CortexFlow",
  );
  await stage(page);
  await expect(page.locator(".workspace-stage-page h1")).toHaveText(
    "当前阶段：项目工作空间设计",
  );
  await page
    .getByRole("button", { name: "进入任务：阶段与任务页面设计", exact: true })
    .click();
  await expect(page.locator(".workspace-task-page h1")).toHaveText(
    "阶段与任务页面设计",
  );
  await page
    .getByRole("button", { name: "查看实施：阶段页面原型实现", exact: true })
    .click();
  await expect(
    page.locator(".workspace-execution-page .workspace-page-heading h1"),
  ).toHaveText("阶段页面原型实现");
  await expect(page.getByRole("tab")).toHaveCount(3);
  await page
    .getByRole("navigation", { name: "层级路径" })
    .getByRole("button", { name: "阶段与任务页面设计", exact: true })
    .click();
  await expect(page.locator(".workspace-task-page")).toBeVisible();
  await page
    .getByRole("navigation", { name: "层级路径" })
    .getByRole("button", { name: "CortexFlow", exact: true })
    .click();
  await expect(page.locator(".project-development:visible")).toBeVisible();
});

test("collapse controls do not navigate and unified navigator changes semantic scope", async ({
  page,
}) => {
  await page
    .getByRole("button", {
      name: "收起阶段：当前阶段：项目工作空间设计",
      exact: true,
    })
    .click();
  await expect(page.locator(".project-development:visible")).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "进入任务：阶段与任务页面设计",
      exact: true,
    }),
  ).toHaveCount(0);
  await stage(page);
  const nav = page.getByRole("navigation", { name: "开发导航" });
  await expect(nav.locator('button[aria-current="page"]')).toHaveCount(1);
  await expect(
    nav.getByRole("button", {
      name: "浏览：当前阶段：项目工作空间设计",
      exact: true,
    }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    nav.getByRole("button", { name: "浏览：CortexFlow", exact: true }),
  ).not.toHaveClass(/is-focused/);
  await expect(nav).toContainText("阶段与任务页面设计");
  await nav
    .getByRole("button", { name: "浏览：阶段 A：基础通信设施", exact: true })
    .click();
  await expect(page.locator(".workspace-stage-page h1")).toHaveText(
    "阶段 A：基础通信设施",
  );
  await expect(page.locator(".workspace-stage-page")).toContainText(
    "已实现 · 待验证",
  );
  await nav
    .getByRole("button", {
      name: "浏览：当前阶段：项目工作空间设计",
      exact: true,
    })
    .click();
  await page
    .getByRole("button", { name: "进入任务：阶段与任务页面设计", exact: true })
    .click();
  await expect(nav).toContainText("阶段页面原型实现");
  await expect(nav).not.toContainText("阶段 A：基础通信设施");
});

test("work tasks have independent execution records and Markdown context", async ({
  page,
}) => {
  await task(page);
  await expect(page.locator(".execution-record-row")).toHaveCount(3);
  await expect(page.locator(".task-context-markdown h2")).toContainText([
    "任务目标与需求",
    "已确定的设计",
    "当前进展",
    "遗留问题与下一步",
  ]);
  await page
    .getByRole("navigation", { name: "开发导航" })
    .getByRole("button", { name: "浏览：上下文维护机制集成", exact: true })
    .click();
  await expect(page.locator(".workspace-task-page")).toContainText(
    "暂无实施记录",
  );
  await expect(page.locator(".execution-record-row")).toHaveCount(0);
});

test("execution renders prompt, recorded tools, full report and references", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await task(page);
  await page
    .getByRole("button", { name: "查看实施：阶段页面原型实现", exact: true })
    .click();
  await page
    .getByRole("button", { name: "展开原始工作委托", exact: true })
    .click();
  await expect(page.locator(".execution-prompt")).toContainText(
    "Mock 工作委托",
  );
  await page
    .getByRole("button", { name: "复制原始工作委托", exact: true })
    .click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(
    "Mock 工作委托",
  );
  await expect(page.locator(".execution-process-list li")).toHaveCount(4);
  await expect(page.locator(".execution-report-markdown h2")).toContainText([
    "实现结果",
    "验证记录",
  ]);
  await expect(
    page.locator(".execution-report-markdown pre code"),
  ).toContainText("npm run build");
  await expect(page.locator(".execution-references")).toContainText(
    "Mock 提交",
  );
});

test("missing execution process remains unrecorded even when a report exists", async ({
  page,
}) => {
  await task(page);
  await page
    .getByRole("button", { name: "查看实施：记录复核", exact: true })
    .click();
  await expect(page.getByRole("region", { name: "执行过程" })).toContainText(
    "未记录",
  );
  await expect(page.locator(".execution-process-list li")).toHaveCount(0);
  await expect(page.locator(".execution-report-markdown")).toContainText(
    "Mock 复核报告",
  );
});

test("tab switching restores project level, selected node, scroll and expansion", async ({
  page,
}) => {
  await task(page);
  const scroll = page
    .getByRole("tabpanel", { name: "CortexFlow", exact: true })
    .locator(".project-overview-scroll");
  await scroll.evaluate((el) => {
    el.scrollTop = 220;
  });
  const before = await scroll.evaluate((el) => el.scrollTop);
  await page
    .getByRole("tab", { name: "LoongArch-CPU-2027", exact: true })
    .click();
  await expect(page.locator(".workspace-project-page:visible")).toContainText(
    "LoongArch-CPU-2027",
  );
  await page.getByRole("tab", { name: "CortexFlow", exact: true }).click();
  await expect(page.locator(".workspace-task-page:visible h1")).toHaveText(
    "阶段与任务页面设计",
  );
  await expect.poll(() => scroll.evaluate((el) => el.scrollTop)).toBe(before);
  await page
    .getByRole("navigation", { name: "层级路径" })
    .getByRole("button", { name: "CortexFlow", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "收起阶段：当前阶段：项目工作空间设计",
      exact: true,
    }),
  ).toHaveAttribute("aria-expanded", "true");
});

test("invalid or cross-project descendant routes fall back safely", async ({
  page,
}) => {
  await page.goto("/#/project/loongarch/stage/cf-current/task/cf-task-pages");
  await expect(page.locator(".workspace-project-page:visible")).toContainText(
    "LoongArch-CPU-2027",
  );
  await expect(page.locator(".workspace-task-page:visible")).toHaveCount(0);
});

test("returning to the project restores explicit tree folding rather than changing it on navigation", async ({
  page,
}) => {
  await page
    .getByRole("button", {
      name: "收起阶段：当前阶段：项目工作空间设计",
      exact: true,
    })
    .click();
  await stage(page);
  await page
    .getByRole("navigation", { name: "层级路径" })
    .getByRole("button", { name: "CortexFlow", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "展开阶段：当前阶段：项目工作空间设计",
      exact: true,
    }),
  ).toHaveAttribute("aria-expanded", "false");
});

test("keyboard internal navigation transfers focus to the new content heading", async ({
  page,
}) => {
  const trigger = page.getByRole("button", {
    name: "进入阶段：当前阶段：项目工作空间设计",
    exact: true,
  });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".workspace-stage-page h1")).toBeFocused();
  const next = page.getByRole("button", {
    name: "进入任务：阶段与任务页面设计",
    exact: true,
  });
  await next.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".workspace-task-page h1")).toBeFocused();
});

test("four full page screenshots and narrow navigator are usable without runtime errors", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("response", (response) => {
    if (response.status() >= 400)
      errors.push(`${response.status()} ${response.url()}`);
  });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`${m.text()} (${m.location().url})`);
  });
  await page.screenshot({ path: "screenshots/levels-project.png" });
  await fullScreenshot(page, "levels-project");
  await stage(page);
  await page.screenshot({ path: "screenshots/levels-stage.png" });
  await fullScreenshot(page, "levels-stage");
  await page
    .getByRole("button", { name: "进入任务：阶段与任务页面设计", exact: true })
    .click();
  await page.screenshot({ path: "screenshots/levels-task.png" });
  await fullScreenshot(page, "levels-task");
  await page
    .getByRole("button", { name: "查看实施：阶段页面原型实现", exact: true })
    .click();
  await page.screenshot({ path: "screenshots/levels-execution.png" });
  await page
    .getByRole("button", { name: "展开原始工作委托", exact: true })
    .click();
  await fullScreenshot(page, "levels-execution");
  await page
    .getByRole("button", { name: "收起原始工作委托", exact: true })
    .click();
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.screenshot({ path: "screenshots/levels-execution-1600.png" });
  await page.setViewportSize({ width: 900, height: 900 });
  await page.screenshot({ path: "screenshots/levels-narrow.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
