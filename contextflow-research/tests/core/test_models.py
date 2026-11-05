import unittest

from contextflow_core.models import (
    DiscoveredSession,
    EndpointSnapshot,
    EndpointState,
    NormalizedMessage,
)


class ModelContractTests(unittest.TestCase):
    def test_endpoint_states_are_stable_wire_values(self):
        self.assertEqual(
            [state.value for state in EndpointState],
            ["available", "offline", "ambiguous"],
        )

    def test_discovered_session_serializes_adapter_metadata(self):
        session = DiscoveredSession(
            provider="chatgpt_web",
            native_session_id="session-a",
            tab_id=17,
            url="https://chatgpt.com/c/session-a",
            session_kind="chat",
            ready=True,
            bindable=True,
            source="open_tab",
            error=None,
        )

        self.assertEqual(
            session.to_dict(),
            {
                "provider": "chatgpt_web",
                "native_session_id": "session-a",
                "tab_id": 17,
                "url": "https://chatgpt.com/c/session-a",
                "session_kind": "chat",
                "ready": True,
                "bindable": True,
                "source": "open_tab",
                "error": None,
            },
        )

    def test_message_consumes_adapter_turn_group_without_dom_parsing(self):
        message = NormalizedMessage.from_adapter(
            {
                "message_id": "assistant-5",
                "message_ids": ["assistant-5"],
                "role": "assistant",
                "content": "reply",
                "turn_key": "fallback-turn-5:2:assistant",
                "turn_group_id": "fallback-turn-5",
                "turn_position": 2,
                "order": 9,
                "status": "complete",
            }
        )

        self.assertEqual(message.turn_group_id, "fallback-turn-5")
        self.assertEqual(message.turn_position, 2)
        self.assertEqual(
            message.evidence,
            {
                "turn_key": "fallback-turn-5:2:assistant",
                "message_ids": ["assistant-5"],
                "dom_order": 9,
                "adapter_status": "complete",
            },
        )

    def test_snapshot_declares_rendered_dom_is_not_deletion_evidence(self):
        snapshot = EndpointSnapshot(
            endpoint_id="ep-1",
            native_session_id="session-a",
            state=EndpointState.AVAILABLE,
            captured_at="2026-11-05T00:00:00Z",
            session_kind="chat",
            url="https://chatgpt.com/c/session-a",
            generation={"state": "completed"},
            messages=[],
        )

        data = snapshot.to_dict()
        self.assertEqual(data["history_scope"], "rendered_dom")
        self.assertFalse(data["absence_means_deleted"])


if __name__ == "__main__":
    unittest.main()
