import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});
test("chat is an explicit unconnected host placeholder", async ({ page }) => {
  await page.getByRole("link", { name: "聊天", exact: true }).click();
  await expect(page.getByText("WebView Integration Placeholder")).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(0);
});
test("conversation switching scopes tasks and supports empty conversations", async ({
  page,
}) => {
  await page.getByRole("button", { name: "选择对话：Agent 接入方案" }).click();
  await expect(
    page.getByRole("button", { name: /验证 dsh 适配器/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /搭建工作台应用外壳/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "选择对话：下一步想法" }).click();
  await expect(page.getByText("这个对话还没有任务")).toBeVisible();
});
test("project tree collapses and project selection changes context", async ({
  page,
}) => {
  await page.getByRole("button", { name: "折叠项目：CortexFlow" }).click();
  await expect(
    page.getByRole("button", { name: "选择对话：工作台界面设计" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "选择项目：卫画助手" }).click();
  await expect(
    page.getByRole("heading", { name: "知识库检索优化", exact: true }),
  ).toBeVisible();
});
test("status filter distinguishes unknown and failed with correct detail", async ({
  page,
}) => {
  await page.getByLabel("筛选任务状态").selectOption("unknown");
  await expect(
    page.getByRole("button", { name: /同步 Agent 执行快照/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /同步 Agent 执行快照/ }).click();
  await expect(
    page
      .getByRole("complementary", { name: "任务详情" })
      .getByText("无法确认执行结果"),
  ).toBeVisible();
  await page.getByLabel("关闭任务详情").click();
  await page.getByLabel("筛选任务状态").selectOption("failed");
  await page.getByRole("button", { name: /验证报告回传格式/ }).click();
  await expect(
    page
      .getByRole("complementary", { name: "任务详情" })
      .getByText("报告格式校验失败"),
  ).toBeVisible();
});
test("agent selection filters tasks and task selection highlights its agent", async ({
  page,
}) => {
  await page.getByRole("button", { name: "筛选 Agent：Claude CLI" }).click();
  await expect(
    page.getByRole("button", { name: /梳理任务详情的信息层级/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /搭建工作台应用外壳/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "清除筛选" }).click();
  await page.getByRole("button", { name: /搭建工作台应用外壳/ }).click();
  await expect(
    page.getByRole("button", { name: "筛选 Agent：Codex CLI" }),
  ).toHaveAttribute("data-linked", "true");
});
test("completed task has seven stages, report and review", async ({ page }) => {
  await page.getByRole("button", { name: /梳理任务详情的信息层级/ }).click();
  await expect(page.locator(".execution-stage")).toHaveCount(7);
  await page.getByRole("button", { name: "预览 Mock 报告" }).click();
  await expect(
    page.getByRole("dialog").getByText("任务详情信息结构"),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
test("create project and conversation are local interactive demos", async ({
  page,
}) => {
  await page.getByLabel("新建项目").click();
  await page.getByLabel("名称", { exact: true }).fill("测试工作空间");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "选择项目：测试工作空间" }),
  ).toBeVisible();
  await page.getByLabel("新建对话", { exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("设计探索");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "设计探索", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("这个对话还没有任务")).toBeVisible();
});
test("search supports a filtered empty state and reset", async ({ page }) => {
  await page.getByLabel("搜索任务").fill("不存在的任务");
  await expect(page.getByText("没有匹配的任务")).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).first().click();
  await expect(
    page.getByRole("button", { name: /搭建工作台应用外壳/ }),
  ).toBeVisible();
});
test("narrow viewport keeps usable detail drawer and no document overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 900 });
  await page.getByRole("button", { name: /搭建工作台应用外壳/ }).click();
  await expect(
    page.getByRole("dialog", { name: "任务详情" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("desktop screenshots and no runtime or console errors", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.goto("/#/work");
  await page.screenshot({ path: "screenshots/work.png" });
  await page.getByRole("button", { name: /搭建工作台应用外壳/ }).click();
  await page.screenshot({ path: "screenshots/task-detail.png" });
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.screenshot({ path: "screenshots/work-1600.png" });
  await page.getByRole("link", { name: "聊天", exact: true }).click();
  await page.screenshot({ path: "screenshots/chat.png" });
  expect(errors).toEqual([]);
});
