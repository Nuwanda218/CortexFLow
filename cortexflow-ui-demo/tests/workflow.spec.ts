import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("workspace route precedes the message diagram and defaults to the current slot", async ({
  page,
}) => {
  const workflow = page.getByRole("region", { name: "Workspace 工作路线" });
  await expect(workflow).toContainText("完成多 Agent 协作工作台原型");
  await expect(workflow.locator(".workflow-step")).toHaveCount(4);
  await expect(workflow.locator(".workflow-step.is-current")).toContainText(
    "S02",
  );
  await expect(
    page.getByRole("button", { name: "查看阶段 S02：前端工作台设计" }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(workflow.locator(".workflow-slot-detail")).toContainText(
    "2 / 6",
  );
  expect(
    await workflow.evaluate(
      (el) =>
        el.compareDocumentPosition(
          document.querySelector(".sequence-section")!,
        ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ),
  ).toBeTruthy();
});

test("viewing history, collapsing and returning never changes actual current slot", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "查看阶段 S01：领域模型与报告约定" })
    .click();
  await expect(page.locator(".workflow-slot-detail")).toContainText(
    "明确报告与审核分别记录",
  );
  await expect(page.locator(".workflow-step.is-current")).toContainText("S02");
  await expect(page.locator(".workflow-step.is-viewed")).toContainText("S01");
  await page.getByRole("button", { name: "收起阶段详情" }).click();
  await expect(page.locator(".workflow-slot-detail")).toHaveCount(0);
  await page.getByRole("button", { name: "回到当前阶段" }).click();
  await expect(page.locator(".workflow-slot-detail")).toContainText(
    "前端工作台设计",
  );
});

test("stage task references use existing task status and detail, clearing conflicting filters", async ({
  page,
}) => {
  await page.getByLabel("筛选任务状态").selectOption("waiting");
  await expect(
    page.locator('.workflow-task[data-task-id="CF-024"]'),
  ).toHaveAttribute("data-status", "running");
  await page
    .getByRole("button", { name: "查看阶段任务 CF-024", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "任务详情" }),
  ).toContainText("搭建工作台应用外壳");
  await expect(page.getByLabel("筛选任务状态")).toHaveValue("");
  await expect(
    page.locator('.task-row[data-task-id="CF-024"]'),
  ).toHaveAttribute("aria-pressed", "true");
});

test("historical task opens its source conversation while the Workspace workflow is preserved", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "查看阶段 S01：领域模型与报告约定" })
    .click();
  await page
    .getByRole("button", { name: "查看阶段任务 CF-017", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Agent 接入方案", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("complementary", { name: "任务详情" }),
  ).toContainText("定义 CLI 报告契约");
  await expect(page.locator(".workflow-step.is-current")).toContainText("S02");
  await expect(page.locator(".workflow-step.is-viewed")).toContainText("S01");
  await page.getByRole("button", { name: "选择对话：下一步想法" }).click();
  await expect(
    page.getByRole("region", { name: "Workspace 工作路线" }),
  ).toContainText("完成多 Agent 协作工作台原型");
});

test("far future is a direction without invented tasks and future plan edits preserve current stage", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "查看阶段 S04：Core 联调与交付" })
    .click();
  await expect(page.locator(".workflow-slot-detail")).toContainText(
    "具体任务将在进入该阶段后确定。",
  );
  await expect(
    page.locator(".workflow-slot-detail .workflow-task"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "编辑阶段计划" }).click();
  await page
    .getByLabel("阶段目标", { exact: true })
    .fill("验证完整协作链路的可观察性");
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  await expect(page.locator(".workflow-slot-detail")).toContainText(
    "验证完整协作链路的可观察性",
  );
  await expect(page.locator(".workflow-step.is-current")).toContainText("S02");
  await page.getByRole("button", { name: "前移规划阶段" }).click();
  expect(
    await page
      .locator(".workflow-step")
      .evaluateAll((nodes) =>
        nodes.map((n) => n.getAttribute("data-slot-code")),
      ),
  ).toEqual(["S01", "S02", "S04", "S03"]);
});

test("supplementary work creates a new waiting task and retains completed execution history", async ({
  page,
}) => {
  await page.getByRole("button", { name: "新增补充任务" }).click();
  await page.getByLabel("任务名称", { exact: true }).fill("补充键盘操作验证");
  await page
    .getByLabel("简要目标", { exact: true })
    .fill("检查阶段路线和任务详情的键盘切换");
  await page.getByRole("button", { name: "创建补充任务", exact: true }).click();
  const task = page
    .locator(".workflow-task")
    .filter({ hasText: "补充键盘操作验证" });
  await expect(task).toHaveAttribute("data-status", "waiting");
  await expect(
    page.locator('.workflow-task[data-task-id="CF-023"]'),
  ).toHaveAttribute("data-status", "completed");
  await expect(page.locator(".workflow-step.is-current")).toContainText("S02");
  await expect(
    page.getByRole("complementary", { name: "任务详情" }),
  ).toContainText("补充键盘操作验证");
  await page.getByLabel("关闭任务详情").click();
  await page.getByLabel("演示设置").click();
  await page.getByRole("button", { name: "重置演示数据" }).click();
  await expect(page.locator(".workflow-task")).toHaveCount(6);
  await expect(page.locator(".workflow-slot-detail")).toContainText("2 / 6");
});

test("switching Workspace selects its own single workflow and adapts to a narrow window", async ({
  page,
}) => {
  await page.getByRole("button", { name: "选择项目：卫画助手" }).click();
  await expect(
    page.getByRole("region", { name: "Workspace 工作路线" }),
  ).toContainText("建立来源可追溯的年画知识体验");
  await expect(page.locator(".workflow-step.is-current")).toHaveCount(1);
  await page.setViewportSize({ width: 900, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "screenshots/workflow-narrow.png" });
});

test("workflow desktop screenshots and no runtime errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.screenshot({ path: "screenshots/workflow.png" });
  await page
    .getByRole("button", { name: "查看阶段 S01：领域模型与报告约定" })
    .click();
  await page.screenshot({ path: "screenshots/workflow-history.png" });
  await page.getByRole("button", { name: "回到当前阶段" }).click();
  await page
    .getByRole("button", { name: "查看阶段任务 CF-024", exact: true })
    .click();
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.screenshot({ path: "screenshots/workflow-task-detail.png" });
  expect(errors).toEqual([]);
});

test("opening a stage task locates its existing row inside the task table", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "查看阶段任务 CF-020", exact: true })
    .click();
  await expect
    .poll(() =>
      page.locator(".task-table").evaluate((table) => {
        const row = table
          .querySelector('.task-row[data-task-id="CF-020"]')!
          .getBoundingClientRect();
        const header = table
          .querySelector(".task-table-header")!
          .getBoundingClientRect();
        return (
          row.top >= header.bottom &&
          row.bottom <= table.getBoundingClientRect().bottom
        );
      }),
    )
    .toBe(true);
});
