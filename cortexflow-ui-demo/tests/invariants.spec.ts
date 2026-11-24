import { test, expect } from "@playwright/test";
import { mockProjects, mockAgents, mockTasks } from "../src/data/mock";

test("mock references and event times are coherent", () => {
  const conversations = mockProjects.flatMap((p) =>
    p.conversations.map((c) => c.id),
  );
  expect(new Set(mockTasks.map((t) => t.id)).size).toBe(mockTasks.length);
  for (const task of mockTasks) {
    expect(conversations).toContain(task.conversationId);
    expect(mockAgents.map((a) => a.id)).toContain(task.agentId);
    expect(task.execution.stages).toHaveLength(7);
    const eventTimes = task.execution.stages
      .filter((s) => s.time !== "—")
      .map((s) => s.time);
    expect(eventTimes).toEqual([...eventTimes].sort());
    expect(eventTimes[0]! >= task.created.slice(11)).toBe(true);
    expect(eventTimes.at(-1)! <= task.updated.slice(11)).toBe(true);
  }
});

test("filtering never leaves an unrelated task selected in execution summary", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("筛选任务状态").selectOption("unknown");
  await expect(
    page.locator(".execution-section .section-heading"),
  ).toContainText("CF-020");
  await expect(
    page.getByRole("button", { name: "筛选 Agent：Claude CLI" }),
  ).toHaveAttribute("data-linked", "true");
  await page.getByLabel("搜索任务").fill("无匹配");
  await expect(page.locator(".execution-section")).toHaveCount(0);
});

test("desktop prototype needs no external service requests", async ({
  page,
}) => {
  const external: string[] = [];
  page.on("request", (r) => {
    if (
      !r.url().startsWith("http://127.0.0.1:5176") &&
      !r.url().startsWith("data:")
    )
      external.push(r.url());
  });
  await page.goto("/");
  await page.getByRole("link", { name: "聊天", exact: true }).click();
  expect(external).toEqual([]);
});
