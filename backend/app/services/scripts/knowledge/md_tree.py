"""Deterministic Markdown -> knowledge-tree parser (item: vectorless RAG, P1).

Markdown headings (#..######) *are* the hierarchy, so we parse them directly —
no LLM splitting (reliable, instant, free). The LLM is used later only to
*enrich* each node (summary/keywords/voice_answer). A `.txt` file with no
headings collapses to a single node holding all the text (the LLM-segmentation
fallback lives in the ingest service, not here).

Output is a FLAT, ordered list of `ParsedNode` linked by `parent_index`, which
the ingest service inserts row-by-row (mapping index -> UUID for parent_id).
Pure + stateless → unit-testable in isolation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class ParsedNode:
    heading: str
    depth: int                          # heading level 1..6; 0 = synthetic root
    content: str                        # body under this heading (excludes children)
    position: int = 0                   # 0-based index among siblings
    path: str = ""                      # materialised "1.2.3" (1-based)
    parent_index: Optional[int] = None  # index into the returned list, or None at top level
    index: int = -1


def parse_markdown_tree(md: str, *, default_heading: str = "Overview") -> List[ParsedNode]:
    """Parse markdown into a flat, ordered node list linked by parent_index.

    - A heading is a child of the nearest preceding heading of smaller depth.
    - Body lines belong to the most recent heading.
    - Fenced code blocks (``` / ~~~) are never scanned for headings.
    - Text before the first heading becomes a top-level ``default_heading`` node;
      a file with no headings collapses to that single node holding all content.
    - Empty / whitespace-only input → empty list.
    """
    if not md or not md.strip():
        return []

    # --- 1. Split into ordered (depth, heading, body-lines) sections + preamble.
    preamble: list[str] = []
    sections: list[tuple[int, str, list[str]]] = []
    in_fence = False
    fence_marker = ""
    for line in md.splitlines():
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, mark
            elif line.strip().startswith(fence_marker):
                in_fence = False
            (sections[-1][2] if sections else preamble).append(line)
            continue
        if not in_fence:
            m = _HEADING_RE.match(line)
            if m:
                sections.append((len(m.group(1)), m.group(2).strip(), []))
                continue
        (sections[-1][2] if sections else preamble).append(line)

    def _text(lines: list[str]) -> str:
        return "\n".join(lines).strip()

    nodes: List[ParsedNode] = []
    sibling_next: dict[int, int] = {}   # parent_index (-1 = top level) -> next position

    def _add(heading: str, depth: int, content: str, parent_index: Optional[int]) -> int:
        key = parent_index if parent_index is not None else -1
        pos = sibling_next.get(key, 0)
        sibling_next[key] = pos + 1
        path = str(pos + 1) if parent_index is None else f"{nodes[parent_index].path}.{pos + 1}"
        node = ParsedNode(
            heading=heading, depth=depth, content=content,
            position=pos, path=path, parent_index=parent_index, index=len(nodes),
        )
        nodes.append(node)
        return node.index

    # --- 2. Synthetic top-level root for preamble (or a no-heading document).
    pre = _text(preamble)
    if pre or not sections:
        _add(default_heading, 0, pre, None)   # top-level sibling, NOT an ancestor

    # --- 3. Build the tree from sections via a depth stack of open ancestors.
    stack: list[tuple[int, int]] = []   # (depth, index)
    for depth, heading, body in sections:
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent_index = stack[-1][1] if stack else None
        idx = _add(heading, depth, _text(body), parent_index)
        stack.append((depth, idx))

    return nodes
