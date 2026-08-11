"""Per-call agent-name rotator.

The campaign creator supplies 1-3 names. For each outbound call, one
name is picked uniformly at random from that pool and stays stable for
the whole call.

Call-site: `campaign_service._create_job_for_lead` picks the name when
creating the DialerJob so the choice is durable in Redis and survives
restarts.
"""
from __future__ import annotations

import re
import random
from typing import Mapping, Optional, Sequence

MAX_POOL_SIZE = 3


def pick_agent_name_for_voice(
    pool: Sequence[str],
    genders: Optional[Mapping[str, str]],
    voice_gender: Optional[str],
    *,
    seed: Optional[str] = None,
) -> str:
    """Pick an agent name whose gender matches the selected voice.

    Resolution order:
      1. If we know the voice gender AND have name→gender tags, pick at
         random from the pool names tagged with that gender.
      2. If the pool has no name of that gender (or no tags), fall back to a
         built-in name of the voice's gender — so the voice never speaks a
         clearly-mismatched name.
      3. If the voice gender is unknown, fall back to the legacy random pick
         over the whole pool.

    ``seed`` (optional): when given, the pick is DETERMINISTIC for that seed —
    pass a stable per-call value (e.g. the lead or call id) so a retried or
    restarted call keeps the same agent name instead of re-rolling. When None,
    selection is uniformly random as before.

    THE POOL ALWAYS WINS (2026-07-09): campaigns configure their names in the
    UI without gender tags, and the old "no tag matched → invent a built-in
    name" branch made the agent introduce itself as a name the campaign never
    configured ("Emily") while the campaign's own instructions said "You are
    James" — a self-contradicting prompt observed in production. Gender only
    ORDERS the preference within the configured pool; it never replaces it.

    Never raises — on any inconsistency it degrades to a plain pool pick.
    """
    chooser = random.Random(seed) if seed is not None else random
    vg = (voice_gender or "").strip().lower()
    if vg in ("male", "female") and pool:
        gmap = {str(k).strip().lower(): str(v).strip().lower() for k, v in (genders or {}).items()}
        matching = [n for n in pool if n and gmap.get(str(n).strip().lower()) == vg]
        if matching:
            return chooser.choice(matching)
        # No explicit tag matched. Infer gender from the built-in name lists
        # (first token, case-insensitive) and prefer pool names that match the
        # voice — but if nothing infers, STILL pick from the pool.
        inferred = [n for n in pool if n and _inferred_gender(n) == vg]
        if inferred:
            return chooser.choice(inferred)
    # Unknown voice gender / nothing inferred → the configured pool decides.
    return pick_agent_name(pool, seed=seed)


def _inferred_gender(name: str) -> Optional[str]:
    """Best-effort gender for an untagged name via the built-in name lists.
    First token only ("Sarah jones" → "sarah"). None when unknown/ambiguous."""
    try:
        from app.domain.services.global_ai_config import FEMALE_NAMES, MALE_NAMES
        first = str(name).strip().split()[0].strip().lower()
        if not first:
            return None
        male = any(first == str(n).strip().lower() for n in MALE_NAMES)
        female = any(first == str(n).strip().lower() for n in FEMALE_NAMES)
        if male and not female:
            return "male"
        if female and not male:
            return "female"
        return None
    except Exception:
        return None


def _positively_conflicts(
    name: str, gmap: Mapping[str, str], voice_gender: str
) -> bool:
    """True only when this name has a KNOWN gender that differs from the voice.

    An explicit operator tag wins over inference. A name we cannot place
    ("Alex", "Sam", "Jordan", anything not in the built-in lists) returns
    False — unknown is not a conflict, and treating it as one would let a
    perfectly usable unisex name be thrown away.
    """
    tag = str(gmap.get(str(name).strip().lower(), "")).strip().lower()
    gender = tag if tag in ("male", "female") else _inferred_gender(name)
    return gender in ("male", "female") and gender != voice_gender


