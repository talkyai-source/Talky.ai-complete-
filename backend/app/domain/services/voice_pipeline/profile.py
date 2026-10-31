"""Safe per-turn provenance; source content is represented only by digests."""
import hashlib
import json
import re


def turn_profile(session, system_prompt: str, knowledge_block: str | None) -> dict:
    checksum = getattr(session, "_knowledge_snapshot_checksum", None)
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        checksum = None
    # Evidence is current only when this turn actually requested retrieval.
    evidence = getattr(session, "_knowledge_evidence", {}) if knowledge_block else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    raw_passages = evidence.get("passages")
    passages = [p for p in raw_passages if isinstance(p, dict)] if isinstance(raw_passages, list) else []
    versioned = bool(passages) and all(p.get("version") is not None for p in passages)
    versions = [(
        str(p.get("node_id") or ""), str(p.get("version")),
        str(p.get("source_id") or ""), str(p.get("source_version")),
    ) for p in passages]
    mode = getattr(session, "knowledge_mode", None)
    status = evidence.get("status")
    return {
        "instructions_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "knowledge_mode": mode if isinstance(mode, str) and mode in {"inline", "retrieve", "map_retrieve"} else "none",
        "knowledge_snapshot_checksum": checksum,
        "knowledge_version_status": "snapshot" if checksum else "versioned_passages" if versioned else "unversioned",
        "knowledge_versions_sha256": hashlib.sha256(json.dumps(versions, separators=(",", ":")).encode()).hexdigest() if versioned else None,
        "knowledge_block_sha256": hashlib.sha256(knowledge_block.encode("utf-8")).hexdigest() if knowledge_block else None,
        "knowledge_status": status if isinstance(status, str) and status in {"matched", "weak_match", "no_match", "unavailable"} else "not_retrieved_this_turn",
        "knowledge_passage_count": len(passages),
    }
