import { test, expect } from "@playwright/test";

test("narrow detail drawer receives and contains keyboard focus then restores it", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 900 });
  await page.goto("/");
  const trigger = page.getByRole("button", { name: /搭建工作台应用外壳/ });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("关闭任务详情")).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  expect(
    await page
      .locator(".task-detail")
      .evaluate((el) => el.contains(document.activeElement)),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.locator(".task-detail")).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("create dialog traps focus and rejects whitespace names", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("新建项目", { exact: true }).click();
  await expect(page.getByLabel("名称", { exact: true })).toBeFocused();
  await page.getByLabel("名称", { exact: true }).fill("   ");
  await expect(
    page.getByRole("button", { name: "创建", exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("新建项目", { exact: true })).toBeFocused();
});
