#!/usr/bin/env python3
"""Structural analysis of a dumped ChatGPT page HTML.

Privacy: this script only reports tag/attribute statistics and short attribute
values (class names, data-* names, aria-labels). It deliberately does NOT print
message text. The dumped HTML is deleted by the caller.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

html = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
out: dict = {"bytes": len(html)}

# data-* attribute names
data_attrs = collections.Counter(re.findall(r'\b(data-[a-z0-9-]+)\s*=', html))
out["data_attributes_top"] = data_attrs.most_common(40)

# data-testid values
testids = collections.Counter(re.findall(r'data-testid="([^"]+)"', html))
out["data_testid_top"] = testids.most_common(30)

# message author role
roles = collections.Counter(re.findall(r'data-message-author-role="([^"]+)"', html))
out["message_author_roles"] = roles.most_common()

# message ids (count only)
out["message_id_count"] = len(re.findall(r'data-message-id="', html))

# article elements and their attributes
articles = re.findall(r'<article\b([^>]*)>', html)
out["article_count"] = len(articles)
out["article_attr_samples"] = [re.sub(r'"[^"]{80,}"', '"<long>"', a)[:300] for a in articles[:3]]

# contenteditable / editor
out["contenteditable_count"] = len(re.findall(r'contenteditable="(true|false|plaintext-only)"', html))
out["prosemirror_count"] = html.count("ProseMirror")
out["editor_class_samples"] = list(dict.fromkeys(re.findall(r'class="([^"]*ProseMirror[^"]*)"', html)))[:3]

# streaming / busy signals
out["aria_busy_values"] = collections.Counter(re.findall(r'aria-busy="([^"]+)"', html)).most_common()
out["data_is_streaming"] = collections.Counter(re.findall(r'data-is-streaming="([^"]+)"', html)).most_common()
out["data_streaming"] = collections.Counter(re.findall(r'data-streaming="([^"]+)"', html)).most_common()

# math
out["math_tag_count"] = len(re.findall(r'<math\b', html))
out["katex_count"] = html.count("katex")

# code blocks
out["pre_count"] = len(re.findall(r'<pre\b', html))
out["code_count"] = len(re.findall(r'<code\b', html))

# buttons by aria-label (short labels only, dedup)
labels = collections.Counter(re.findall(r'aria-label="([^"]{1,40})"', html))
interesting = {k: v for k, v in labels.items() if any(w in k for w in ("停止", "复制", "发送", "分享", "编辑", "重新生成", "朗读", "评价", "更多", "侧边栏", "新聊天", "附件", "听写", "模型"))}
out["aria_labels_interesting"] = sorted(interesting.items(), key=lambda x: -x[1])[:30]

# lazy loading / virtualization hints
out["loading_earlier_text"] = "正在加载更早的消息" in html
out["scroll_container_candidates"] = len(re.findall(r'overflow-y:\s*(auto|scroll)', html))

# turn containers
out["conversation_turn_testid"] = testids.get("conversation-turn", 0)
out["turn_count_hint"] = len(re.findall(r'data-testid="conversation-turn-', html))

# composer form
out["form_count"] = len(re.findall(r'<form\b', html))

print(json.dumps(out, ensure_ascii=False, indent=2))
