"""Call-scoped, model-selected source sections. No semantic search or ranking.

The catalog is navigation, never factual evidence. A read returns the complete
selected authored section and its actual ancestors, or explicitly withholds it.
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
SECTIONS_MAX_CHARS = 12000
MAX_SELECTED_SECTIONS = 3
_PATH = re.compile(r"[1-9][0-9]*(?:\.[1-9][0-9]*){0,5}\Z")

SECTION_TOOL_DESCRIPTION = (
    "Read exact company source sections selected from the catalog. Send "
    "section_ids to read up to three sections, OR catalog_offset to see another "
    "catalog page. Catalog headings are navigation, not answers."
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
    },
    "additionalProperties": False,
    "oneOf": [{"required": ["section_ids"]}, {"required": ["catalog_offset"]}],
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


def _chain(catalog: SectionCatalog, selected: dict) -> list[dict] | None:
    """Path and parent must agree; Markdown heading depths need not be consecutive."""
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
        if sum(row["source_id"] == current["source_id"] and row.get("path") == path
               for row in catalog.nodes) != 1:
            return None
        seen.add(current["id"])
        chain.append(current)
        parent_path = path.rpartition(".")[0]
        if not parent_path:
            if current.get("parent_id") is not None:
                return None
            break
        parents = [row for row in catalog.nodes if row["source_id"] == current["source_id"]
                   and row["source_version"] == current["source_version"] and row.get("path") == parent_path]
        if len(parents) != 1:
            return None
        parent = parents[0]
        if ("parent_id" in current and current["parent_id"] != parent["id"]
                or type(parent.get("depth")) is not int or parent["depth"] >= depth):
            return None
        current = parent
    return list(reversed(chain))


def render_catalog_page(catalog: SectionCatalog | None, offset: int = 0,
                        *, max_chars: int = CATALOG_MAX_CHARS) -> dict:
    if not isinstance(catalog, SectionCatalog):
        return _result(catalog, "unavailable", reason="knowledge_unavailable")
    if type(offset) is not int or offset < 0 or offset > len(catalog.nodes):
        return _result(catalog, "unavailable", reason="invalid_catalog_offset")
    entries, used = [], 0
    for row in catalog.nodes[offset:]:
        chain = _chain(catalog, row)
        entry = {"section_id": row["section_id"], "source_id": row["source_id"],
                 "heading": row["heading"],
                 "path": [item["heading"] for item in chain] if chain else [],
                 "readable": chain is not None}
        size = len(json.dumps(entry, ensure_ascii=False)) + 1
        if used + size > max_chars:
            if not entries:
                return _result(catalog, "too_large", reason="catalog_entry_too_large")
            break
        entries.append(entry)
        used += size
    next_offset = offset + len(entries)
    complete = next_offset == len(catalog.nodes)
    return _result(catalog, "catalog", entries=entries, complete=complete,
                   next_offset=None if complete else next_offset)


def read_sections(catalog: SectionCatalog | None, section_ids, *, max_chars: int = SECTIONS_MAX_CHARS) -> dict:
    if not isinstance(catalog, SectionCatalog):
        return _result(catalog, "unavailable", reason="knowledge_unavailable")
    if (not isinstance(section_ids, list) or not 1 <= len(section_ids) <= MAX_SELECTED_SECTIONS
            or any(not isinstance(ref, str) for ref in section_ids) or len(set(section_ids)) != len(section_ids)):
        return _result(catalog, "unavailable", reason="invalid_section_ids")
    by_ref = {row["section_id"]: row for row in catalog.nodes}
    passages, included, used = [], set(), 0
    for ref in section_ids:
        row = by_ref.get(ref)
        chain = _chain(catalog, row) if row is not None else None
        if not chain:
            return _result(catalog, "unavailable", reason="section_context_unavailable")
        if not row["content"].strip():
            return _result(catalog, "unavailable", reason="section_has_no_body")
        for section in chain:
            text = f"{section['heading']}\n{section['content']}".strip()
            # Do not remove a malicious ancestor while retaining its child's price.
            if scan_for_injection(text):
                return _result(catalog, "unavailable", reason="unsafe_source")
            if section["id"] in included:
                continue
            used += len(text) + 2
            if used > max_chars:
                return _result(catalog, "too_large", reason="section_context_too_large")
            included.add(section["id"])
            passages.append({"node_id": section["id"], "version": section["version"],
                             "source_id": section["source_id"], "source_version": section["source_version"],
                             "section_id": section["section_id"], "text": text})
    return _result(catalog, "available", passages=passages,
                   text="\n\n".join(passage["text"] for passage in passages))


def run_section_request(catalog: SectionCatalog | None, arguments: Any) -> dict:
    """Shared traditional/native argument boundary; no caller scope parameters."""
    if not isinstance(arguments, dict) or set(arguments) not in ({"section_ids"}, {"catalog_offset"}):
        return _result(catalog, "unavailable", reason="invalid_arguments")
    if "catalog_offset" in arguments:
        return render_catalog_page(catalog, arguments["catalog_offset"])
    return read_sections(catalog, arguments["section_ids"])


def serialize_section_result(result: dict) -> str:
    """One source-body copy on the wire; keep the full result for diagnostics."""
    payload = {key: value for key, value in result.items() if key != "text"}
    if not payload.get("passages"):
        payload.pop("passages", None)
    return json.dumps(payload, ensure_ascii=False)
