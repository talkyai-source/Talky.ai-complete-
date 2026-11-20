"""In-memory memo of campaign-wide dialler gates (paused, in-flight cap, call gap).

A campaign-wide gate answers the same for every job of the campaign. Without a
memo the worker re-ran six to ten database statements for EVERY queued job
every few seconds (1.8 million log lines for 213 calls on 20 Nov 2026).

The memo only skips repeated checking. It is per process and in memory: it
expires on its own, it is dropped when the worker learns the campaign changed,
and losing it (a restart) just brings back the old behaviour. It never allows
anything: a memo hit only RE-SCHEDULES a job, it never places one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

REASON_PAUSED = "campaign_paused"
REASON_CAPACITY = "batch_capacity"
REASON_CALL_GAP = "call_gap"

#: Longest extra spread (seconds) given to followers of a capacity / call-gap
#: block, so a long queue does not come back in one burst every few seconds.
MAX_FOLLOWER_SPREAD_S = 120


@dataclass
class GateBlock:
    reason: str
    #: monotonic time the blocked jobs are re-scheduled to
    blocked_until: float
    #: monotonic time up to which the memo is trusted without a fresh check
    fresh_until: float
    followers: int = 0


class CampaignGateMemo:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._blocks: Dict[Tuple[str, str], GateBlock] = {}
        # Jobs this worker parked for a pause, per campaign, so a resume
        # promotes only those. Kept apart from the blocks on purpose: a
        # placed call (clear) or a different gate (block) must not lose them.
        self._parked: Dict[Tuple[str, str], Set[str]] = {}

    @staticmethod
    def _key(tenant_id: object, campaign_id: object) -> Tuple[str, str]:
        return (str(tenant_id), str(campaign_id))

    def block(
        self,
        tenant_id: object,
        campaign_id: object,
        reason: str,
        delay_s: float,
        *,
        fresh_s: Optional[float] = None,
    ) -> GateBlock:
        now = self._clock()
        entry = GateBlock(
            reason=reason,
            blocked_until=now + max(1.0, float(delay_s)),
            fresh_until=now + (float(delay_s) if fresh_s is None else float(fresh_s)),
        )
        self._blocks[self._key(tenant_id, campaign_id)] = entry
        return entry

    def lookup(self, tenant_id: object, campaign_id: object) -> Optional[GateBlock]:
        """The live block of this campaign, or None (expired entries are dropped)."""
        key = self._key(tenant_id, campaign_id)
        entry = self._blocks.get(key)
        if entry is None:
            return None
        now = self._clock()
        if now >= entry.blocked_until:
            del self._blocks[key]
            return None
        if now >= entry.fresh_until:
            # Parked-until is still ahead (a paused campaign) but the memo is no
            # longer trusted: the next job gets a real check. The entry stays so
            # the worker's resume poll can still watch this campaign.
            return None
        return entry

    def clear(self, tenant_id: object, campaign_id: object) -> None:
        self._blocks.pop(self._key(tenant_id, campaign_id), None)

    def remaining(self, entry: GateBlock) -> int:
        """Whole seconds (at least 1) until the block's re-schedule time."""
        return max(1, int(round(entry.blocked_until - self._clock())))

    def follower_delay(self, entry: GateBlock) -> int:
        """Delay for the next follower job of this block.

        Paused: every follower goes to the same time. Capacity / call gap: the
        followers are spread (one extra second per two followers, capped) so the
        queue returns as a trickle, never as one burst per window.
        """
        entry.followers += 1
        base = self.remaining(entry)
        if entry.reason == REASON_PAUSED:
            return base
        return base + min(entry.followers // 2, MAX_FOLLOWER_SPREAD_S)

    def note_parked(self, tenant_id: object, campaign_id: object, job_id: object) -> None:
        self._parked.setdefault(self._key(tenant_id, campaign_id), set()).add(str(job_id))

    def parked_campaigns(self) -> List[Tuple[str, str]]:
        """Campaigns with jobs parked for a pause that no resume has promoted yet."""
        return [k for k, v in self._parked.items() if v]

    def take_parked(self, tenant_id: object, campaign_id: object) -> Set[str]:
        return self._parked.pop(self._key(tenant_id, campaign_id), set())
