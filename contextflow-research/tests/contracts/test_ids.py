import unittest

from contextflow_core.errors import ContextFlowError


class IdentifierTests(unittest.TestCase):
    def test_core_identifiers_have_separate_namespaces(self):
        from contextflow_core.contracts import DeliveryId, MessageId, TaskId

        suffix = "a" * 32
        self.assertEqual(str(MessageId("cfmsg_" + suffix)), "cfmsg_" + suffix)
        self.assertEqual(str(TaskId("cftask_" + suffix)), "cftask_" + suffix)
        self.assertEqual(str(DeliveryId("cfdel_" + suffix)), "cfdel_" + suffix)
        for cls, value in (
            (MessageId, "cftask_" + suffix),
            (TaskId, "cfmsg_" + suffix),
            (DeliveryId, "cfmsg_" + suffix),
            (MessageId, "cfmsg_short"),
        ):
            with self.subTest(cls=cls.__name__, value=value):
                with self.assertRaises(ContextFlowError) as caught:
                    cls(value)
                self.assertEqual(caught.exception.code, "INVALID_CONTRACT")

    def test_a_typed_id_cannot_be_reinterpreted_as_another_identity(self):
        from contextflow_core.contracts import AgentId, MessageId, NativeMessageId

        core_id = MessageId("cfmsg_" + "a" * 32)
        with self.assertRaises(ContextFlowError):
            NativeMessageId(core_id)
        with self.assertRaises(ContextFlowError):
            AgentId(core_id)
        self.assertEqual(MessageId(core_id), core_id)

    def test_adapter_identifiers_keep_the_existing_v1_character_rules(self):
        from contextflow_core.contracts import EndpointId, SubmissionId

        self.assertEqual(str(EndpointId("cfep_alpha_-123")), "cfep_alpha_-123")
        self.assertEqual(str(SubmissionId("sub_alpha_-123")), "sub_alpha_-123")
        for cls, value in ((EndpointId, "cfep_x"), (SubmissionId, "sub_x")):
            with self.subTest(cls=cls.__name__):
                with self.assertRaises(ContextFlowError):
                    cls(value)

    def test_external_identity_values_are_not_core_generated_ids(self):
        from contextflow_core.contracts import (
            AgentId,
            NativeMessageId,
            NativeSessionId,
            ReportId,
        )

        for cls, value in (
            (AgentId, "codex-local"),
            (NativeMessageId, "assistant-native-1"),
            (NativeSessionId, "session-native-1"),
            (ReportId, "agent-report-1"),
        ):
            with self.subTest(cls=cls.__name__):
                self.assertEqual(str(cls(value)), value)
                for invalid in ("", "  ", None, True, 123, "x" * 257):
                    with self.subTest(invalid=invalid):
                        with self.assertRaises(ContextFlowError):
                            cls(invalid)


if __name__ == "__main__":
    unittest.main()
