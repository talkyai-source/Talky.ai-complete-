"""Unit tests for the per-call agent-name rotator."""
from __future__ import annotations

import pytest

from app.services.scripts.prompts.agent_name_rotator import (
    MAX_POOL_SIZE,
    pick_agent_name,
    validate_pool,
)


def test_pick_from_single_entry():
    assert pick_agent_name(["Solo"]) == "Solo"


def test_pick_covers_all_entries():
    pool = ["Alex", "Sam", "Jordan"]
    picks = {pick_agent_name(pool) for _ in range(500)}
    assert picks == set(pool), f"Expected uniform distribution, got {picks}"


def test_empty_pool_raises():
    with pytest.raises(ValueError, match="empty"):
        pick_agent_name([])


def test_oversized_pool_raises():
    with pytest.raises(ValueError, match="max is"):
        pick_agent_name(["A", "B", "C", "D"])


def test_blank_entry_raises():
    with pytest.raises(ValueError, match="blank"):
        pick_agent_name(["Alex", "   "])


def test_validate_pool_trims_and_rejects_duplicates():
    assert validate_pool(["  Alex  ", "Sam"]) == ["Alex", "Sam"]
    with pytest.raises(ValueError, match="Duplicate"):
        validate_pool(["Alex", "alex"])


def test_validate_pool_rejects_oversized():
    too_many = ["A"] * (MAX_POOL_SIZE + 1)
    with pytest.raises(ValueError, match=f"Up to {MAX_POOL_SIZE}"):
        validate_pool(too_many)


def test_validate_pool_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        validate_pool([])
