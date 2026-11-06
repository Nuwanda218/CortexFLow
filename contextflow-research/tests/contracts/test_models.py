from dataclasses import FrozenInstanceError, replace
import unittest

from contextflow_core import contracts as c
from contextflow_core.errors import ContextFlowError
from contextflow_core.submission import validate_envelope
from tests.contracts.samples import (
    AGENT_ID,
    DELIVERY_ID,
    ENDPOINT_ID,
    MESSAGE_ID,
    NOW,
    REPORT_MESSAGE_ID,
    TASK_ID,
    instruction,
    parsed,
    report_delivery,
    report_dict,
    report_message,
    supervisor_dict,
    supervisor_message,
    task,
    task_delivery,
)


class ContractCase(unittest.TestCase):
    def assert_invalid(self, build):
        with self.assertRaises(ContextFlowError) as caught:
            build()
        self.assertEqual(caught.exception.code, "INVALID_CONTRACT")
        self.assertIn("field", caught.exception.details)


class EnvelopeTests(ContractCase):
    def test_supervisor_round_trip_uses_existing_v1_validation(self):
        raw = supervisor_dict()
        envelope = c.SupervisorEnvelope.from_dict(raw)
        self.assertEqual(envelope.to_dict(), validate_envelope(raw))
        self.assertIsInstance(envelope.source.message_id, c.NativeMessageId)
        self.assertIsInstance(envelope.source.native_session_id, c.NativeSessionId)
        self.assertIsInstance(envelope.source.endpoint_id, c.EndpointId)
        self.assertEqual(envelope.schema_version, 1)

    def test_supervisor_snapshot_is_not_aliased_to_wire_input(self):
        raw = supervisor_dict()
        envelope = c.SupervisorEnvelope.from_dict(raw)
        raw["content"]["text"] = "modified input"
        exported = envelope.to_dict()
        exported["source"]["message_id"] = "modified output"
        self.assertNotEqual(envelope.content.text, "modified input")
        self.assertEqual(str(envelope.source.message_id), "native-assistant-1")
        with self.assertRaises(FrozenInstanceError):
            envelope.content.text = "changed"

    def test_missing_nullable_key_is_still_rejected_by_existing_v1(self):
        raw = supervisor_dict()
        del raw["source"]["user_message_id"]
        with self.assertRaises(ContextFlowError) as caught:
            c.SupervisorEnvelope.from_dict(raw)
        self.assertEqual(caught.exception.code, "INVALID_ENVELOPE")

    def test_supervisor_unknown_fields_and_wrong_versions_are_rejected(self):
        for change in ({"schema_version": True}, {"schema_version": 2}, {"task_id": str(TASK_ID)}):
            with self.subTest(change=change):
                with self.assertRaises(ContextFlowError) as caught:
                    c.SupervisorEnvelope.from_dict({**supervisor_dict(), **change})
                self.assertEqual(caught.exception.code, "INVALID_ENVELOPE")

    def test_native_source_field_rejects_a_core_message_id(self):
        source = c.SupervisorEnvelope.from_dict(supervisor_dict()).source
        self.assert_invalid(lambda: replace(source, message_id=MESSAGE_ID))

    def test_content_preserves_body_and_rejects_empty_or_non_markdown_text(self):
        text = "  原文\n\n```python\nprint(1)\n```  "
        self.assertEqual(c.Content(text=text).text, text)
        for fields in ({"text": " "}, {"text": 1}, {"text": "ok", "format": "html"}):
            with self.subTest(fields=fields):
                self.assert_invalid(lambda: c.Content(**fields))

    def test_each_report_kind_has_the_correct_outcome_and_return_rule(self):
        for kind, outcome, requires_return in (
            ("progress", None, False),
            ("question", None, True),
            ("blocked", None, True),
            ("final", "completed", True),
            ("final", "failed", True),
        ):
            with self.subTest(kind=kind, outcome=outcome):
                raw = report_dict(kind, outcome)
                envelope = c.AgentReportEnvelope.from_dict(raw)
                self.assertEqual(envelope.to_dict(), raw)
                self.assertEqual(envelope.requires_return, requires_return)

    def test_report_invalid_kind_outcome_and_field_combinations_are_rejected(self):
        invalid = [
            report_dict("final", None),
            report_dict("final", "blocked"),
            report_dict("progress", "completed"),
            report_dict("question", "failed"),
            report_dict("blocked", "completed"),
            report_dict("unknown", None),
            {**report_dict(), "schema_version": True},
            {**report_dict(), "source_agent_id": str(AGENT_ID)},
            {key: value for key, value in report_dict().items() if key != "outcome"},
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assert_invalid(lambda: c.AgentReportEnvelope.from_dict(raw))

    def test_report_constructor_also_enforces_identity_and_enum_types(self):
        report = c.AgentReportEnvelope.from_dict(report_dict())
        for change in ({"task_id": MESSAGE_ID}, {"kind": "progress"}, {"report_id": AGENT_ID}):
            with self.subTest(change=change):
                self.assert_invalid(lambda: replace(report, **change))


class MessageTests(ContractCase):
    def test_core_and_native_message_identities_remain_separate(self):
        message = supervisor_message()
        self.assertIsInstance(message.message_id, c.MessageId)
        self.assertIsInstance(message.envelope.source.message_id, c.NativeMessageId)
        self.assertIsNone(message.source_agent_id)
        self.assertEqual(message.processing_state, c.ProcessingState.PENDING)

    def test_report_identity_is_supplied_outside_the_envelope(self):
        message = report_message()
        self.assertEqual(message.source_agent_id, AGENT_ID)
        self.assertNotIn("source_agent_id", message.envelope.to_dict())
        self.assert_invalid(lambda: report_message(source_agent_id=None))
        self.assert_invalid(lambda: report_message(source_agent_id=ENDPOINT_ID))

    def test_message_kind_must_match_its_envelope(self):
        self.assert_invalid(lambda: supervisor_message(kind=c.MessageKind.AGENT_REPORT))
        self.assert_invalid(lambda: report_message(kind=c.MessageKind.SUPERVISOR_INSTRUCTION))
        self.assert_invalid(lambda: supervisor_message(source_agent_id=AGENT_ID))
        self.assert_invalid(lambda: supervisor_message(envelope=supervisor_dict()))

    def test_processing_state_and_error_combinations_are_validated(self):
        error = c.ErrorInfo(code="TARGET_UNRESOLVED", message="No explicit agent")
        blocked = supervisor_message(processing_state=c.ProcessingState.BLOCKED, processing_error=error)
        self.assertEqual(blocked.processing_error.code, "TARGET_UNRESOLVED")
        self.assert_invalid(lambda: supervisor_message(processing_state=c.ProcessingState.BLOCKED))
        self.assert_invalid(lambda: supervisor_message(processing_state=c.ProcessingState.PROCESSED, processing_error=error))

    def test_timestamps_must_be_utc_and_records_are_immutable(self):
        self.assert_invalid(lambda: supervisor_message(received_at="2026-11-06T00:00:00"))
        self.assert_invalid(lambda: task(updated_at="2026-11-06T00:00:00+08:00"))
        with self.assertRaises(FrozenInstanceError):
            supervisor_message().message_id = REPORT_MESSAGE_ID


class ParseResultTests(ContractCase):
    def test_parsed_requires_a_complete_nonempty_tuple_and_no_error(self):
        error = c.ErrorInfo(code="PARSE_FAILED", message="Bad model output")
        for fields in (
            {"status": c.ParseStatus.PARSED},
            {"status": c.ParseStatus.PARSED, "instructions": (instruction(),), "error": error},
            {"status": c.ParseStatus.PARSED, "instructions": [instruction()]},
            {"status": "parsed", "instructions": (instruction(),)},
        ):
            with self.subTest(fields=fields):
                self.assert_invalid(lambda: c.ParseResult(**fields))
        self.assertEqual(len(parsed().instructions), 1)

    def test_unresolved_and_failed_cannot_leak_dispatchable_instructions(self):
        error = c.ErrorInfo(code="TARGET_UNRESOLVED", message="No explicit agent")
        #错误信息
        for status in (c.ParseStatus.UNRESOLVED, c.ParseStatus.FAILED):
            with self.subTest(status=status):
                result = c.ParseResult(status=status, error=error)
                self.assertEqual(result.instructions, ())
                self.assertIsNone(result.intent_fingerprint)
                self.assert_invalid(lambda: c.ParseResult(status=status))
                self.assert_invalid(lambda: c.ParseResult(status=status, error=error, instructions=(instruction(),)))

    def test_instruction_rejects_an_empty_target_or_body(self):
        self.assert_invalid(lambda: instruction(target=" "))
        self.assert_invalid(lambda: instruction(content=" "))
        self.assert_invalid(lambda: instruction(target=123))

    def test_intent_fingerprint_is_stable_and_sensitive_to_all_instruction_changes(self):
        first = parsed(instruction("codex", "code"), instruction("docs", "docs"))
        same = parsed(instruction("codex", "code"), instruction("docs", "docs"))
        self.assertEqual(first.intent_fingerprint, same.intent_fingerprint)
        self.assertRegex(first.intent_fingerprint, r"^[0-9a-f]{64}$")
        for changed in (
            parsed(instruction("other", "code"), instruction("docs", "docs")),
            parsed(instruction("codex", "changed"), instruction("docs", "docs")),
            parsed(instruction("docs", "docs"), instruction("codex", "code")),
            parsed(instruction("codex", "code")),
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual(first.intent_fingerprint, changed.intent_fingerprint)


class TaskAndDeliveryTests(ContractCase):
    def test_task_terminal_state_requires_a_final_report_message(self):
        for state in (c.TaskState.COMPLETED, c.TaskState.FAILED):
            with self.subTest(state=state):
                final = task(state=state, final_report_message_id=REPORT_MESSAGE_ID)
                self.assertEqual(final.final_report_message_id, REPORT_MESSAGE_ID)
                self.assert_invalid(lambda: task(state=state))
        self.assert_invalid(lambda: task(final_report_message_id=REPORT_MESSAGE_ID))
        self.assert_invalid(lambda: task(state=c.TaskState.COMPLETED, final_report_message_id=c.ReportId("report-1")))

    def test_task_index_and_identity_constraints_are_enforced(self):
        for changes in (
            {"instruction_index": -1},
            {"instruction_index": True},
            {"target_agent_id": ENDPOINT_ID},
            {"source_message_id": c.NativeMessageId("native-1")},
            {"content": " "},
        ):
            with self.subTest(changes=changes):
                self.assert_invalid(lambda: task(**changes))

    def test_task_and_report_deliveries_have_disjoint_target_types(self):
        dispatch = task_delivery()
        returned = report_delivery()
        self.assertEqual(dispatch.purpose, c.DeliveryPurpose.TASK_DISPATCH)
        self.assertEqual(returned.purpose, c.DeliveryPurpose.REPORT_RETURN)
        self.assert_invalid(lambda: task_delivery(target_id=ENDPOINT_ID))
        self.assert_invalid(lambda: task_delivery(subject_id=REPORT_MESSAGE_ID))
        self.assert_invalid(lambda: report_delivery(target_id=AGENT_ID))
        self.assert_invalid(lambda: report_delivery(subject_id=TASK_ID))
        self.assert_invalid(lambda: task_delivery(target_id=str(AGENT_ID)))

    def test_confirmed_delivery_does_not_complete_a_task(self):
        dispatched = task(state=c.TaskState.DISPATCHED)
        delivery = task_delivery(state=c.DeliveryState.CONFIRMED, evidence={"task_id": str(TASK_ID), "status": "accepted"})
        self.assertEqual(dispatched.state, c.TaskState.DISPATCHED)
        self.assertIsNone(dispatched.final_report_message_id)
        self.assertEqual(delivery.state, c.DeliveryState.CONFIRMED)
        self.assertFalse(delivery.can_attempt_send)

    def test_unknown_cannot_send_even_when_the_underlying_error_is_retryable(self):
        error = c.ErrorInfo(code="BRIDGE_TIMEOUT", message="No receipt", retryable=True)
        unknown = task_delivery(state=c.DeliveryState.UNKNOWN, error=error)
        self.assertTrue(unknown.error.retryable)
        self.assertFalse(unknown.can_attempt_send)
        self.assertFalse(replace(unknown, delivery_id=c.DeliveryId("cfdel_" + "f" * 32)).can_attempt_send)
        self.assertTrue(task_delivery().can_attempt_send)

    def test_sending_failed_and_confirmed_are_not_sendable(self):
        error = c.ErrorInfo(code="AGENT_REJECTED", message="Rejected")
        for fields in (
            {"state": c.DeliveryState.SENDING},
            {"state": c.DeliveryState.FAILED, "error": error},
            {"state": c.DeliveryState.CONFIRMED, "evidence": {"accepted": True}},
        ):
            with self.subTest(fields=fields):
                self.assertFalse(task_delivery(**fields).can_attempt_send)

    def test_delivery_result_error_and_evidence_combinations_are_validated(self):
        error = c.ErrorInfo(code="SEND_UNVERIFIED", message="Unknown")
        for fields in (
            {"state": c.DeliveryState.FAILED},
            {"state": c.DeliveryState.UNKNOWN},
            {"state": c.DeliveryState.CONFIRMED},
            {"state": c.DeliveryState.CONFIRMED, "error": error, "evidence": {"verified": True}},
            {"state": "pending"},
        ):
            with self.subTest(fields=fields):
                self.assert_invalid(lambda: task_delivery(**fields))

    def test_evidence_is_a_deeply_immutable_json_snapshot(self):
        evidence = {"receipt": {"ids": ["one"]}}
        delivery = report_delivery(state=c.DeliveryState.CONFIRMED, evidence=evidence)
        evidence["receipt"]["ids"].append("two")
        self.assertEqual(delivery.evidence["receipt"]["ids"], ("one",))
        with self.assertRaises(TypeError):
            delivery.evidence["receipt"]["extra"] = True
        self.assert_invalid(lambda: task_delivery(evidence={"bad": object()}))


class ResultTests(ContractCase):
    def test_acceptance_requires_a_core_message_id_and_duplicate_only_on_acceptance(self):
        accepted = c.AcceptResult(status=c.AcceptStatus.ACCEPTED, message_id=MESSAGE_ID, duplicate=True)
        self.assertTrue(accepted.duplicate)
        error = c.ErrorInfo(code="SUBMISSION_CONFLICT", message="Different envelope")
        for fields in (
            {"status": c.AcceptStatus.ACCEPTED},
            {"status": c.AcceptStatus.ACCEPTED, "message_id": TASK_ID},
            {"status": c.AcceptStatus.ACCEPTED, "message_id": MESSAGE_ID, "error": error},
            {"status": c.AcceptStatus.REJECTED},
            {"status": c.AcceptStatus.CONFLICT, "error": error, "duplicate": True},
            {"status": c.AcceptStatus.REJECTED, "error": error, "message_id": MESSAGE_ID},
        ):
            with self.subTest(fields=fields):
                self.assert_invalid(lambda: c.AcceptResult(**fields))
        self.assertIsNone(c.AcceptResult(status=c.AcceptStatus.CONFLICT, error=error).message_id)

    def test_created_task_batch_expresses_a_committed_intent_even_if_message_is_pending(self):
        result = c.CreateTasksResult(
            status=c.CreateTasksStatus.CREATED,
            tasks=(task(),),
            deliveries=(task_delivery(),),
            intent_fingerprint=parsed().intent_fingerprint,
        )
        self.assertTrue(result.is_committed)
        self.assertEqual(supervisor_message().processing_state, c.ProcessingState.PENDING)
        self.assertEqual(result.intent_fingerprint, parsed().intent_fingerprint)
        with self.assertRaises(FrozenInstanceError):
            result.intent_fingerprint = parsed(instruction(content="replacement")).intent_fingerprint

    def test_rejected_batch_cannot_include_partial_tasks_or_deliveries(self):
        error = c.ErrorInfo(code="AGENT_NOT_FOUND", message="One target does not exist")
        rejected = c.CreateTasksResult(status=c.CreateTasksStatus.REJECTED, error=error)
        self.assertFalse(rejected.is_committed)
        self.assertEqual(rejected.tasks, ())
        self.assertEqual(rejected.deliveries, ())
        self.assert_invalid(lambda: replace(rejected, tasks=(task(),), deliveries=(task_delivery(),)))

    def test_created_batch_requires_all_initial_deliveries_and_valid_commit_fingerprint(self):
        valid = {
            "status": c.CreateTasksStatus.CREATED,
            "tasks": (task(),),
            "deliveries": (task_delivery(),),
            "intent_fingerprint": parsed().intent_fingerprint,
        }
        for changes in (
            {"tasks": ()},
            {"deliveries": ()},
            {"intent_fingerprint": None},
            {"intent_fingerprint": "bad"},
            {"tasks": (task(instruction_index=1),)},
            {"deliveries": (report_delivery(),)},
            {"deliveries": (task_delivery(target_id=c.AgentId("other")),)},
            {"tasks": (task(state=c.TaskState.DISPATCHED),)},
        ):
            with self.subTest(changes=changes):
                self.assert_invalid(lambda: c.CreateTasksResult(**{**valid, **changes}))

    def test_batch_rejects_duplicate_ids_and_mixed_source_messages(self):
        other_task_id = c.TaskId("cftask_" + "c" * 32)
        other_delivery_id = c.DeliveryId("cfdel_" + "e" * 32)
        second = task(task_id=other_task_id, instruction_index=1)
        second_delivery = task_delivery(delivery_id=other_delivery_id, subject_id=other_task_id)
        fields = {
            "status": c.CreateTasksStatus.CREATED,
            "tasks": (task(), second),
            "deliveries": (task_delivery(), second_delivery),
            "intent_fingerprint": parsed(instruction(), instruction("docs", "docs")).intent_fingerprint,
        }
        self.assertEqual(len(c.CreateTasksResult(**fields).tasks), 2)
        for changes in (
            {"tasks": (task(), replace(second, task_id=TASK_ID))},
            {"tasks": (task(), replace(second, source_message_id=REPORT_MESSAGE_ID))},
            {"deliveries": (task_delivery(), replace(second_delivery, delivery_id=DELIVERY_ID))},
        ):
            with self.subTest(changes=changes):
                self.assert_invalid(lambda: c.CreateTasksResult(**{**fields, **changes}))

    def test_duplicate_batch_can_return_existing_terminal_or_unknown_records(self):
        error = c.ErrorInfo(code="ACK_TIMEOUT", message="No acknowledgement")
        result = c.CreateTasksResult(
            status=c.CreateTasksStatus.DUPLICATE,
            tasks=(task(state=c.TaskState.COMPLETED, final_report_message_id=REPORT_MESSAGE_ID),),
            deliveries=(task_delivery(state=c.DeliveryState.UNKNOWN, error=error),),
            intent_fingerprint=parsed().intent_fingerprint,
        )
        self.assertTrue(result.is_committed)
        self.assertFalse(result.deliveries[0].can_attempt_send)

    def test_process_result_cannot_claim_pending_work_is_processed(self):
        error = c.ErrorInfo(code="FINAL_REPORT_CONFLICT", message="Second final")
        blocked = c.ProcessResult(message_id=REPORT_MESSAGE_ID, state=c.ProcessingState.BLOCKED, error=error)
        self.assertEqual(blocked.error.code, "FINAL_REPORT_CONFLICT")
        self.assert_invalid(lambda: c.ProcessResult(message_id=MESSAGE_ID, state=c.ProcessingState.PENDING))
        self.assert_invalid(lambda: c.ProcessResult(message_id=MESSAGE_ID, state=c.ProcessingState.BLOCKED))
        self.assert_invalid(lambda: c.ProcessResult(message_id=MESSAGE_ID, state=c.ProcessingState.PROCESSED, error=error))

    def test_gateway_outcome_cannot_return_sending_as_a_completed_call(self):
        self.assert_invalid(lambda: c.DeliveryOutcome(state=c.DeliveryState.SENDING))
        self.assert_invalid(lambda: c.DeliveryOutcome(state=c.DeliveryState.UNKNOWN))
        self.assert_invalid(lambda: c.DeliveryOutcome(state=c.DeliveryState.PENDING))
        error = c.ErrorInfo(code="AGENT_UNAVAILABLE", message="Not sent")
        pending = c.DeliveryOutcome(state=c.DeliveryState.PENDING, error=error)
        self.assertTrue(pending.can_attempt_send)
        unknown = c.DeliveryOutcome(state=c.DeliveryState.UNKNOWN, error=error)
        self.assertFalse(unknown.can_attempt_send)

    def test_error_info_reuses_existing_exception_and_snapshots_its_details(self):
        details = {"fields": ["target"]}
        original = ContextFlowError("PARSE_FAILED", "Invalid output", retryable=True, details=details)
        info = c.ErrorInfo.from_error(original)
        self.assertIs(c.ContextFlowError, ContextFlowError)
        self.assertEqual(info.to_dict(), original.to_dict())
        details["fields"].append("content")
        self.assertEqual(info.details["fields"], ("target",))
        exported = info.to_dict()
        exported["details"]["fields"].append("other")
        self.assertEqual(info.details["fields"], ("target",))
        self.assert_invalid(lambda: c.ErrorInfo(code="", message="Invalid"))
        self.assert_invalid(lambda: c.ErrorInfo(code="ERROR", message="Invalid", retryable=1))


if __name__ == "__main__":
    unittest.main()
