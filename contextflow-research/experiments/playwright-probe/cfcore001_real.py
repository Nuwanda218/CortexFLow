#!/usr/bin/env python3
"""CF-CORE-001 real ChatGPT Endpoint validation.

The script records structure only: it never writes message bodies to evidence.
Run phase ``before``, refresh the exact ChatGPT tab, then run phase ``after``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contextflow_core import (  # noqa: E402
    EndpointRegistry,
    EndpointService,
    WebSocketChatGPTAdapter,
)


ARTIFACTS = ROOT / "_artifacts"
DEFAULT_REGISTRY = ARTIFACTS / "cfcore001-endpoints.json"
DEFAULT_EVIDENCE = ARTIFACTS / "cfcore001-real-evidence.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json_lf(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def snapshot_evidence(snapshot) -> dict:
    return {
        "endpoint_id": snapshot.endpoint_id,
        "native_session_id": snapshot.native_session_id,
        "state": snapshot.state.value,
        "captured_at": snapshot.captured_at,
        "session_kind": snapshot.session_kind,
        "url": snapshot.url,
        "generation_state": snapshot.generation.get("state"),
        "history_scope": snapshot.history_scope,
        "absence_means_deleted": snapshot.absence_means_deleted,
        "message_count": len(snapshot.messages),
        "messages": [
            {
                "message_id": message.message_id,
                "role": message.role,
                "turn_group_id": message.turn_group_id,
                "turn_position": message.turn_position,
                "status": message.status,
                "content_length": len(message.content),
            }
            for message in snapshot.messages
        ],
    }


async def run(args) -> int:
    registry_path = Path(args.registry)
    evidence_path = Path(args.evidence)
    registry = EndpointRegistry(registry_path)
    adapter = WebSocketChatGPTAdapter(
        uri=args.bridge_uri,
        origin=args.bridge_origin,
        request_timeout=args.timeout,
    )
    service = EndpointService(adapter=adapter, registry=registry)
    try:
        if args.phase == "before":
            discovered = await service.discover()
            target = [item for item in discovered if item.native_session_id == args.session]
            if len(target) != 1 or not target[0].bindable:
                raise RuntimeError(
                    f"target must have exactly one bindable open tab; matches={len(target)}"
                )
            bound = await service.bind(args.session)
            resolution = await service.resolve(bound.endpoint.endpoint_id)
            snapshot = await service.read(bound.endpoint.endpoint_id)
            evidence = {
                "schema_version": 1,
                "test": "CF-CORE-001 real ChatGPT Endpoint lifecycle",
                "started_at": now_iso(),
                "contains_message_bodies": False,
                "endpoint_id": bound.endpoint.endpoint_id,
                "native_session_id": args.session,
                "registry_path": str(registry_path),
                "before": {
                    "created": bound.created,
                    "discovery": target[0].to_dict(),
                    "resolution": resolution.to_dict(),
                    "snapshot": snapshot_evidence(snapshot),
                },
            }
            write_json_lf(evidence_path, evidence)
            print(json.dumps({
                "phase": "before",
                "endpoint_id": bound.endpoint.endpoint_id,
                "tab_id": resolution.location.tab_id if resolution.location else None,
                "message_count": len(snapshot.messages),
                "evidence": str(evidence_path),
            }, ensure_ascii=False))
            return 0

        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        endpoint_id = evidence["endpoint_id"]
        binding = registry.get(endpoint_id)
        if binding is None or binding.native_session_id != args.session:
            raise RuntimeError("persisted endpoint binding is missing or changed")
        resolution = await service.resolve(endpoint_id)
        snapshot = await service.read(endpoint_id)
        same_endpoint = snapshot.endpoint_id == evidence["before"]["snapshot"]["endpoint_id"]
        evidence["after"] = {
            "observed_at": now_iso(),
            "refresh_performed_externally": True,
            "resolution": resolution.to_dict(),
            "snapshot": snapshot_evidence(snapshot),
        }
        evidence["verification"] = {
            "same_endpoint_id": same_endpoint,
            "same_native_session_id": snapshot.native_session_id == args.session,
            "available_after_refresh": resolution.state.value == "available",
            "read_after_refresh": True,
        }
        evidence["completed_at"] = now_iso()
        write_json_lf(evidence_path, evidence)
        print(json.dumps({
            "phase": "after",
            "endpoint_id": endpoint_id,
            "tab_id": resolution.location.tab_id if resolution.location else None,
            "message_count": len(snapshot.messages),
            "same_endpoint_id": same_endpoint,
            "evidence": str(evidence_path),
        }, ensure_ascii=False))
        return 0 if same_endpoint else 1
    finally:
        await adapter.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--phase", choices=("before", "after"), default="before")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--bridge-uri", default="ws://127.0.0.1:8766/ws?token=dev-token-cf-exp-002")
    parser.add_argument("--bridge-origin", default="http://127.0.0.1:8766")
    parser.add_argument("--timeout", type=float, default=20.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
