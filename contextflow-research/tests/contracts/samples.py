"""Small immutable contract examples, not business module substitutes."""

from contextflow_core import contracts as c

NOW = "2026-11-06T00:00:00Z"
MESSAGE_ID = c.MessageId("cfmsg_" + "a" * 32)
TASK_ID = c.TaskId("cftask_" + "b" * 32)
DELIVERY_ID = c.DeliveryId("cfdel_" + "c" * 32)
REPORT_MESSAGE_ID = c.MessageId("cfmsg_" + "d" * 32)
ENDPOINT_ID = c.EndpointId("cfep_" + "e" * 32)
AGENT_ID = c.AgentId("codex-local")


def supervisor_dict():
    return {
        "schema_version": 1,
        "submission_id": "sub_" + "f" * 32,
        "source": {
            "adapter": "chatgpt_web",
            "endpoint_id": str(ENDPOINT_ID),
            "native_session_id": "native-session-1",
            "message_id": "native-assistant-1",
            "user_message_id": None,
            "turn_group_id": None,
        },
        "content": {"format": "markdown", "text": "请 Codex 修复 Adapter 并运行测试。"},
    }


def report_dict(kind="progress", outcome=None):
    return {
        "schema_version": 1,
        "report_id": "agent-report-1",
        "task_id": str(TASK_ID),
        "kind": kind,
        "outcome": outcome,
        "content": {"format": "markdown", "text": "Agent 的原始报告"},
    }


def supervisor_message(**overrides):
    fields = {
        "message_id": MESSAGE_ID,
        "kind": c.MessageKind.SUPERVISOR_INSTRUCTION,
        "envelope": c.SupervisorEnvelope.from_dict(supervisor_dict()),
        "received_at": NOW,
    }
    return c.Message(**{**fields, **overrides})


def report_message(**overrides):
    fields = {
        "message_id": REPORT_MESSAGE_ID,
        "kind": c.MessageKind.AGENT_REPORT,
        "envelope": c.AgentReportEnvelope.from_dict(report_dict()),
        "source_agent_id": AGENT_ID,
        "received_at": NOW,
    }
    return c.Message(**{**fields, **overrides})


def instruction(target="codex", content="修复 Adapter 并运行测试"):
    return c.ParsedInstruction(target_agent=target, content=content)


def parsed(*instructions):
    return c.ParseResult(
        status=c.ParseStatus.PARSED,
        instructions=tuple(instructions) or (instruction(),),
    )


def task(**overrides):
    fields = {
        "task_id": TASK_ID,
        "source_message_id": MESSAGE_ID,
        "instruction_index": 0,
        "target_agent_id": AGENT_ID,
        "content": "修复 Adapter 并运行测试",
        "state": c.TaskState.PENDING_DELIVERY,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return c.Task(**{**fields, **overrides})


def task_delivery(**overrides):
    fields = {
        "delivery_id": DELIVERY_ID,
        "subject_id": TASK_ID,
        "target_id": AGENT_ID,
        "state": c.DeliveryState.PENDING,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return c.TaskDelivery(**{**fields, **overrides})


def report_delivery(**overrides):
    fields = {
        "delivery_id": c.DeliveryId("cfdel_" + "d" * 32),
        "subject_id": REPORT_MESSAGE_ID,
        "target_id": ENDPOINT_ID,
        "state": c.DeliveryState.PENDING,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return c.ReportDelivery(**{**fields, **overrides})