def pool_wholly_conflicts(
    pool: Sequence[str],
    genders: Optional[Mapping[str, str]],
    voice_gender: Optional[str],
) -> bool:
    """True when EVERY configured name conflicts with the voice BY INFERENCE.

    This is the narrow case where the operator's pool cannot be satisfied at
    all: a male voice whose only names are female, or vice versa. If even one
    name matches — or is merely unknown ("Sam", "Jordan") — this is False and
    ``pick_agent_name_for_voice`` can do its job within the pool.

    THE ESCAPE HATCH IS A TAG THAT MATCHES THE VOICE — not any tag at all.

    Rewritten 2026-08-12. This used to return False whenever ANY pool name
    carried ANY gender tag, on a stated premise: "campaign forms never sent
    tags, so ``agent_name_genders`` is null on real campaigns". That premise
    silently stopped being true. Campaign 50847cc9 now stores
    ``{'Sarah': 'female'}`` against a MALE voice, and because a tag was
    present the whole conflict check switched itself off — production logged
    ``agent_name_voice_gender_mismatch ... 'Sarah' ... voice_gender=male``
    twenty-one times in fourteen days while this function reported no conflict.

    It also contradicted its own caller: ``resolve_name_against_voice``
    documents the escape hatch as "tag it explicitly in ``agent_name_genders``
    WITH THE VOICE'S GENDER". That is the coherent reading, and it is what we
    now implement:

      * tag == voice gender  -> deliberate casting ("yes, use this name on
        this voice"). Hands off, exactly as documented.
      * tag != voice gender  -> this is not a casting decision, it is the
        operator recording what the name obviously is. It is the STRONGEST
        evidence of a conflict, not a reason to ignore one.

    An untagged name is still judged by inference, and a name that is merely
    unknown/unisex ("Sam", "Jordan") still never counts as a conflict.
    """
    vg = (voice_gender or "").strip().lower()
    if vg not in ("male", "female"):
        return False
    names = [n for n in (pool or []) if n and str(n).strip()]
    if not names:
        return False
    gmap = {
        str(k).strip().lower(): str(v)
        for k, v in (genders or {}).items()
    }
    # A tag AGREEING with the voice is the operator's deliberate casting
    # choice — that name is usable, so the pool is satisfiable. Hands off.
    for name in names:
        tag = str(gmap.get(str(name).strip().lower(), "")).strip().lower()
        if tag == vg:
            return False
    return all(_positively_conflicts(n, gmap, vg) for n in names)


def name_is_referenced_in(text: Optional[str], pool: Sequence[str]) -> bool:
    """True if any configured name appears in the operator's own script text.

    This is the guard against re-creating the 2026-07-09 regression: a campaign
    whose ROLE/GOAL text says "You are James" must keep using James, because
    substituting a built-in name would produce a prompt that contradicts
    itself — the agent introducing itself as one name while its instructions
    assert another. Matching is on the FIRST TOKEN, case-insensitive, which is
    what a script actually writes ("You are Sarah", not "You are Sarah jones").
    """
    if not text:
        return False
    haystack = str(text).lower()
    for name in pool or []:
        parts = str(name or "").strip().split()
        if not parts:
            continue
        first = parts[0].strip().lower()
        # Bounded check so "Sam" does not match "same" / "sample".
        if len(first) < 2:
            continue
        import re as _re

        if _re.search(rf"\b{_re.escape(first)}\b", haystack):
            return True
    return False


