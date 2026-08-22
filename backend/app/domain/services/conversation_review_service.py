"""Reviewer rating, tags and comment for one conversation (goals.md §3).

RELATIONSHIP TO CALL FEEDBACK
-----------------------------
``call_feedback`` is one *voice note* per call. This is a *structured* review —
a 1-5 rating, tags from a fixed vocabulary, an optional comment — and there is
one per USER per call, so two reviewers can rate the same conversation
independently. They are complementary and sit on the same call page.

WHY THE PROMPT IDENTITY IS COPIED, NOT JOINED
---------------------------------------------
Every review snapshots the call's prompt_template/version/hash and campaign at
the moment it is written. Joining would be less code and wrong: prompts get
re-versioned, and a review has to keep pointing at what the agent actually ran
on. The Safe Improvement Loop ("aggregate reviews by prompt version and failure
category") is only meaningful if that attribution is frozen.

REWARDS
-------
Off by default. goals.md puts reward points in P1 while review *storage* is P0,
so the ledger and its idempotency exist and are tested, and enabling them is one
environment variable rather than a deploy.

The safety properties are in the schema, not here: UNIQUE (review_id) on the
ledger means an edit — or a retried request, or a double-click — cannot credit
twice. This module only decides *whether* an award is due; the database decides
whether it can happen.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import asyncpg

from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)


class ConversationReviewError(RuntimeError):
    """Base class for expected review failures."""


class ReviewCallNotFoundError(ConversationReviewError):
    """The call does not exist, or belongs to another tenant.

    Deliberately the same error for both: telling a caller that a call exists
    but is not theirs is itself a cross-tenant disclosure.
    """


class ReviewNotFoundError(ConversationReviewError):
    pass


class InvalidReviewError(ConversationReviewError):
    pass


#: The eleven tags from goals.md §3. Mirrors the CHECK constraint in Alembic
#: 0015 — change one and you must change the other, which is why the test
#: pins them against each other.
REVIEW_TAGS: tuple[str, ...] = (
    "agent_did_not_understand",
    "agent_interrupted_caller",
    "agent_did_not_answer_question",
    "response_too_long",
    "response_too_slow",
    "agent_repeated_itself",
    "wrong_qualification_question",
    "wrong_call_outcome",
    "poor_objection_handling",
    "incorrect_information",
    "good_conversation",
)

_MAX_COMMENT = 4000

_COLUMNS = """
    id, tenant_id, call_id, campaign_id, user_id,
    rating, review_tags, comment,
    prompt_template, prompt_version, prompt_hash, llm_model,
    created_at, updated_at
