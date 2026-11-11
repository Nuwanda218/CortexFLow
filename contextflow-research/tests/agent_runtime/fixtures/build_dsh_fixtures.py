"""Build trimmed DSH history fixtures from recorded C-02 evidence.

The full C-02 history page is ~225 KB; tests only need the user message,
assistant messages and the turn/end record. This script is kept for
reproducibility; the generated fixtures are committed alongside it.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
C02 = HERE.parents[2] / "_artifacts" / "c02-agent-adapter" / "dsh"


def trim(source: Path, target: Path, keep_types: set[str]) -> None:
    page = json.loads(source.read_text(encoding="utf-8"))
    records = []
    for record in page.get("records", []):
        if record.get("type") != "event":
            continue
        event = record.get("event", {})
        if event.get("type") in keep_types:
            records.append({"type": "event", "event": event})
    payload = {
        "records": records,
        "hasMore": False,
        "note": f"trimmed from C-02 evidence {source.name}",
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    trim(
        C02 / "t2-history.json",
        HERE / "dsh_history_completed.json",
        {"user/message", "assistant/message", "turn/end"},
    )


if __name__ == "__main__":
    main()
