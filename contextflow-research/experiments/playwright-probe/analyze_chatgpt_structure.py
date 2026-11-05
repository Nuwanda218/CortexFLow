#!/usr/bin/env python3
"""Targeted discovery of ChatGPT message/turn structure (privacy: no message text)."""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

html = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
out: dict = {}

# 1) all attribute names that look message/turn/role related
attr_names = collections.Counter(re.findall(r'\b([a-zA-Z:-]*(?:message|turn|author|role|stream|conversation)[a-zA-Z:-]*)\s*=', html, re.I))
out["related_attribute_names"] = attr_names.most_common(40)

# 2) values of the most promising attributes
for name in ["data-chatgpt-message-metadata", "data-markdown-copy", "data-markdown-han-text", "data-message-author-role", "data-author", "data-role", "data-turn"]:
    vals = collections.Counter(re.findall(re.escape(name) + r'="([^"]{0,120})"', html))
    if vals:
        out[name] = vals.most_common(10)

# 3) elements carrying data-chatgpt-message-metadata: tag + class + short attrs
els = re.findall(r'<([a-zA-Z0-9-]+)([^>]*data-chatgpt-message-metadata[^>]*)>', html)
out["metadata_elements"] = []
for tag, attrs in els[:6]:
    attrs_short = re.sub(r'"[^"]{100,}"', '"<long>"', attrs)
    out["metadata_elements"].append({"tag": tag, "attrs": attrs_short[:500]})

# 4) all data-testid values
out["all_testids"] = collections.Counter(re.findall(r'data-testid="([^"]+)"', html)).most_common(40)

# 5) class names containing message/turn/assistant/user
classes = collections.Counter()
for c in re.findall(r'class="([^"]+)"', html):
    for tok in c.split():
        if any(k in tok.lower() for k in ("message", "turn", "assistant", "user", "conversation", "markdown", "stream")):
            classes[tok] += 1
out["related_classes"] = classes.most_common(40)

# 6) role-ish text in attributes (aria-label / data-*)
out["aria_labels_all"] = collections.Counter(re.findall(r'aria-label="([^"]{1,30})"', html)).most_common(40)

# 7) composer structure
composer = re.findall(r'<form\b[^>]*>', html)
out["forms"] = [re.sub(r'"[^"]{100,}"', '"<long>"', f)[:400] for f in composer]

# 8) scroll containers
out["overflow_style_count"] = len(re.findall(r'overflow[^;"]{0,40}(auto|scroll)', html))

print(json.dumps(out, ensure_ascii=False, indent=2))
