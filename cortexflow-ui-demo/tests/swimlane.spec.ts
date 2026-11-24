import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("work uses five chronological participant lanes with explicit Mock messages", async ({
  page,
}) => {
  await expect(
    page.getByRole("heading", { name: "协作消息时序", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".sequence-participant")).toHaveCount(5);
  const rows = page.locator(".sequence-message");
  expect(await rows.count()).toBeGreaterThan(10);
  const times = await rows.evaluateAll((nodes) =>
    nodes.map((n) => n.getAttribute("data-time")!),
  );
  expect(times).toEqual([...times].sort());
  await expect(page.locator(".sequence-section")).toContainText("Mock 消息");
});

test("task scope and message selection link the diagram to task detail", async ({
  page,
}) => {
  await page.getByRole("button", { name: "仅查看选中任务的消息" }).click();
  const rows = page.locator(".sequence-message");
  expect(await rows.count()).toBeGreaterThan(0);
  expect(
    await rows.evaluateAll((nodes) => [
      ...new Set(nodes.map((n) => n.getAttribute("data-task"))),
    ]),
  ).toEqual(["CF-024"]);
  await page.getByRole("button", { name: "查看消息：CF-024 指令下发" }).click();
  await expect(
    page.getByRole("complementary", { name: "任务详情" }),
  ).toContainText("搭建工作台应用外壳");
  await expect(page.locator(".sequence-message-preview")).toContainText(
    "Supervisor",
  );
  await page.screenshot({ path: "screenshots/swimlane-message.png" });
  await page.getByLabel("关闭任务详情").click();
  await page
    .getByRole("button", { name: /CF-023 梳理任务详情的信息层级/ })
    .click();
  expect(
    await rows.evaluateAll((nodes) => [
      ...new Set(nodes.map((n) => n.getAttribute("data-task"))),
    ]),
  ).toEqual(["CF-023"]);
  await expect(
    page.getByRole("button", { name: "筛选 Agent：Claude CLI" }),
  ).toHaveAttribute("data-linked", "true");
});

test("waiting arrows are unsent and unknown is a local observation", async ({
  page,
}) => {
  await page.getByLabel("筛选任务状态").selectOption("waiting");
  await expect(
    page.locator('.sequence-message[data-kind="pending"]'),
  ).toContainText("待派发 · 未发送");
  await page.getByLabel("筛选任务状态").selectOption("unknown");
  const note = page.locator('.sequence-message[data-kind="observation"]');
  await expect(note).toContainText("状态未确认");
  await expect(note.locator("line.message-arrow")).toHaveCount(0);
});

test("agent filter and empty conversation also scope message records", async ({
  page,
}) => {
  await page.getByRole("button", { name: "筛选 Agent：Claude CLI" }).click();
  const tasks = await page
    .locator(".sequence-message")
    .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-task")));
  expect(tasks.every((id) => id === "CF-023" || id === "CF-020")).toBe(true);
  await page.getByRole("button", { name: "选择对话：下一步想法" }).click();
  await expect(page.getByText("这个对话还没有消息记录")).toBeVisible();
  await expect(page.locator(".sequence-message")).toHaveCount(0);
});

test("lane canvas scrolls internally in narrow windows", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 900 });
  await expect(page.locator(".task-table-header .created-column")).toBeHidden();
  expect(
    await page
      .locator(".sequence-scroll")
      .evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "screenshots/swimlane-narrow.png" });
});

test("selecting an earlier task brings its messages into the internal viewport", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: /CF-020 同步 Agent 执行快照/ })
    .click();
  await expect
    .poll(() =>
      page.locator(".sequence-scroll").evaluate((el) => {
        const rows = el.querySelectorAll(".sequence-message.is-task");
        const row = rows[rows.length - 1]!.getBoundingClientRect();
        const header = el
          .querySelector(".sequence-participants")!
          .getBoundingClientRect();
        const frame = el.getBoundingClientRect();
        return row.top >= header.bottom && row.bottom <= frame.bottom;
      }),
    )
    .toBe(true);
});