def rename_agent_in_script(text: Optional[str], old: str, new: str) -> Optional[str]:
    """Rewrite the operator's own script so it names the agent we will speak as.

    WHY THIS EXISTS (2026-08-12). ``resolve_name_against_voice`` used to face a
    false choice when a campaign's only name conflicted with its voice AND the
    campaign's own instructions referenced that name:

      * substitute  -> the agent says "Michael" while its 9,000-character
        script asserts "You are Sarah" (the 2026-07-09 self-contradiction), or
      * keep        -> a male voice introduces itself as "Sarah" (the audible
        defect this whole module exists to prevent).

    It chose KEEP, so production campaign 50847cc9 shipped a male London voice
    saying "this is Sarah" — logged, correctly, as agent_name_conflict_kept,
    and still wrong in the caller's ear.

    There is no need to choose. Rename the agent in the script too and both
    problems disappear: the spoken name matches the voice AND the prompt agrees
    with itself.

    Matches the FIRST TOKEN on a word boundary, case-insensitively — the same
    rule ``name_is_referenced_in`` uses to detect the reference in the first
    place, so detection and rewrite can never disagree. Returns ``text``
    unchanged when there is nothing to do.
    """
    if not text or not old or not new:
        return text
    first = str(old).strip().split()
    if not first:
        return text
    token = first[0].strip()
    if len(token) < 2:
        return text
    replacement = str(new).strip().split()[0] if str(new).strip() else new
    return re.sub(
        rf"\b{re.escape(token)}\b", replacement, str(text), flags=re.IGNORECASE
    )


def substitute_name_for_voice(
    voice_gender: Optional[str], *, seed: Optional[str] = None
) -> Optional[str]:
    """A built-in name matching the voice, for use when the pool cannot be.

    Deterministic when ``seed`` is given — pass a stable per-call/per-campaign
    value so a retried call does not introduce itself with a different name
    than the attempt before it.
    """
    vg = (voice_gender or "").strip().lower()
    if vg not in ("male", "female"):
        return None
    try:
        from app.domain.services.global_ai_config import FEMALE_NAMES, MALE_NAMES

        options = list(MALE_NAMES if vg == "male" else FEMALE_NAMES)
    except Exception:  # pragma: no cover - defensive
        return None
    if not options:
        return None
    chooser = random.Random(seed) if seed is not None else random
    return chooser.choice(sorted(options))


def pick_agent_name(pool: Sequence[str], *, seed: Optional[str] = None) -> str:
    """Return one agent name from the pool.

    The pool is passed through light validation — an empty pool or one
    larger than MAX_POOL_SIZE is a configuration error the campaign
    creation form should have caught.

    ``seed`` (optional): when given, the pick is DETERMINISTIC for that seed
    (pass a stable per-call value so a retry keeps the same name); when None,
    the pick is uniformly random as before.

    Raises
    ------
    ValueError
        If the pool is empty or exceeds MAX_POOL_SIZE, or contains a
        non-string / blank entry.
    """
    if not pool:
        raise ValueError("agent-name pool is empty")
    if len(pool) > MAX_POOL_SIZE:
        raise ValueError(
            f"agent-name pool has {len(pool)} entries, "
            f"max is {MAX_POOL_SIZE}"
        )
    cleaned: list[str] = []
    for entry in pool:
        if not isinstance(entry, str):
            raise ValueError(f"agent-name entry must be str, got {type(entry).__name__}")
        name = entry.strip()
        if not name:
            raise ValueError("agent-name entry is blank")
        cleaned.append(name)
    chooser = random.Random(seed) if seed is not None else random
    return chooser.choice(cleaned)


def validate_pool(pool: Sequence[str]) -> list[str]:
    """Validate and normalize an agent-name pool. Used by
    CampaignCreateRequest to reject bad input at the API boundary.

    Returns the normalized list (stripped, non-empty). Raises ValueError
    with a user-friendly message if invalid.
    """
    if not pool:
        raise ValueError("Provide at least one agent name.")
    if len(pool) > MAX_POOL_SIZE:
        raise ValueError(f"Up to {MAX_POOL_SIZE} agent names — got {len(pool)}.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in pool:
        if not isinstance(entry, str):
            raise ValueError("Agent names must be plain text.")
        name = entry.strip()
        if not name:
            raise ValueError("Agent names cannot be blank.")
        key = name.lower()
        if key in seen:
            raise ValueError(f"Duplicate agent name: {name!r}.")
        seen.add(key)
        cleaned.append(name)
    return cleaned
