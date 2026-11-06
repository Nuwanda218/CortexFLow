"""CF-ADAPTER-001: envelope validation and Mock Core idempotency tests."""

import json
import tempfile
import unittest
from pathlib import Path

from contextflow_core.errors import ContextFlowError
from contextflow_core.submission import (
    MockCoreReceiver,
    canonical_envelope,
    envelope_fingerprint,
    validate_envelope,
)


def envelope(**overrides):
    base = {
        "schema_version": 1,
        "submission_id": "sub_0123456789abcdef0123456789abcdef",
        "source": {
            "adapter": "chatgpt_web",
            "endpoint_id": "cfep_0123456789abcdef0123456789abcdef",
            "native_session_id": "6aeb4db5-d930-83ee-8b14-0cf74ef0a84d",
            "message_id": "ae4b98f0-d220-4d9f-bd36-87057b1920ec",
            "user_message_id": "4c32015b-5f55-40f5-9464-986eeff58164",
            "turn_group_id": "fallback-turn-14",
        },
        "content": {"format": "markdown", "text": "Assistant reply body\n\nsecond paragraph"},
    }
    for key, value in overrides.items():
        if key == "source":
            base["source"] = {**base["source"], **value}
        elif key == "content":
            base["content"] = {**base["content"], **value}
        else:
            base[key] = value
    return base


class EnvelopeValidationTests(unittest.TestCase):
    def test_valid_envelope_is_normalized(self):
        normalized = validate_envelope(envelope())
        self.assertEqual(normalized["schema_version"], 1)
        self.assertEqual(normalized["source"]["adapter"], "chatgpt_web")
        self.assertEqual(normalized["content"]["format"], "markdown")

    def test_null_user_message_id_and_turn_group_are_allowed(self):
        normalized = validate_envelope(
            envelope(source={"user_message_id": None, "turn_group_id": None})
        )
        self.assertIsNone(normalized["source"]["user_message_id"])
        self.assertIsNone(normalized["source"]["turn_group_id"])

    def test_missing_native_message_id_is_rejected(self):
        with self.assertRaises(ContextFlowError) as caught:
            validate_envelope(envelope(source={"message_id": None}))
        self.assertEqual(caught.exception.code, "INVALID_ENVELOPE")
        self.assertEqual(caught.exception.details["field"], "source.message_id")

    def test_empty_native_message_id_is_rejected(self):
        with self.assertRaises(ContextFlowError):
            validate_envelope(envelope(source={"message_id": "   "}))

    def test_unsupported_schema_version_is_rejected(self):
        with self.assertRaises(ContextFlowError) as caught:
            validate_envelope(envelope(schema_version=2))
        self.assertEqual(caught.exception.details["field"], "schema_version")

    def test_missing_field_is_rejected(self):
        broken = envelope()
        del broken["content"]
        with self.assertRaises(ContextFlowError) as caught:
            validate_envelope(broken)
        self.assertEqual(caught.exception.details["field"], "$")

    def test_unexpected_field_is_rejected(self):
        broken = envelope()
        broken["context"] = {"history": []}
        with self.assertRaises(ContextFlowError) as caught:
            validate_envelope(broken)
        self.assertEqual(caught.exception.code, "INVALID_ENVELOPE")

    def test_wrong_adapter_is_rejected(self):
        with self.assertRaises(ContextFlowError):
            validate_envelope(envelope(source={"adapter": "other"}))

    def test_bad_endpoint_id_is_rejected(self):
        with self.assertRaises(ContextFlowError):
            validate_envelope(envelope(source={"endpoint_id": "session-1"}))

    def test_bad_submission_id_is_rejected(self):
        with self.assertRaises(ContextFlowError):
            validate_envelope(envelope(submission_id="sub_x"))

    def test_empty_content_is_rejected(self):
        with self.assertRaises(ContextFlowError):
            validate_envelope(envelope(content={"text": "   "}))

    def test_non_markdown_format_is_rejected(self):
        with self.assertRaises(ContextFlowError):
            validate_envelope(envelope(content={"format": "plaintext"}))

    def test_fingerprint_is_stable_and_content_sensitive(self):
        first = envelope()
        second = envelope()
        self.assertEqual(canonical_envelope(first), canonical_envelope(second))
        self.assertEqual(envelope_fingerprint(first), envelope_fingerprint(second))
        changed = envelope(content={"text": "different body"})
        self.assertNotEqual(envelope_fingerprint(first), envelope_fingerprint(changed))


class MockCoreReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Path(self.temp_dir.name) / "submissions.json"
        self.receiver = MockCoreReceiver(self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_envelope_is_accepted_and_stored(self):
        ack = self.receiver.receive(envelope())
        self.assertEqual(ack["status"], "accepted")
        self.assertFalse(ack["duplicate"])
        self.assertEqual(self.receiver.count(), 1)
        self.assertTrue(self.store.exists())

    def test_duplicate_same_envelope_returns_same_ack_without_duplicate(self):
        first = self.receiver.receive(envelope())
        second = self.receiver.receive(envelope())
        self.assertEqual(first["submission_id"], second["submission_id"])
        self.assertEqual(second["status"], "accepted")
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.receiver.count(), 1)

    def test_duplicate_with_different_content_is_conflict_and_not_overwritten(self):
        self.receiver.receive(envelope())
        conflict = self.receiver.receive(envelope(content={"text": "changed body"}))
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(conflict["error"]["code"], "SUBMISSION_CONFLICT")
        self.assertEqual(self.receiver.count(), 1)
        stored = self.receiver.get("sub_0123456789abcdef0123456789abcdef")
        self.assertEqual(stored["envelope"]["content"]["text"], "Assistant reply body\n\nsecond paragraph")

    def test_invalid_envelope_is_rejected_and_not_stored(self):
        ack = self.receiver.receive(envelope(source={"message_id": None}))
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["error"]["code"], "INVALID_ENVELOPE")
        self.assertEqual(self.receiver.count(), 0)

    def test_receiver_reloads_store_and_keeps_idempotency(self):
        self.receiver.receive(envelope())
        reloaded = MockCoreReceiver(self.store)
        self.assertEqual(reloaded.count(), 1)
        ack = reloaded.receive(envelope())
        self.assertTrue(ack["duplicate"])
        self.assertEqual(reloaded.count(), 1)

    def test_store_contains_full_envelope_for_inspection(self):
        self.receiver.receive(envelope())
        document = json.loads(self.store.read_text(encoding="utf-8"))
        record = document["submissions"]["sub_0123456789abcdef0123456789abcdef"]
        self.assertEqual(record["envelope"]["source"]["native_session_id"],
                         "6aeb4db5-d930-83ee-8b14-0cf74ef0a84d")
        self.assertIn("Assistant reply body", record["envelope"]["content"]["text"])

    def test_two_different_submission_ids_are_both_stored(self):
        self.receiver.receive(envelope())
        self.receiver.receive(envelope(submission_id="sub_ffffffffffffffffffffffffffffffff"))
        self.assertEqual(self.receiver.count(), 2)


if __name__ == "__main__":
    unittest.main()
