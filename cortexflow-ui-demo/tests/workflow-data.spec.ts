import { test, expect } from "@playwright/test";
import { mockWorkflows } from "../src/data/mockWorkflows";
import { mockProjects, mockTasks } from "../src/data/mock";

test("each planned Workspace has one current slot and all task references stay in that Workspace", () => {
  expect(new Set(mockWorkflows.map((w) => w.workspaceId)).size).toBe(
    mockWorkflows.length,
  );
  for (const workflow of mockWorkflows) {
    const project = mockProjects.find((p) => p.id === workflow.workspaceId)!;
    expect(project).toBeTruthy();
    expect(
      workflow.slots.filter((s) => s.id === workflow.currentSlotId),
    ).toHaveLength(1);
    expect(
      workflow.slots.find((s) => s.id === workflow.currentSlotId)!.completed,
    ).toBe(false);
    expect(new Set(workflow.slots.map((s) => s.id)).size).toBe(
      workflow.slots.length,
    );
    const refs = workflow.slots.flatMap((s) => s.taskIds);
    expect(new Set(refs).size).toBe(refs.length);
    for (const id of refs) {
      const task = mockTasks.find((t) => t.id === id)!;
      expect(task).toBeTruthy();
      expect(project.conversations.map((c) => c.id)).toContain(
        task.conversationId,
      );
    }
    expect(workflow.slots.at(-1)!.taskIds).toHaveLength(0);
  }
});
