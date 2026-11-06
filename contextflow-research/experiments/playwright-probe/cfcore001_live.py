#!/usr/bin/env python3
"""CF-CORE-001 live interface check (real page, real bridge).

Phases (run in order, bridge up / down / up):
  python cfcore001_live.py --phase online    # discover, bind, resolve, read, errors
  (stop bridge)
  python cfcore001_live.py --phase offline   # resolve -> offline, read -> error
  (start bridge)
  python cfcore001_live.py --phase recover   # resolve -> available again, read works
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contextflow_core import (  # noqa: E402
    EndpointRegistry,
    EndpointService,
    WebSocketChatGPTAdapter,
)
from contextflow_core.errors import ContextFlowError  # noqa: E402

STATE = ROOT / "_artifacts" / "cfcore001-live-state.json"
REPORT = ROOT / "_artifacts" / "cfcore001-live-report.json"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def online() -> dict:
    out: dict = {"phase": "online"}
    adapter = WebSocketChatGPTAdapter()
    service = EndpointService(adapter, EndpointRegistry())
    try:
        discovered = await service.discover()
        out["discover"] = [d.to_dict() for d in discovered]
        bindable = [d for d in discovered if d.bindable]
        out["bindable_count"] = len(bindable)
        if not bindable:
            out["error"] = "no bindable session"
            return out

        target = bindable[0]
        bound = await service.bind(target.native_session_id)
        out["bind"] = {
            "endpoint_id": bound.endpoint.endpoint_id,
            "native_session_id": bound.endpoint.native_session_id,
            "session_kind": bound.endpoint.session_kind,
            "created": bound.created,
        }
        again = await service.bind(target.native_session_id)
        out["bind_again"] = {
            "endpoint_id": again.endpoint.endpoint_id,
            "created": again.created,
            "same_id": again.endpoint.endpoint_id == bound.endpoint.endpoint_id,
        }

        resolution = await service.resolve(bound.endpoint.endpoint_id)
        out["resolve"] = resolution.to_dict()

        snapshot = await service.read(bound.endpoint.endpoint_id)
        data = snapshot.to_dict()
        out["read"] = {
            "endpoint_id": data["endpoint_id"],
            "native_session_id": data["native_session_id"],
            "state": data["state"],
            "session_kind": data["session_kind"],
            "generation": data["generation"],
            "message_count": len(data["messages"]),
            "history_scope": data["history_scope"],
            "absence_means_deleted": data["absence_means_deleted"],
            "messages": [
                {
                    "role": m["role"],
                    "message_id": m["message_id"],
                    "turn_group_id": m["turn_group_id"],
                    "turn_position": m["turn_position"],
                    "status": m["status"],
                    "text_length": len(m["content"]),
                }
                for m in data["messages"][-4:]
            ],
        }

        # persistence: a fresh registry must see the same binding
        fresh = EndpointRegistry()
        persisted = fresh.get(bound.endpoint.endpoint_id)
        out["persistence"] = {
            "registry_path": str(fresh.path),
            "registry_exists": fresh.path.exists(),
            "reloaded_binding": persisted.to_dict() if persisted else None,
        }

        # error paths
        try:
            await service.bind("definitely-not-a-real-session")
            out["bind_unknown"] = "unexpected success"
        except ContextFlowError as exc:
            out["bind_unknown"] = {"code": exc.code, "retryable": exc.retryable}
        try:
            await service.resolve("cfep_does_not_exist")
            out["resolve_unknown"] = "unexpected success"
        except ContextFlowError as exc:
            out["resolve_unknown"] = {"code": exc.code}

        save_state({"endpoint_id": bound.endpoint.endpoint_id,
                    "native_session_id": bound.endpoint.native_session_id})
        return out
    finally:
        await adapter.close()


async def offline() -> dict:
    out: dict = {"phase": "offline"}
    state = load_state()
    endpoint_id = state.get("endpoint_id")
    if not endpoint_id:
        out["error"] = "no endpoint id from online phase"
        return out
    adapter = WebSocketChatGPTAdapter(request_timeout=5.0)
    service = EndpointService(adapter, EndpointRegistry())
    try:
        try:
            resolution = await service.resolve(endpoint_id)
            out["resolve"] = resolution.to_dict()
        except ContextFlowError as exc:
            out["resolve_error"] = {"code": exc.code, "message": exc.message}
        try:
            await service.read(endpoint_id)
            out["read"] = "unexpected success"
        except ContextFlowError as exc:
            out["read_error"] = {"code": exc.code, "retryable": exc.retryable}
        return out
    finally:
        await adapter.close()


async def recover() -> dict:
    out: dict = {"phase": "recover"}
    state = load_state()
    endpoint_id = state.get("endpoint_id")
    if not endpoint_id:
        out["error"] = "no endpoint id from online phase"
        return out
    adapter = WebSocketChatGPTAdapter()
    service = EndpointService(adapter, EndpointRegistry())
    try:
        resolution = await service.resolve(endpoint_id)
        out["resolve"] = resolution.to_dict()
        snapshot = await service.read(endpoint_id)
        out["read"] = {
            "state": snapshot.state.value,
            "message_count": len(snapshot.messages),
            "captured_at": snapshot.captured_at,
        }
        return out
    finally:
        await adapter.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["online", "offline", "recover"])
    args = ap.parse_args()
    fn = {"online": online, "offline": offline, "recover": recover}[args.phase]
    result = await fn()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # append to report
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    report[args.phase] = result
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
