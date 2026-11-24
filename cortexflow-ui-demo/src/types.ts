export type Status = "waiting" | "running" | "completed" | "failed" | "unknown";
export type ParticipantId = "supervisor" | "core" | "codex" | "claude" | "dsh";
export interface CollaborationMessage {
  id: string;
  taskId: string;
  conversationId: string;
  from: ParticipantId;
  to: ParticipantId;
  time: string;
  order: number;
  label: string;
  body: string;
  kind: "message" | "pending" | "observation";
  status: Status;
}
export interface Conversation {
  id: string;
  title: string;
  description: string;
}
export interface Project {
  id: string;
  name: string;
  description: string;
  initials: string;
  conversations: Conversation[];
}
export interface Agent {
  id: string;
  name: string;
  subtitle: string;
  configured: boolean;
  version: string;
}
export interface Stage {
  name: string;
  status: Status;
  time: string;
  note: string;
}
export interface Execution {
  id: string;
  stages: Stage[];
}
export interface Task {
  id: string;
  conversationId: string;
  title: string;
  description: string;
  agentId: string;
  status: Status;
  created: string;
  updated: string;
  outcome: string;
  report: {
    status: "pending" | "available" | "none";
    title?: string;
    body?: string;
  };
  review: { status: "pending" | "approved" | "none"; note: string };
  execution: Execution;
}

export interface WorkflowSlot {
  id: string;
  code: string;
  title: string;
  goal: string;
  summary: string;
  completed: boolean;
  taskIds: string[];
  constraints: string[];
  plan: string[];
  outcomes: string[];
}
export interface Workflow {
  id: string;
  workspaceId: string;
  goal: string;
  currentSlotId: string;
  slots: WorkflowSlot[];
}
export type SlotKind = "completed" | "current" | "next" | "future";
