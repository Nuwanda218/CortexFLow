import json
import unittest

from contextflow_core import contracts as c
from contextflow_core.errors import ContextFlowError
from contextflow_core.parse_result_codec import (
    EncodedParseResult,
    decode_parse_result,
    encode_parse_result,
)


class ParseResultCodecTests(unittest.TestCase):
    def test_parsed_round_trip_preserves_order_and_recomputes_fingerprint(self):
        result = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(
                c.ParsedInstruction(target_agent="Codex", content="修复 Adapter"),
                c.ParsedInstruction(target_agent="Claude Code", content="运行测试 ✅"),
            ),
        )

        encoded = encode_parse_result(result)

        self.assertEqual(encoded.status, "parsed")
        self.assertIsNone(encoded.error_json)
        self.assertEqual(encoded.fingerprint, result.intent_fingerprint)
        self.assertEqual(
            json.loads(encoded.instructions_json),
            [
                {"content": "修复 Adapter", "target_agent": "Codex"},
                {"content": "运行测试 ✅", "target_agent": "Claude Code"},
            ],
        )
        self.assertEqual(decode_parse_result(encoded), result)

    def test_unresolved_and_failed_round_trip_full_error_info(self):
        error = c.ErrorInfo(
            code="TARGET_AMBIGUOUS",
            message="无法安全确定目标",
            retryable=False,
            details={"matches": ["Claude", "Claude Code"], "nested": {"count": 2}},
        )
        for status in (c.ParseStatus.UNRESOLVED, c.ParseStatus.FAILED):
            with self.subTest(status=status):
                result = c.ParseResult(status=status, error=error)
                encoded = encode_parse_result(result)
                self.assertEqual(encoded.instructions_json, "[]")
                self.assertIsNone(encoded.fingerprint)
                self.assertEqual(decode_parse_result(encoded), result)

    def test_encode_requires_a_real_parse_result(self):
        with self.assertRaises(ContextFlowError) as caught:
            encode_parse_result({"status": "parsed"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_decode_rejects_missing_extra_and_wrong_instruction_fields(self):
        invalid_payloads = (
            '[{"target_agent":"Codex"}]',
            '[{"target_agent":"Codex","content":"work","extra":true}]',
            '[{"target_agent":"","content":"work"}]',
            '{}',
            'null',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                encoded = EncodedParseResult(
                    status="parsed",
                    instructions_json=payload,
                    error_json=None,
                    fingerprint="0" * 64,
                )
                with self.assertRaises(ContextFlowError) as caught:
                    decode_parse_result(encoded)
                self.assertEqual(caught.exception.code, "PARSE_RESULT_CORRUPT")

    def test_decode_rejects_duplicate_json_keys_at_every_object_level(self):
        cases = (
            EncodedParseResult(
                status="parsed",
                instructions_json='[{"target_agent":"Codex","target_agent":"Claude","content":"work"}]',
                error_json=None,
                fingerprint="0" * 64,
            ),
            EncodedParseResult(
                status="failed",
                instructions_json="[]",
                error_json='{"code":"A","code":"B","message":"bad","retryable":false,"details":{}}',
                fingerprint=None,
            ),
            EncodedParseResult(
                status="failed",
                instructions_json="[]",
                error_json='{"code":"A","message":"bad","retryable":false,"details":{"x":1,"x":2}}',
                fingerprint=None,
            ),
        )
        for encoded in cases:
            with self.subTest(encoded=encoded):
                with self.assertRaises(ContextFlowError) as caught:
                    decode_parse_result(encoded)
                self.assertEqual(caught.exception.code, "PARSE_RESULT_CORRUPT")

    def test_decode_rejects_nonfinite_numbers_and_invalid_error_shape(self):
        cases = (
            '{"code":"A","message":"bad","retryable":false,"details":{"value":NaN}}',
            '{"code":"A","message":"bad","retryable":false,"details":{"value":Infinity}}',
            '{"code":"A","message":"bad","retryable":0,"details":{}}',
            '{"code":"A","message":"bad","retryable":false}',
            '{"code":"A","message":"bad","retryable":false,"details":{},"extra":1}',
        )
        for error_json in cases:
            with self.subTest(error_json=error_json):
                encoded = EncodedParseResult(
                    status="failed",
                    instructions_json="[]",
                    error_json=error_json,
                    fingerprint=None,
                )
                with self.assertRaises(ContextFlowError) as caught:
                    decode_parse_result(encoded)
                self.assertEqual(caught.exception.code, "PARSE_RESULT_CORRUPT")

    def test_decode_rejects_status_combinations_and_unknown_status(self):
        cases = (
            EncodedParseResult("parsed", "[]", None, "0" * 64),
            EncodedParseResult("parsed", '[{"target_agent":"Codex","content":"work"}]', "{}", "0" * 64),
            EncodedParseResult("failed", "[]", None, None),
            EncodedParseResult("unresolved", '[{"target_agent":"Codex","content":"work"}]', '{}', None),
            EncodedParseResult("unknown", "[]", None, None),
        )
        for encoded in cases:
            with self.subTest(encoded=encoded):
                with self.assertRaises(ContextFlowError) as caught:
                    decode_parse_result(encoded)
                self.assertEqual(caught.exception.code, "PARSE_RESULT_CORRUPT")

    def test_decode_rejects_tampered_fingerprint_and_unencodable_text(self):
        result = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(c.ParsedInstruction(target_agent="Codex", content="work"),),
        )
        encoded = encode_parse_result(result)
        tampered = EncodedParseResult(
            encoded.status,
            encoded.instructions_json,
            encoded.error_json,
            "0" * 64,
        )
        with self.assertRaises(ContextFlowError) as caught:
            decode_parse_result(tampered)
        self.assertEqual(caught.exception.code, "PARSE_RESULT_CORRUPT")

        invalid_unicode = EncodedParseResult(
            "parsed",
            '[{"target_agent":"Codex","content":"\\ud800"}]',
            None,
            "0" * 64,
        )
        with self.assertRaises(ContextFlowError) as caught:
            decode_parse_result(invalid_unicode)
        self.assertEqual(caught.exception.code, "PARSE_RESULT_CORRUPT")


if __name__ == "__main__":
    unittest.main()
