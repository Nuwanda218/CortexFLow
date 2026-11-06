"""Regression cases for contract-boundary review findings."""

from enum import Enum
import json
import unittest

from contextflow_core import contracts as c
from tests.contracts.samples import task_delivery


class IdentifierValue(str, Enum):
    MESSAGE = "cfmsg_" + "a" * 32
    TASK = "cftask_" + "b" * 32
    DELIVERY = "cfdel_" + "c" * 32
    AGENT = "codex-local"


class StringBoundaryTests(unittest.TestCase):
    def test_id_construction_preserves_string_enum_value_not_display_name(self):
        for cls, value in (
            (c.MessageId, IdentifierValue.MESSAGE),
            (c.TaskId, IdentifierValue.TASK),
            (c.DeliveryId, IdentifierValue.DELIVERY),
            (c.AgentId, IdentifierValue.AGENT),
        ):
            with self.subTest(cls=cls.__name__):
                identifier = cls(value)
                self.assertEqual(str(identifier), value.value)
                if cls.pattern is not None:
                    self.assertIsNotNone(cls.pattern.match(identifier))
    def test_error_snapshot_preserves_the_json_value_of_string_enums(self):
        details = {"state": c.DeliveryState.UNKNOWN, "nested": [IdentifierValue.AGENT]}
        original = c.ContextFlowError("ERROR", "test", details=details)
        snapshot = c.ErrorInfo.from_error(original)
        expected = json.loads(json.dumps(details))
        self.assertEqual(snapshot.to_dict()["details"], expected)
        self.assertIs(type(snapshot.details["state"]), str)

    def test_delivery_snapshot_preserves_the_json_value_of_string_enums(self):
        evidence = {"state": c.DeliveryState.UNKNOWN, "nested": [IdentifierValue.AGENT]}
        snapshot = task_delivery(evidence=evidence)
        self.assertEqual(snapshot.evidence["state"], "unknown")
        self.assertEqual(snapshot.evidence["nested"], ("codex-local",))
        self.assertIs(type(snapshot.evidence["state"]), str)


class UnicodeBoundaryTests(unittest.TestCase):
    def test_instruction_rejects_surrogates_before_a_parsed_candidate_is_built(self):
        for field in ("target_agent", "content"):
            for raw in ('"\\ud800"', '"\\udfff"', '"text\\ud800tail"'):
                with self.subTest(field=field, raw=raw):
                    fields = {"target_agent": "codex", "content": "valid body"}
                    fields[field] = json.loads(raw)
                    with self.assertRaises(c.ContextFlowError) as caught:
                        c.ParsedInstruction(**fields)
                    self.assertEqual(caught.exception.code, "INVALID_CONTRACT")
                    self.assertEqual(caught.exception.details["field"], field)

    def test_valid_unicode_candidate_still_has_a_stable_fingerprint(self):
        instruction = c.ParsedInstruction(target_agent="文档 Agent", content="修复 Unicode 😀")
        candidate = c.ParseResult(status=c.ParseStatus.PARSED, instructions=(instruction,))
        self.assertRegex(candidate.intent_fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual(candidate.intent_fingerprint, candidate.intent_fingerprint)


class JsonBoundaryTests(unittest.TestCase):
    def assert_metadata_rejected(self, metadata):
        for field, build in (
            ("error.details", lambda: c.ErrorInfo(code="ERROR", message="test", details=metadata)),
            ("evidence", lambda: task_delivery(evidence=metadata)),
        ):
            with self.subTest(field=field):
                with self.assertRaises(c.ContextFlowError) as caught:
                    build()
                self.assertEqual(caught.exception.code, "INVALID_CONTRACT")
                self.assertEqual(caught.exception.details["field"], field)

    def test_cyclic_dict_and_list_are_rejected_as_contract_errors(self):
        mapping = {}
        mapping["self"] = mapping
        sequence = []
        sequence.append(sequence)
        for metadata in (mapping, {"list": sequence}):
            self.assert_metadata_rejected(metadata)

    def test_deep_json_input_is_rejected_without_bare_recursion_error(self):
        metadata = json.loads('{"x":' + "[" * 600 + "0" + "]" * 600 + "}")
        self.assert_metadata_rejected(metadata)

    def test_json_nesting_boundary_is_explicit_and_serializable(self):
        nested = 0
        for _ in range(64):
            nested = [nested]
        metadata = {"value": nested}
        snapshot = c.ErrorInfo(code="ERROR", message="test", details=metadata)
        self.assertEqual(snapshot.to_dict()["details"], metadata)
        self.assert_metadata_rejected({"value": [nested]})

    def test_repeated_shared_values_are_not_mistaken_for_cycles(self):
        shared = {"state": c.DeliveryState.UNKNOWN, "values": ["one"]}
        metadata = {"left": shared, "right": shared}
        snapshot = c.ErrorInfo(code="ERROR", message="test", details=metadata)
        self.assertEqual(snapshot.to_dict()["details"], json.loads(json.dumps(metadata)))
        self.assertIsNot(snapshot.details["left"], snapshot.details["right"])


if __name__ == "__main__":
    unittest.main()
