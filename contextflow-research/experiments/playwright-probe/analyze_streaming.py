#!/usr/bin/env python3
"""Analyze streaming-state HTML dumps: turn state values, stop button, structure deltas.
Privacy: attribute values and counts only; no message text is printed."""
from __future__ import annotations

import collections
import glob
import json
import re
import sys
from pathlib import Path

files = sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else "*.html"))
for f in files:
    html = Path(f).read_text(encoding="utf-8", errors="replace")
    row = {"file": Path(f).name, "bytes": len(html)}
    row["turn_state"] = collections.Counter(re.findall(r'data-talvt-turn-state="([^"]+)"', html)).most_common()
    row["roles"] = collections.Counter(re.findall(r'data-conversation-role="([^"]+)"', html)).most_common()
    row["message_metadata"] = len(re.findall(r'data-chatgpt-message-metadata="true"', html))
    row["turn_keys"] = len(re.findall(r'data-turn-key="', html))
    row["selection_message_ids"] = len(re.findall(r'data-chatgpt-selection-message-id="', html))
    # stop / generating indicators
    labels = collections.Counter(re.findall(r'aria-label="([^"]{1,40})"', html))
    row["stop_labels"] = {k: v for k, v in labels.items() if any(w in k for w in ("停止", "Stop", "生成", "取消"))}
    row["has_stop_text"] = "停止生成" in html or "停止回答" in html
    row["has_send_button"] = 'aria-label="发送"' in html
    row["has_composer"] = "ProseMirror" in html
    row["aria_busy"] = collections.Counter(re.findall(r'aria-busy="([^"]+)"', html)).most_common()
    row["data_is_streaming"] = collections.Counter(re.findall(r'data-is-streaming="([^"]+)"', html)).most_common()
    row["data_streaming"] = collections.Counter(re.findall(r'data-streaming="([^"]+)"', html)).most_common()
    row["loading_earlier"] = "正在加载更早的消息" in html
    print(json.dumps(row, ensure_ascii=False))
