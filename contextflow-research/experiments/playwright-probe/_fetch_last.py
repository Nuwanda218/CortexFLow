import asyncio, json, sys
from pathlib import Path
import websockets

URI = "ws://127.0.0.1:8766/ws?token=dev-token-cf-exp-002"
SID = "6aeb4db5-d930-83ee-8b14-0cf74ef0a84d"
OUT = Path(r"D:\CortexFlow\contextflow-research\_artifacts\chatgpt-latest-reply.md")

async def main():
    async with websockets.connect(URI, origin="http://127.0.0.1:8766") as ws:
        await ws.send(json.dumps({"id": "req-fetch-last", "type": "get_snapshot", "session_id": SID}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "welcome":
                continue
            if msg.get("id") == "req-fetch-last":
                break
    if not msg.get("success"):
        print("FAILED:", msg.get("error"))
        return 1
    snap = msg["data"]["snapshot"]
    msgs = snap["messages"]
    assistants = [m for m in msgs if m["role"] == "assistant"]
    last = assistants[-1] if assistants else None
    lines = [
        f"# ChatGPT conversation {SID}",
        f"captured_at: {msg['data']['captured_at']}",
        f"messages: {len(msgs)}  generation: {snap['generation']['state']}",
        "",
    ]
    for m in msgs:
        lines.append(f"## [{m['order']}] {m['role']} ({m['text_length']} chars)")
        lines.append(m["content"])
        lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes), last assistant len={last['text_length'] if last else 0}")
    return 0

sys.exit(asyncio.run(main()))