"""


def rewards_enabled() -> bool:
    """P1 gate. Reviews store fine with this off; nothing else changes."""
    return os.getenv("REVIEW_REWARDS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def reward_points() -> int:
    try:
        return max(1, int(os.getenv("REVIEW_REWARD_POINTS", "10")))
    except (TypeError, ValueError):
        return 10


def reward_daily_cap() -> int:
    """Per user, per UTC day. The abuse control §3 asks for."""
    try:
        return max(1, int(os.getenv("REVIEW_REWARD_DAILY_MAX", "20")))
    except (TypeError, ValueError):
        return 20


def _clean_tags(tags: Sequence[str] | None) -> list[str]:
    """Normalise, de-duplicate, and reject anything not in the vocabulary.

    Rejecting loudly rather than dropping silently: a client sending an unknown
    tag has a bug, and swallowing it would make the resulting aggregation quietly
    incomplete instead of obviously broken.
    """
    if not tags:
        return []
    seen: list[str] = []
    for raw in tags:
        tag = (raw or "").strip().lower()
        if not tag:
            continue
        if tag not in REVIEW_TAGS:
            raise InvalidReviewError(f"Unknown review tag: {raw!r}")
        if tag not in seen:
            seen.append(tag)
    return seen


def _clean_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    text = comment.strip()
    if not text:
        return None
    if len(text) > _MAX_COMMENT:
        raise InvalidReviewError(f"Comment is too long (max {_MAX_COMMENT} characters)")
    return text


def _validate_rating(rating: Any) -> int:
    try:
        value = int(rating)
    except (TypeError, ValueError) as exc:
        raise InvalidReviewError("Rating must be a whole number from 1 to 5") from exc
    if not 1 <= value <= 5:
        raise InvalidReviewError("Rating must be between 1 and 5")
    return value


def is_reward_eligible(rating: int, tags: Sequence[str], comment: str | None) -> bool:
    """§3: "Do not reward an empty review".

    A bare rating with no tags and no comment carries no information anyone can
    act on — rewarding it just pays for clicks. Set
    REVIEW_REWARD_BARE_RATING_ELIGIBLE=true if you decide otherwise; §3
    explicitly leaves that open ("unless a simple rating is intentionally
    eligible").
    """
    if os.getenv("REVIEW_REWARD_BARE_RATING_ELIGIBLE", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return True
    return bool(tags) or bool(comment)


class ConversationReviewService:
    """Reads and writes reviews, and decides when a reward is due."""

    def __init__(self, db_pool: Any) -> None:
        self._pool = db_pool

    @staticmethod
    def _uuid(value: Any) -> UUID:
        return value if isinstance(value, UUID) else UUID(str(value))

    async def _require_call(self, tenant_id: str, call_id: str) -> Mapping[str, Any]:
        """Fetch the call, scoped to the tenant.

        The explicit ``AND tenant_id = $2`` is not redundant with RLS. The
        production database role currently bypasses row security (see task #80),
        so this filter is the only thing enforcing isolation today — and it
        should remain even once RLS is enforced, as defence in depth.
        """
        try:
            call_uuid, tenant_uuid = self._uuid(call_id), self._uuid(tenant_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ReviewCallNotFoundError("Call not found") from exc

        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """SELECT id, campaign_id, status,
                          prompt_template, prompt_version, prompt_hash
                     FROM calls WHERE id = $1 AND tenant_id = $2""",
                call_uuid, tenant_uuid,
            )
        if not row:
            raise ReviewCallNotFoundError("Call not found")
        return row

    async def get_my_review(
        self, *, tenant_id: str, call_id: str, user_id: str
    ) -> dict[str, Any] | None:
        await self._require_call(tenant_id, call_id)
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM conversation_reviews "
                "WHERE call_id = $1 AND user_id = $2",
                self._uuid(call_id), self._uuid(user_id),
            )
        return dict(row) if row else None

    async def list_for_call(self, *, tenant_id: str, call_id: str) -> list[dict[str, Any]]:
        """Every review on this call. Several reviewers may have rated it."""
        await self._require_call(tenant_id, call_id)
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM conversation_reviews "
                "WHERE call_id = $1 ORDER BY created_at",
                self._uuid(call_id),
            )
        return [dict(r) for r in rows]

    async def submit(
        self,
        *,
        tenant_id: str,
        call_id: str,
        user_id: str,
        rating: Any,
        tags: Sequence[str] | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Create this user's review, or update it if they already left one.

        Returns the review row with an ``awarded_points`` key describing what the
        reward ledger did — 0 when rewards are disabled, already granted, capped,
        or the review is too thin to earn one.
        """
        call = await self._require_call(tenant_id, call_id)
        clean_rating = _validate_rating(rating)
        clean_tags = _clean_tags(tags)
        clean_comment = _clean_comment(comment)

        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO conversation_reviews (
                    tenant_id, call_id, campaign_id, user_id,
                    rating, review_tags, comment,
                    prompt_template, prompt_version, prompt_hash
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (call_id, user_id) DO UPDATE
                   SET rating = EXCLUDED.rating,
                       review_tags = EXCLUDED.review_tags,
                       comment = EXCLUDED.comment,
                       updated_at = NOW()
                RETURNING {_COLUMNS}
                """,
                self._uuid(tenant_id), self._uuid(call_id),
                call["campaign_id"], self._uuid(user_id),
                clean_rating, clean_tags, clean_comment,
                call["prompt_template"], call["prompt_version"], call["prompt_hash"],
            )

        review = dict(row)
        review["awarded_points"] = await self._maybe_award(
            tenant_id=tenant_id,
            user_id=user_id,
            review_id=str(review["id"]),
            rating=clean_rating,
            tags=clean_tags,
            comment=clean_comment,
        )
        return review

    async def _maybe_award(
        self,
        *,
        tenant_id: str,
        user_id: str,
        review_id: str,
        rating: int,
        tags: Sequence[str],
        comment: str | None,
    ) -> int:
        """Insert a ledger entry if one is due. Never raises into the caller.

        A reward failure must not fail the review — the review is the P0, the
        points are P1, and losing someone's written feedback because a ledger
        insert hit a constraint would be the wrong trade every time.
        """
        if not rewards_enabled():
            return 0
        if not is_reward_eligible(rating, tags, comment):
            return 0
        try:
            async with acquire_with_tenant(self._pool, tenant_id) as conn:
                already = await conn.fetchval(
                    "SELECT 1 FROM review_reward_ledger WHERE review_id = $1",
                    self._uuid(review_id),
                )
                if already:
                    # The edit case. Deliberately silent: this is the normal,
                    # correct outcome of updating a review, not a problem.
                    return 0

                today = await conn.fetchval(
                    """SELECT count(*) FROM review_reward_ledger
                        WHERE user_id = $1
                          AND awarded_at >= date_trunc('day', NOW() AT TIME ZONE 'utc')""",
                    self._uuid(user_id),
                )
                cap = reward_daily_cap()
                if today >= cap:
                    logger.info(
                        "review_reward_daily_cap_reached user=%s tenant=%s count=%s cap=%s",
                        str(user_id)[:8], str(tenant_id)[:8], today, cap,
                    )
                    return 0

                points = reward_points()
                try:
                    await conn.execute(
                        """INSERT INTO review_reward_ledger
                               (tenant_id, user_id, review_id, points)
                           VALUES ($1,$2,$3,$4)""",
                        self._uuid(tenant_id), self._uuid(user_id),
                        self._uuid(review_id), points,
                    )
                except asyncpg.UniqueViolationError:
                    # Two concurrent submits for the same review. The constraint
                    # did its job; the loser awards nothing.
                    return 0
                return points
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception(
                "review_reward_failed review=%s tenant=%s — review is saved regardless",
                review_id[:8], str(tenant_id)[:8],
            )
            return 0

    async def reward_balance(self, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        """Derived from the ledger, never stored. A stored balance can drift
        from its transactions; a summed one cannot."""
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """SELECT COALESCE(SUM(points), 0)::int AS total,
                          count(*)::int AS entries,
                          count(*) FILTER (
                              WHERE awarded_at >= date_trunc('day', NOW() AT TIME ZONE 'utc')
                          )::int AS today
                     FROM review_reward_ledger WHERE user_id = $1""",
                self._uuid(user_id),
            )
        return {
            "total_points": row["total"],
            "entries": row["entries"],
            "awarded_today": row["today"],
            "daily_cap": reward_daily_cap(),
            "rewards_enabled": rewards_enabled(),
        }
