#!/usr/bin/env python3
"""Extract ChatGPT role/turn-state attribute values (privacy: attribute values only)."""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

html = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
names = [
    "data-conversation-role",
    "data-chatgpt-message-metadata-role",
    "data-turn-key",
    "data-talvt-turn-state",
    "data-chatgpt-agent-turn-start",
    "data-role",
    "data-user-message-bubble",
    "data-chatgpt-selection-message-id",
    "data-chatgpt-selection-conversation-id",
    "data-above-composer-conversation-id",
    "data-map-composer-conversation",
    "data-chatgpt-search-message-ids",
]
for n in names:
    vals = collections.Counter(re.findall(re.escape(n) + r'="([^"]{0,100})"', html))
    print(n, "=>", vals.most_common(8))

print("\n--- elements carrying data-conversation-role ---")
for m in re.finditer(r"<([a-zA-Z0-9-]+)([^>]*data-conversation-role[^>]*)>", html):
    attrs = re.sub(r'"[^"]{80,}"', '"<long>"', m.group(2))
    print(m.group(1), attrs[:500])
    print()

print("--- elements carrying data-talvt-turn-state ---")
for m in re.finditer(r"<([a-zA-Z0-9-]+)([^>]*data-talvt-turn-state[^>]*)>", html):
    attrs = re.sub(r'"[^"]{80,}"', '"<long>"', m.group(2))
    print(m.group(1), attrs[:500])
    print()

print("--- elements carrying data-role ---")
for m in re.finditer(r"<([a-zA-Z0-9-]+)([^>]*\bdata-role=\"[^\"]+\"[^>]*)>", html):
    attrs = re.sub(r'"[^"]{80,}"', '"<long>"', m.group(2))
    print(m.group(1), attrs[:500])
    print()

print("--- stop / streaming related buttons ---")
for m in re.finditer(r'<button[^>]*aria-label="([^"]{1,40})"[^>]*>', html):
    if any(w in m.group(1) for w in ("停止", "Stop", "停止生成")):
        print(re.sub(r'"[^"]{80,}"', '"<long>"', m.group(0))[:400])
