"""Review validation, reward eligibility, and the tag vocabulary (goals.md §3).

WHAT IS TESTED HERE AND WHAT IS NOT
-----------------------------------
The pure decisions live here: what counts as a valid tag, a valid rating, and
whether a review has earned a reward. They are the parts that can be wrong
silently.

The SQL behaviours — one review per user per call, edits updating in place, the
reward ledger refusing a second award — are enforced by database constraints
rather than by Python, so asserting them against a fake would only prove the
fake agrees with itself. Those are exercised against a real PostgreSQL inside a
rolled-back transaction by scripts/verify_conversation_reviews.py.

THE TAG VOCABULARY IS DUPLICATED ON PURPOSE
--------------------------------------------
Once in the service (for validation) and once in Alembic 0015 (as a CHECK
constraint). Duplication is the right call — the database must reject a bad tag
even if a future caller bypasses the service — but duplication drifts, and a
drifted CHECK rejects rows the service thinks are fine. The test below reads the
migration file and compares, so drift fails here rather than in production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.domain.services.conversation_review_service import (
    REVIEW_TAGS,
    InvalidReviewError,
    _clean_comment,
    _clean_tags,
    _validate_rating,
    is_reward_eligible,
    reward_daily_cap,
    reward_points,
    rewards_enabled,
)

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "Alembic" / "versions" / "0015_conversation_reviews.py"
)


# ── the vocabulary ──────────────────────────────────────────────────────────


def test_the_eleven_tags_from_the_spec_are_all_present():
    """goals.md §3 lists exactly eleven. Fewer means a reviewer cannot say what
    went wrong; more means someone added one without updating the CHECK."""
    assert len(REVIEW_TAGS) == 11
    assert len(set(REVIEW_TAGS)) == 11, "duplicate tag"
    for expected in ("agent_interrupted_caller", "response_too_long",
                     "wrong_call_outcome", "good_conversation"):
        assert expected in REVIEW_TAGS


def test_the_service_vocabulary_matches_the_database_check_constraint():
    """THE DRIFT GUARD. See the module docstring."""
    src = MIGRATION.read_text(encoding="utf-8")
    block = src.split("REVIEW_TAGS = (")[1].split(")")[0]
    in_migration = tuple(re.findall(r'"([a-z_]+)"', block))
    assert in_migration == REVIEW_TAGS, (
        "the service and the migration disagree about the tag vocabulary:\n"
        f"  service  : {REVIEW_TAGS}\n  migration: {in_migration}"
    )


# ── tags ────────────────────────────────────────────────────────────────────


def test_known_tags_are_accepted_and_normalised():
    assert _clean_tags([" Response_Too_Long ", "good_conversation"]) == [
        "response_too_long", "good_conversation",
    ]


def test_duplicate_tags_collapse():
    assert _clean_tags(["response_too_long", "response_too_long"]) == ["response_too_long"]


def test_an_unknown_tag_is_rejected_loudly():
    """Not dropped silently. A client sending a bad tag has a bug, and
    swallowing it makes the resulting aggregation quietly incomplete rather
    than obviously broken."""
    with pytest.raises(InvalidReviewError, match="Unknown review tag"):
        _clean_tags(["response_to_long"])  # a real typo, one character out


def test_no_tags_is_valid():
    assert _clean_tags(None) == []
    assert _clean_tags([]) == []
    assert _clean_tags(["", "   "]) == []


# ── rating ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [1, 2, 3, 4, 5, "4"])
def test_ratings_one_to_five_are_accepted(value):
    assert 1 <= _validate_rating(value) <= 5


@pytest.mark.parametrize("value", [0, 6, -1, 99])
def test_ratings_outside_the_range_are_rejected(value):
    with pytest.raises(InvalidReviewError, match="between 1 and 5"):
        _validate_rating(value)


@pytest.mark.parametrize("value", [None, "", "excellent", 2.5j])
def test_non_numeric_ratings_are_rejected(value):
    with pytest.raises(InvalidReviewError):
        _validate_rating(value)


# ── comment ─────────────────────────────────────────────────────────────────


def test_a_blank_comment_becomes_none_not_an_empty_string():
    """So "no comment" is one value in the database, not two."""
    assert _clean_comment("   ") is None
    assert _clean_comment(None) is None
    assert _clean_comment("  real feedback ") == "real feedback"


def test_an_overlong_comment_is_rejected_rather_than_truncated():
    """Truncating loses the end of what someone wrote without telling them."""
    with pytest.raises(InvalidReviewError, match="too long"):
        _clean_comment("x" * 4001)


# ── reward eligibility ──────────────────────────────────────────────────────


def test_a_bare_rating_earns_nothing_by_default():
    """§3: "Do not reward an empty review". A lone star rating carries nothing
    anyone can act on, so paying for it just buys clicks."""
    assert is_reward_eligible(5, [], None) is False


def test_a_rating_with_a_tag_is_eligible():
    assert is_reward_eligible(2, ["response_too_long"], None) is True


def test_a_rating_with_a_comment_is_eligible():
    assert is_reward_eligible(4, [], "the agent talked over me twice") is True


def test_the_bare_rating_rule_is_configurable(monkeypatch):
    """§3 leaves it open: "unless a simple rating is intentionally eligible"."""
    monkeypatch.setenv("REVIEW_REWARD_BARE_RATING_ELIGIBLE", "true")
    assert is_reward_eligible(5, [], None) is True


# ── reward configuration ────────────────────────────────────────────────────


def test_rewards_are_off_by_default():
    """Review storage is P0; reward points are P1. Shipping the mechanism
    disabled means enabling it is an env var, not a deploy."""
    assert rewards_enabled() is False


def test_rewards_can_be_switched_on(monkeypatch):
    monkeypatch.setenv("REVIEW_REWARDS_ENABLED", "true")
    assert rewards_enabled() is True


@pytest.mark.parametrize("raw", ["", "not-a-number", "0", "-5"])
def test_broken_reward_settings_fall_back_to_a_sane_value(monkeypatch, raw):
    """A malformed env var must not award zero or negative points, and must not
    crash a review submission."""
    monkeypatch.setenv("REVIEW_REWARD_POINTS", raw)
    monkeypatch.setenv("REVIEW_REWARD_DAILY_MAX", raw)
    assert reward_points() >= 1
    assert reward_daily_cap() >= 1
