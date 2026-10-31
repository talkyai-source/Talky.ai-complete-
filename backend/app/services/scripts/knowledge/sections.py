"""Call-scoped, model-selected source sections. No semantic search or ranking.

The catalog is navigation, never factual evidence. A read returns the complete
selected authored section and its actual ancestors, explicitly incomplete
contiguous source pages, or a bounded unavailability result.
Outbound calls pin a snapshot at setup; inbound calls keep admission's snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.services.scripts.prompts.prompt_safety import scan_for_injection

CATALOG_MAX_CHARS = 8000
CATALOG_LABEL_MAX_CHARS = 160
SECTIONS_MAX_CHARS = 12000
SOURCE_PAGE_MAX_BYTES = 12000
SOURCE_CONTEXT_MAX_BYTES = 48000
MAX_SELECTED_SECTIONS = 3
_PATH = re.compile(r"[1-9][0-9]*(?:\.[1-9][0-9]*){0,5}\Z")

SECTION_TOOL_DESCRIPTION = (
    "Read exact company source sections selected from the catalog. Send "
    "section_ids to read up to three sections; a heading that only groups others "
    "returns the sections under it, and more_section_ids names any that did not fit. "
    "source_offset continues an oversized "
    "source. Send catalog_parent='' for roots or an exact section ID for its children; "
    "catalog_offset pages that list. Catalog labels are navigation, not answers. "
    "source_page is incomplete: read every contiguous part of the same context_digest "
    "before answering from it, including its conditions. The last part alone is not the whole source."
)
SECTION_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "section_ids": {
            "type": "array", "items": {"type": "string"},
            "minItems": 1, "maxItems": MAX_SELECTED_SECTIONS,
            "description": "Exact section_id values from this call's catalog.",
        },
        "catalog_offset": {
            "type": "integer", "minimum": 0,
            "description": "The next_offset returned by a catalog page; zero shows the first page.",
        },
        "catalog_parent": {
            "type": "string",
            "description": "Empty string lists roots; an exact section_id lists only its direct children.",
        },
        "source_offset": {
            "type": "integer", "minimum": 0,
            "description": "Exact next_source_offset (character offset) from a source_page for the same section_ids.",
        },
    },
    "additionalProperties": False,
    "oneOf": [
        {"required": ["section_ids"], "not": {"anyOf": [{"required": ["catalog_offset"]}, {"required": ["catalog_parent"]}]}},
        {"anyOf": [{"required": ["catalog_offset"]}, {"required": ["catalog_parent"]}],
         "not": {"anyOf": [{"required": ["section_ids"]}, {"required": ["source_offset"]}]}},
    ],
}


@dataclass(frozen=True)
class SectionCatalog:
    tenant_id: str
    campaign_id: str
    source_policy: str
    checksum: str
    nodes: tuple[dict, ...]


def build_section_catalog(nodes, *, tenant_id: str, campaign_id: str,
                          source_policy: str, checksum: str | None = None) -> SectionCatalog:
    """Copy trusted scoped rows; reject inconsistent identities/revisions."""
    if not tenant_id or not campaign_id or source_policy not in ("call_snapshot", "admission_snapshot", "current_read"):
        raise ValueError("Knowledge scope is unavailable")
    copied, identities, revisions = [], set(), {}
    for raw in nodes:
        if not isinstance(raw, dict):
            raise ValueError("Malformed knowledge snapshot")
        if raw.get("enabled") is False:
            continue
        row = deepcopy(raw)
        for key in ("id", "source_id"):
            if row.get(key) is None or not str(row[key]).strip():
                raise ValueError("Missing source identity")
            row[key] = str(row[key])
        for key, expected in (("tenant_id", tenant_id), ("campaign_id", campaign_id)):
            if row.get(key) is not None and str(row[key]) != str(expected):
                raise ValueError("Knowledge scope mismatch")
        revision = row.get("source_version")
        if type(revision) is not int or revision < 1:
            raise ValueError("Missing source revision")
        if row["source_id"] in revisions and revisions[row["source_id"]] != revision:
            raise ValueError("Mixed source revisions")
        revisions[row["source_id"]] = revision
        if row["id"] in identities:
            raise ValueError("Duplicate section identity")
        identities.add(row["id"])
        if row.get("parent_id") is not None:
            row["parent_id"] = str(row["parent_id"])
        for key in ("heading", "content"):
            if not isinstance(row.get(key), str):
                raise ValueError("Malformed authored section")
        version = row.get("version", row.get("updated_at"))
        if version is None or not str(version).strip():
            raise ValueError("Missing section revision")
        row["version"] = str(version)
        copied.append(row)
    # Stable refs belong to this snapshot, not to a global index or an LLM query.
    def path_order(row):
        path = row.get("path")
        return tuple(map(int, path.split("."))) if isinstance(path, str) and _PATH.fullmatch(path) else ()
    copied.sort(key=lambda row: (row["source_id"], path_order(row), row["id"]))
    digest = hashlib.sha256(json.dumps(copied, sort_keys=True, default=str).encode()).hexdigest()
    for index, row in enumerate(copied):
        row["section_id"] = f"k{digest[:8]}_{index + 1}"
    return SectionCatalog(str(tenant_id), str(campaign_id), source_policy,
                          checksum or digest, tuple(copied))


async def load_section_catalog(pool, tenant_id: str, campaign_id: str) -> SectionCatalog:
    """One scoped read at call setup; no per-turn refresh or status writes."""
    from app.core.db_utils import acquire_with_tenant
    from app.services.scripts.knowledge.retrieval import load_current_knowledge_nodes
    if pool is None or not tenant_id or not campaign_id:
        raise ValueError("Knowledge scope is unavailable")
    async with acquire_with_tenant(pool, tenant_id) as conn:
        nodes = await load_current_knowledge_nodes(conn, tenant_id, campaign_id)
    return build_section_catalog(nodes, tenant_id=tenant_id, campaign_id=campaign_id,
                                 source_policy="call_snapshot")


def _result(catalog, status: str, *, reason: str | None = None, **values) -> dict:
    result = {"status": status, "passages": [], "text": "",
              "source_policy": catalog.source_policy if isinstance(catalog, SectionCatalog) else None}
    if reason:
        result["reason"] = reason
    return {**result, **values}


def _path_index(catalog: SectionCatalog) -> dict:
    by_path = {}
    for row in catalog.nodes:
        if isinstance(row.get("path"), str):
            by_path.setdefault((row["source_id"], row["path"]), []).append(row)
    return by_path


def _chain(catalog: SectionCatalog, selected: dict, *, by_path: dict | None = None) -> list[dict] | None:
    """Path and parent must agree; Markdown heading depths need not be consecutive."""
    if by_path is None:
        by_path = _path_index(catalog)
    chain, seen, current = [], set(), selected
    while current is not None:
        path, depth = current.get("path"), current.get("depth")
        # Older flat snapshots have no tree metadata. Absence is not evidence
        # for inventing ancestors; any actual hierarchy hint still must validate.
        if (not chain and path in (None, "") and current.get("parent_id") is None
                and (depth is None or type(depth) is int and depth in (0, 1))):
            return [current]
        if (current["id"] in seen or len(chain) >= 6 or not isinstance(path, str)
                or not _PATH.fullmatch(path) or type(depth) is not int or not 0 <= depth <= 6):
            return None
        if len(by_path.get((current["source_id"], path), ())) != 1:
            return None
        seen.add(current["id"])
        chain.append(current)
        parent_path = path.rpartition(".")[0]
        if not parent_path:
            if current.get("parent_id") is not None:
                return None
            break
        parents = [row for row in by_path.get((current["source_id"], parent_path), ())
                   if row["source_version"] == current["source_version"]]
        if len(parents) != 1:
            return None
        parent = parents[0]
        if ("parent_id" in current and current["parent_id"] != parent["id"]
                or type(parent.get("depth")) is not int or parent["depth"] >= depth):
            return None
        current = parent
    return list(reversed(chain))


def render_catalog_page(catalog: SectionCatalog | None, offset: int = 0,
                        *, max_chars: int = CATALOG_MAX_CHARS, parent: str | None = None) -> dict:
    if not isinstance(catalog, SectionCatalog):
        return _result(catalog, "unavailable", reason="knowledge_unavailable")
    rows = list(catalog.nodes)
    by_path = _path_index(catalog)
    chains = {row["id"]: _chain(catalog, row, by_path=by_path) for row in rows}
    child_counts = {}
    for chain in chains.values():
        if chain is not None and len(chain) > 1:
            key = chain[-2]["id"]
            child_counts[key] = child_counts.get(key, 0) + 1
    if parent is not None:
        if not isinstance(parent, str):
            return _result(catalog, "unavailable", reason="invalid_catalog_parent")
        if parent:
            selected = next((row for row in rows if row["section_id"] == parent), None)
            if selected is None or chains[selected["id"]] is None:
                return _result(catalog, "unavailable", reason="invalid_catalog_parent")
        rows = [row for row in rows if (
            (chains[row["id"]] is None or len(chains[row["id"]]) == 1) if not parent else
            chains[row["id"]] is not None and len(chains[row["id"]]) > 1
            and chains[row["id"]][-2]["section_id"] == parent)]
    if type(offset) is not int or offset < 0 or offset > len(rows):
        return _result(catalog, "unavailable", reason="invalid_catalog_offset")
    entries, used = [], 0
    for row in rows[offset:]:
        chain = chains[row["id"]]
        labels = [row["heading"], *[item["heading"] for item in chain or ()]]
        shortened = [label if len(label) <= CATALOG_LABEL_MAX_CHARS
                     else label[:CATALOG_LABEL_MAX_CHARS - 1] + "…" for label in labels]
        entry = {"section_id": row["section_id"], "source_id": row["source_id"],
                 "heading": shortened[0], "path": shortened[1:],
                 "labels_truncated": labels != shortened,
                 "child_count": child_counts.get(row["id"], 0),
                 "readable": chain is not None}
        size = len(json.dumps(entry, ensure_ascii=False)) + 1
        if used + size > max_chars:
            if not entries:
                complete = offset + 1 == len(rows)
                return _result(catalog, "too_large", reason="catalog_entry_too_large",
                               offset=offset, total_sections=len(rows), catalog_parent=parent,
                               skipped_offset=offset, complete=complete,
                               next_offset=None if complete else offset + 1)
            break
        entries.append(entry)
        used += size
    next_offset = offset + len(entries)
    complete = next_offset == len(rows)
    return _result(catalog, "catalog", entries=entries, complete=complete,
                   offset=offset, total_sections=len(rows), catalog_parent=parent,
                   next_offset=None if complete else next_offset)


def _readable_targets(catalog: SectionCatalog, row: dict, chains: dict) -> list[dict]:
    """What a request for ``row`` means: the section itself when it has text;
    for a heading that only groups others, the sections under it that do.

    Test calls 6b9cd4c4 and 08b2791d (2026-10-08): the model asked for a group
    heading together with its children, the whole read failed on the heading,
    and it spent its lookup rounds retrying combinations; two turns read
    nothing and one ended with the reply "0".
    """
    if row["content"].strip():
        return [row]
    return [other for other in catalog.nodes
            if other is not row and other["content"].strip()
            and (chain := chains.get(other["id"]))
            and any(item["id"] == row["id"] for item in chain[:-1])]


def read_sections(catalog: SectionCatalog | None, section_ids, *, max_chars: int = SECTIONS_MAX_CHARS) -> dict:
    """Read selected sections with their ancestors.

    A group heading expands to the sections under it (in document order, up to
    one page of text); whatever did not fit is named in ``more_section_ids``,
    so a further read can fetch it. Readable selections are never discarded
    because another selection was only a heading.
    """
    if not isinstance(catalog, SectionCatalog):
        return _result(catalog, "unavailable", reason="knowledge_unavailable")
    if (not isinstance(section_ids, list) or not 1 <= len(section_ids) <= MAX_SELECTED_SECTIONS
            or any(not isinstance(ref, str) for ref in section_ids) or len(set(section_ids)) != len(section_ids)):
        return _result(catalog, "unavailable", reason="invalid_section_ids")
    by_ref = {row["section_id"]: row for row in catalog.nodes}
    by_path = _path_index(catalog)
    chains = {row["id"]: _chain(catalog, row, by_path=by_path) for row in catalog.nodes}
    targets, expanded, empty, seen = [], {}, [], set()
    for ref in section_ids:
        row = by_ref.get(ref)
        if row is None or not chains.get(row["id"]):
            return _result(catalog, "unavailable", reason="section_context_unavailable")
        found = _readable_targets(catalog, row, chains)
        if not found:
            empty.append(ref)
            continue
        if found[0] is not row:
            expanded[ref] = [item["section_id"] for item in found]
        for item in found:
            if item["id"] not in seen:
                seen.add(item["id"])
                targets.append((item, found[0] is not row))
    if not targets:
        return _result(catalog, "unavailable", reason="section_has_no_body", empty_section_ids=empty)
    expansion_budget = min(max_chars, SECTIONS_MAX_CHARS)
    passages, included, used, remaining = [], set(), 0, []
    for index, (target, from_heading) in enumerate(targets):
        chain = chains[target["id"]]
        texts = [(section, f"{section['heading']}\n{section['content']}".strip()) for section in chain]
        # Do not remove a malicious ancestor while retaining its child's price.
        if any(scan_for_injection(text) for _, text in texts):
            return _result(catalog, "unavailable", reason="unsafe_source")
        new = [(section, text) for section, text in texts if section["id"] not in included]
        cost = sum(len(text) + 2 for _, text in new)
        if used + cost > (expansion_budget if from_heading else max_chars):
            if not passages:
                return _result(catalog, "too_large", reason="section_context_too_large")
            remaining = [item["section_id"] for item, _ in targets[index:]]
            break
        for section, text in new:
            included.add(section["id"])
            passages.append({"node_id": section["id"], "version": section["version"],
                             "source_id": section["source_id"], "source_version": section["source_version"],
                             "section_id": section["section_id"], "text": text})
        used += cost
    extras = {}
    if expanded:
        extras["expanded"] = expanded
    if remaining:
        extras["more_section_ids"] = remaining
    if empty:
        extras["empty_section_ids"] = empty
    return _result(catalog, "available", passages=passages,
                   text="\n\n".join(passage["text"] for passage in passages), **extras)


def run_section_request(catalog: SectionCatalog | None, arguments: Any) -> dict:
    """Shared traditional/native argument boundary; no caller scope parameters."""
    if not isinstance(arguments, dict) or set(arguments) not in (
        {"section_ids"}, {"section_ids", "source_offset"}, {"catalog_offset"},
        {"catalog_parent"}, {"catalog_parent", "catalog_offset"},
    ):
        return _result(catalog, "unavailable", reason="invalid_arguments")
    if "section_ids" not in arguments:
        return render_catalog_page(catalog, arguments.get("catalog_offset", 0), parent=arguments.get("catalog_parent"))
    offset = arguments.get("source_offset", 0)
    if type(offset) is not int or offset < 0:
        return _result(catalog, "unavailable", reason="invalid_source_offset")
    # Validate the entire authored context, including every ancestor and the
    # injection check, before exposing any fragment. This does not rank facts.
    result = read_sections(catalog, arguments["section_ids"], max_chars=SOURCE_CONTEXT_MAX_BYTES)
    if result["status"] != "available":
        return result
    text = result["text"]
    if len(text.encode("utf-8")) > SOURCE_CONTEXT_MAX_BYTES:
        return _result(catalog, "too_large", reason="section_context_too_large")
    if offset >= len(text):
        return _result(catalog, "unavailable", reason="invalid_source_offset")
    if offset == 0 and len(text.encode("utf-8")) <= SOURCE_PAGE_MAX_BYTES:
        return {**result, "context_complete": True}
    # Character offsets avoid breaking a Unicode code point. The byte ceiling
    # bounds each body on the wire independently of the tool-round ceiling.
    part = text[offset:].encode("utf-8")[:SOURCE_PAGE_MAX_BYTES].decode("utf-8", errors="ignore")
    end = offset + len(part)
    sources, start = [], 0
    for passage in result["passages"]:
        sources.append({**{key: value for key, value in passage.items() if key != "text"},
                        "start": start, "end": start + len(passage["text"])})
        start += len(passage["text"]) + 2
    digest = hashlib.sha256(json.dumps([catalog.tenant_id, catalog.campaign_id, sources, text],
                                      ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return _result(catalog, "source_page", source_text=part, sources=sources,
                   context_digest=digest, context_complete=False, source_offset=offset,
                   source_end=end, total_chars=len(text), total_bytes=len(text.encode("utf-8")),
                   end_of_context=end == len(text), next_source_offset=end if end < len(text) else None)


def serialize_section_result(result: dict) -> str:
    """One source-body copy on the wire; keep the full result for diagnostics."""
    payload = {key: value for key, value in result.items() if key != "text"}
    if not payload.get("passages"):
        payload.pop("passages", None)
    return json.dumps(payload, ensure_ascii=False)
