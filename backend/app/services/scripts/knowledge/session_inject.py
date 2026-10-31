"""Prepare stable campaign section catalogs before either voice runtime starts.

All saved non-none modes now use model-selected sections. Outbound/browser calls
pin published enabled source rows at setup; inbound keeps its admission snapshot.
No generated summary or full-source dump is inserted into the system prompt.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.services.scripts.knowledge.retrieval import knowledge_enabled
from app.services.scripts.knowledge.sections import (
    SectionCatalog,
    build_section_catalog,
    load_section_catalog,
)

logger = logging.getLogger(__name__)
_KNOWLEDGE_MODES = {"inline", "map_retrieve", "retrieve"}
# Budget for the whole-catalog load at call setup (not the per-turn lookup).
_KNOWLEDGE_SETUP_TIMEOUT_S = float(os.getenv("KNOWLEDGE_SETUP_TIMEOUT_MS", "3000")) / 1000.0


def _row_get(row: Any, key: str):
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


def _scope_matches(session, tenant_id, campaign_id):
    return all(not current or str(current) == expected for current, expected in (
        (getattr(session, "tenant_id", None), tenant_id),
        (getattr(session, "campaign_id", None), campaign_id),
    ))


def copy_prepared_knowledge(source, destination) -> None:
    """Transfer a prepared immutable-by-convention call context, never reload it."""
    for key in ("_knowledge_catalog", "_knowledge_snapshot_nodes", "_knowledge_snapshot_checksum", "knowledge_mode"):
        if hasattr(source, key):
            setattr(destination, key, getattr(source, key))
    catalog = getattr(source, "_knowledge_catalog", None)
    if isinstance(catalog, SectionCatalog):
        destination._knowledge_snapshot_checksum = catalog.checksum
    if not getattr(destination, "tenant_id", None):
        destination.tenant_id = getattr(source, "tenant_id", None)


def apply_pinned_campaign_knowledge(session, snapshot: Any) -> None:
    if session is None:
        return
    session._knowledge_catalog = None
    if not isinstance(snapshot, dict) or snapshot.get("enabled") is not True:
        return
    mode = str(snapshot.get("mode") or "none").lower()
    tenant_id, campaign_id = str(snapshot.get("tenant_id") or ""), str(snapshot.get("campaign_id") or "")
    if mode not in _KNOWLEDGE_MODES or not _scope_matches(session, tenant_id, campaign_id):
        return
    try:
        catalog = build_section_catalog(snapshot.get("nodes") or [], tenant_id=tenant_id,
            campaign_id=campaign_id, source_policy="admission_snapshot", checksum=snapshot.get("checksum"))
    except (ValueError, TypeError):
        logger.warning("KB_SETUP unavailable path=inbound reason=invalid_snapshot")
        return
    session.tenant_id = tenant_id
    session.knowledge_mode = mode
    session._knowledge_catalog = catalog
    session._knowledge_snapshot_nodes = list(catalog.nodes)
    session._knowledge_snapshot_checksum = catalog.checksum


async def apply_campaign_knowledge(session, campaign_row: Any, *, pool) -> None:
    if session is None:
        return
    session._knowledge_catalog = None
    if not knowledge_enabled():
        return
    mode = str(_row_get(campaign_row, "knowledge_mode") or "none").lower()
    tenant_id = str(_row_get(campaign_row, "tenant_id") or "")
    campaign_id = str(_row_get(campaign_row, "id") or getattr(session, "campaign_id", None) or "")
    if mode not in _KNOWLEDGE_MODES or not _scope_matches(session, tenant_id, campaign_id):
        return
    # Setup runs before the SIP origination, off the caller's ear, so it gets its
    # own budget and one retry. The 500 ms per-turn constant sits on the
    # first-token path and must not be reused (or raised) here.
    catalog = None
    for attempt in (1, 2):
        try:
            catalog = await asyncio.wait_for(load_section_catalog(pool, tenant_id, campaign_id),
                                             timeout=_KNOWLEDGE_SETUP_TIMEOUT_S)
            break
        except Exception as exc:
            logger.warning("KB_SETUP unavailable path=call_snapshot attempt=%s campaign=%s error_type=%s",
                           attempt, campaign_id[:8], type(exc).__name__)
    if catalog is None:
        return
    session.tenant_id = tenant_id
    session.knowledge_mode = mode
    session._knowledge_catalog = catalog
    session._knowledge_snapshot_nodes = list(catalog.nodes)
    session._knowledge_snapshot_checksum = catalog.checksum
    logger.info("KB_SETUP model_selected policy=call_snapshot campaign=%s sections=%s",
                campaign_id[:8], len(catalog.nodes))
