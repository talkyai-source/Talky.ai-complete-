"""Tests for the confirm-before-commit capture state machine + natural read-back.

Includes DEDICATED race-condition tests (explicitly requested): the state
machine must hold its invariants and lose no updates under concurrent
record/confirm/reject from many threads.

LOCAL ONLY — not committed.
"""
import threading

from app.domain.services.voice_pipeline.capture_confirmation import (
    CaptureConfirmation,
    FieldStatus,
    classify_confirmation,
    MAX_ATTEMPTS,
)
from app.services.scripts.spoken_email_normalizer import natural_email_readback


C, F = "call-1", "email"


# ── commit gate + repair loop ────────────────────────────────────────────────

def test_gate_blocks_until_confirmed():
    cc = CaptureConfirmation()
    assert cc.confirmed_value(C, F) is None
    cc.record_capture(C, F, "bob@gmail.com")
    assert cc.status(C, F) == FieldStatus.PENDING
    assert cc.needs_confirmation(C, F) is True
    assert cc.pending_value(C, F) == "bob@gmail.com"
    assert cc.confirmed_value(C, F) is None              # GATE: not yet confirmed
    cc.confirm(C, F)
    assert cc.confirmed_value(C, F) == "bob@gmail.com"


def test_correction_uncommits():
    cc = CaptureConfirmation()
    cc.record_capture(C, F, "bob@gmail.com")
    cc.confirm(C, F)
    assert cc.confirmed_value(C, F) == "bob@gmail.com"
    cc.record_capture(C, F, "rob@gmail.com")             # caller corrects
    assert cc.status(C, F) == FieldStatus.PENDING
    assert cc.confirmed_value(C, F) is None              # gate closes again
    cc.confirm(C, F)
    assert cc.confirmed_value(C, F) == "rob@gmail.com"


def test_reject_clears_and_gate_closes():
    cc = CaptureConfirmation()
    cc.record_capture(C, F, "bob@gmail.com")
    cc.confirm(C, F)
    cc.reject(C, F)
    assert cc.status(C, F) == FieldStatus.EMPTY
    assert cc.confirmed_value(C, F) is None


def test_same_confirmed_value_reheard_stays_confirmed():
    cc = CaptureConfirmation()
    cc.record_capture(C, F, "bob@gmail.com")
    cc.confirm(C, F)
    cc.record_capture(C, F, "bob@gmail.com")             # STT re-emits same value
    assert cc.confirmed_value(C, F) == "bob@gmail.com"   # stays committed


def test_exhaustion_after_max_attempts():
    cc = CaptureConfirmation()
    for i in range(MAX_ATTEMPTS + 1):
        cc.record_capture(C, F, f"v{i}@x.com")
    assert cc.status(C, F) == FieldStatus.EXHAUSTED
    assert cc.is_exhausted(C, F) is True
    assert cc.confirmed_value(C, F) is None              # never commit an exhausted field


def test_apply_caller_response_transitions():
    cc = CaptureConfirmation()
    cc.record_capture(C, F, "bob@gmail.com")
    assert cc.apply_caller_response(C, F, "umm let me think") == FieldStatus.PENDING
    assert cc.apply_caller_response(C, F, "yes that's right") == FieldStatus.CONFIRMED
    cc.record_capture(C, F, "rob@gmail.com")
    assert cc.apply_caller_response(C, F, "no, that's wrong") == FieldStatus.EMPTY


# ── classifier ───────────────────────────────────────────────────────────────

def test_classify_confirmation():
    assert classify_confirmation("yes that's right") == "affirm"
    assert classify_confirmation("yep, perfect") == "affirm"
    assert classify_confirmation("no that's not it") == "reject"
    assert classify_confirmation("actually it's bob") == "reject"
    assert classify_confirmation("no, yeah it's actually rob") == "reject"  # negation wins
    assert classify_confirmation("hmm") == "unclear"
    assert classify_confirmation("") == "unclear"


# ── natural read-back (no more robotic letter-by-letter) ─────────────────────

def test_natural_readback_word_local_part():
    assert natural_email_readback("allstateestimation@gmail.com") == \
        "allstateestimation at gmail dot com"


def test_natural_readback_word_plus_digits():
    assert natural_email_readback("john7890@gmail.com") == "john 7 8 9 0 at gmail dot com"


def test_natural_readback_spells_only_nonword_runs():
    assert natural_email_readback("xq7@gmail.com") == "x-q 7 at gmail dot com"


def test_natural_readback_multidot_domain():
    assert natural_email_readback("bob@yahoo.co.uk") == "bob at yahoo dot co dot uk"


def test_natural_readback_speaks_local_separators():
    # a literal "." is a silent TTS pause — the caller would hear "j smith" and
    # could yes-confirm jsmith@ when they meant j.smith@ (prompt-craft audit).
    assert natural_email_readback("j.smith@gmail.com") == "j dot smith at gmail dot com"
    assert natural_email_readback("john_smith@acme.com") == "john underscore smith at acme dot com"
    assert natural_email_readback("a-team@acme.com") == "a dash team at acme dot com"
    assert natural_email_readback("bob+tag@acme.com") == "bob plus tag at acme dot com"


# ── RACE CONDITIONS (the explicit ask) ───────────────────────────────────────

def test_race_no_lost_updates_under_concurrent_capture():
    """Every record_capture increments attempts under the lock. With N threads
    recording distinct values concurrently, attempts must be EXACTLY N — a
    missing lock loses increments (Python's += is not atomic) and drops below N.
    """
    cc = CaptureConfirmation()
    N = 500

    def worker(i):
        cc.record_capture("c", "email", f"v{i}@x.com")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert cc.attempts("c", "email") == N


def test_race_state_invariants_hold_under_concurrency():
    """While many threads hammer record/confirm/reject, the (status, value) pair
    must never be torn: a set status (PENDING/CONFIRMED) always has a value, and
    EMPTY always has an empty value. A continuous checker thread asserts this on
    atomic snapshots throughout the storm."""
    cc = CaptureConfirmation()
    stop = threading.Event()
    violations = []

    def checker():
        while not stop.is_set():
            st, val = cc.snapshot("c", "email")
            if st in (FieldStatus.CONFIRMED, FieldStatus.PENDING) and not val:
                violations.append(("set-status-empty-value", st))
            elif st == FieldStatus.EMPTY and val:
                violations.append(("empty-status-set-value", st, val))

    def worker(i):
        for _ in range(300):
            cc.record_capture("c", "email", f"v{i}@x.com")
            cc.confirm("c", "email")
            cc.reject("c", "email")

    chk = threading.Thread(target=checker)
    chk.start()
    workers = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    stop.set()
    chk.join()
    assert not violations, violations[:5]
