"""Tests for the Flux sensitive-capture fixes + the spoken-email normalizer.

From a live-call diagnosis (2026-06-24):
  1. HYBRID email capture — the deterministic normalizer converts only the fixed
     email syntax and pins an UNAMBIGUOUS single-token local; multi-word and
     carrier-prefixed spoken locals return None and are left for the LLM to
     assemble and read back (retiring the brittle carrier-word list).
  2. Flux capture mode uses Deepgram's recommended sensitive-capture config
     (eot_timeout 7-10s, eot_threshold ~0.9) and DISABLES eager EOT so a
     half-spelled email/number isn't answered speculatively.
  3. numerals=true is sent so digits / numeric email parts format as digits.

LOCAL ONLY — not committed.
"""
from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_speech,
    spell_out_email,
)
from app.infrastructure.stt.deepgram_flux import (
    DeepgramFluxSTTProvider,
    CAPTURE_EOT_TIMEOUT_MS,
    CAPTURE_EOT_THRESHOLD,
)


# ── 1. Normalizer: the hybrid contract (deterministic syntax, LLM assembles) ──
# The brittle carrier-word stripping + multi-word joining was retired (2026-06-24).
# The deterministic layer pins ONLY an unambiguous single-token local; multi-word
# and carrier-prefixed spoken locals return None and are left for the LLM.

def test_multiword_spoken_local_defers_to_llm():
    # The exact live-call utterance: a multi-word local ("all state estimation")
    # is no longer joined by regex — the LLM assembles + confirms it. No more
    # period-glued "yes." leaking in either, because we don't stitch at all.
    assert extract_email_from_speech("Yes. All state estimation at g mail dot com.") is None
    for lead in ("Yeah.", "Sure,", "Okay.", "Yes."):
        assert extract_email_from_speech(f"{lead} bob smith at gmail dot com") is None


def test_carrier_phrase_defers_to_llm():
    assert extract_email_from_speech("you can send me on bob at yahoo dot com") is None


def test_single_token_spoken_local_is_pinned():
    # Unambiguous single-token locals are still pinned deterministically.
    assert extract_email_from_speech("bob at yahoo dot com") == "bob@yahoo.com"


def test_legitimate_dotted_local_part_preserved():
    # "john dot smith" -> "john.smith" is ONE token after dot-substitution, so it
    # stays an unambiguous, pinned local.
    out = extract_email_from_speech("john dot smith at gmail dot com")
    assert out == "john.smith@gmail.com", out


def test_written_email_passthrough():
    out = extract_email_from_speech("my email is john@gmail.com")
    assert out == "john@gmail.com", out


def test_spell_out_email_readback():
    # spell_out_email is retained (no longer on the live read-back path, which now
    # reads naturally) and still spells a canonical address letter by letter.
    assert spell_out_email("allstateestimation@gmail.com") == (
        "a-l-l-s-t-a-t-e-e-s-t-i-m-a-t-i-o-n at gmail dot com"
    )


# ── 2. Flux capture mode — Deepgram-aligned thresholds + eager disabled ───────

def _flux():
    f = DeepgramFluxSTTProvider.__new__(DeepgramFluxSTTProvider)
    f._keyterms = []
    f._capture_keyterms = []
    f._pending_config = {}
    f._model = "flux-general-en"
    f._encoding = "linear16"
    f._sample_rate = 16000
    f._eot_threshold = 0.85
    f._eot_timeout_ms = 500
    f._eager_eot_threshold = 0.7
    f._keyterm_params = lambda: []
    f._meta_params = lambda call_id: []
    return f


def test_capture_constants_in_deepgram_recommended_range():
    # Deepgram: raise eot_timeout to 7000-10000 + eot_threshold ~0.9 for capture.
    assert 7000 <= CAPTURE_EOT_TIMEOUT_MS <= 10000, CAPTURE_EOT_TIMEOUT_MS
    assert 0.85 <= CAPTURE_EOT_THRESHOLD <= 0.95, CAPTURE_EOT_THRESHOLD


def test_enter_capture_mode_relaxes_eot_and_disables_eager():
    f = _flux()
    f.enter_capture_mode("call-1")
    th = f._pending_config["call-1"]["thresholds"]
    assert th["eot_timeout_ms"] == CAPTURE_EOT_TIMEOUT_MS
    assert th["eot_threshold"] == CAPTURE_EOT_THRESHOLD
    # eager disabled: eager == eot → no speculative half-email commit.
    assert th["eager_eot_threshold"] == CAPTURE_EOT_THRESHOLD


def test_reset_capture_mode_restores_normal_config():
    f = _flux()
    f.reset_capture_mode("call-1")
    th = f._pending_config["call-1"]["thresholds"]
    assert th["eot_timeout_ms"] == 500
    assert th["eot_threshold"] == 0.85
    assert th["eager_eot_threshold"] == 0.7


# ── 3. numerals param ────────────────────────────────────────────────────────

def test_numerals_sent_by_default():
    params = _flux()._build_connection_params("call-1")
    assert ("numerals", "true") in params


def test_numerals_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setenv("FLUX_NUMERALS", "false")
    params = _flux()._build_connection_params("call-1")
    assert ("numerals", "true") not in params
