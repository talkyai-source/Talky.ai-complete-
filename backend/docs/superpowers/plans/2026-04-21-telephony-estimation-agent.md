# Telephony Estimation Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make telephony outbound calls speak first with a natural estimation-agent greeting (All States Estimation), using the voice from global/campaign config, with a lean system prompt that guides the LLM to act as a home-repair estimation expert — without touching the Ask AI agent.

**Architecture:** Create `telephony_session_config.py` (mirrors `ask_ai_session_config.py`) to own all telephony defaults: agent name pool, company name, greeting builder, and estimation system prompt. `telephony_bridge.py` imports from it instead of inlining config. The random agent name is picked once at session creation and baked into both the system prompt and `agent_config.agent_name` so it never drifts mid-call. First-speaker default changed from `"user"` to `"agent"`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, asyncio

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| **Create** | `backend/app/domain/services/telephony_session_config.py` | Agent names pool, company constant, greeting builder, estimation system prompt, `build_telephony_session_config()` |
| **Modify** | `backend/app/api/v1/endpoints/telephony_bridge.py` | Remove inline `_build_telephony_session_config` + `_build_outbound_greeting`, import from new module, change first-speaker default to `"agent"` |
| **Create** | `backend/tests/unit/test_telephony_session_config.py` | Unit tests for the new config module |
| **Create** | `backend/docs/future-changes/telephony-estimation-agent.md` | Documents every hardcoded value and the exact production migration steps |

---

## Task 1: Create `telephony_session_config.py`

**Files:**
- Create: `backend/app/domain/services/telephony_session_config.py`

- [ ] **Step 1: Write the failing test first**

Create `backend/tests/unit/test_telephony_session_config.py`:

```python
"""
Unit tests for telephony_session_config module.
Tests the estimation agent config, greeting builder, and session config builder.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestAgentNames:
    def test_agent_names_pool_has_at_least_ten_names(self):
        from app.domain.services.telephony_session_config import AGENT_NAMES
        assert len(AGENT_NAMES) >= 10

    def test_all_names_are_non_empty_strings(self):
        from app.domain.services.telephony_session_config import AGENT_NAMES
        for name in AGENT_NAMES:
            assert isinstance(name, str) and len(name) > 0


class TestBuildTelephonyGreeting:
    def test_greeting_contains_agent_name(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("John", "All States Estimation")
        assert "John" in result

    def test_greeting_contains_company_name(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("John", "All States Estimation")
        assert "All States Estimation" in result

    def test_greeting_mentions_estimate(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("Sarah", "TestCo")
        assert "estimate" in result.lower() or "repair" in result.lower()

    def test_greeting_is_a_non_empty_string(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("Alex", "TestCo")
        assert isinstance(result, str) and len(result) > 0


class TestEstimationSystemPrompt:
    def test_system_prompt_template_has_agent_name_slot(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        assert "{agent_name}" in TELEPHONY_ESTIMATION_SYSTEM_PROMPT

    def test_system_prompt_template_has_company_name_slot(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        assert "{company_name}" in TELEPHONY_ESTIMATION_SYSTEM_PROMPT

    def test_system_prompt_forbids_ai_reveal(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        prompt_lower = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.lower()
        assert "ai" in prompt_lower or "robot" in prompt_lower

    def test_system_prompt_formats_cleanly(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        rendered = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
            agent_name="John", company_name="All States Estimation"
        )
        assert "John" in rendered
        assert "All States Estimation" in rendered
        assert "{" not in rendered  # no unfilled slots


class TestBuildTelephonySessionConfig:
    def _mock_global_config(self):
        cfg = MagicMock()
        cfg.tts_provider = "cartesia"
        cfg.tts_voice_id = "test-voice-id"
        cfg.tts_model = "sonic-3"
        cfg.llm_model = "llama-3.1-8b-instant"
        cfg.llm_temperature = 0.6
        cfg.llm_max_tokens = 90
        return cfg

    def test_returns_voice_session_config(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        from app.domain.services.voice_orchestrator import VoiceSessionConfig
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert isinstance(config, VoiceSessionConfig)

    def test_session_type_is_telephony(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.session_type == "telephony"

    def test_agent_name_is_set_and_non_empty(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config, AGENT_NAMES
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.agent_config.agent_name in AGENT_NAMES

    def test_agent_name_appears_in_system_prompt(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.agent_config.agent_name in config.system_prompt

    def test_company_name_appears_in_system_prompt(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config, TELEPHONY_COMPANY_NAME
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert TELEPHONY_COMPANY_NAME in config.system_prompt

    def test_uses_global_config_voice(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        mock_cfg = self._mock_global_config()
        mock_cfg.tts_voice_id = "my-custom-voice"
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=mock_cfg,
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.voice_id == "my-custom-voice"

    def test_gateway_type_browser_is_respected(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="browser")
        assert config.gateway_type == "browser"

    def test_two_calls_may_get_different_names(self):
        """Name is random — run 50 builds; at least 2 distinct names expected."""
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            names = {
                build_telephony_session_config(gateway_type="telephony").agent_config.agent_name
                for _ in range(50)
            }
        assert len(names) > 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_telephony_session_config.py -v 2>&1 | head -40
```

Expected: `ModuleNotFoundError` or `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Create `telephony_session_config.py`**

Create `backend/app/domain/services/telephony_session_config.py`:

```python
"""
Telephony Session Configuration — Estimation Agent

Single source of truth for all outbound telephony call defaults.
Mirrors ask_ai_session_config.py so the pattern stays consistent.

TEMPORARY HARDCODES — see backend/docs/future-changes/telephony-estimation-agent.md
for the exact production migration steps.  Every hardcoded value is marked with
# TODO(production) so they are easy to grep.
"""
import random

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.services.voice_orchestrator import VoiceSessionConfig
from app.domain.services.global_ai_config import get_global_config

# ---------------------------------------------------------------------------
# TODO(production): Replace with company name from campaign.script_config
#                   when campaign creation UI provides it.
# ---------------------------------------------------------------------------
TELEPHONY_COMPANY_NAME = "All States Estimation"

# ---------------------------------------------------------------------------
# TODO(production): Replace with per-campaign name pool configured in campaign
#                   creation UI.  Names should be culturally appropriate for
#                   the target market; ask the client during onboarding.
# ---------------------------------------------------------------------------
AGENT_NAMES = [
    "John", "Sarah", "Michael", "Emily", "David",
    "Jessica", "Chris", "Ashley", "Ryan", "Amanda",
    "James", "Melissa", "Daniel", "Stephanie", "Matthew",
    "Nicole", "Andrew", "Rachel", "Joshua", "Lauren",
]

# ---------------------------------------------------------------------------
# Estimation system prompt.
# Lean phone guardrails + estimation expert flow — no rigid script.
# Uses Python str.format() slots: {agent_name}, {company_name}.
#
# TODO(production): The base guardrails section stays as-is.
#                   The PURPOSE / CONVERSATION blocks should come from
#                   campaign.system_prompt so clients can customise the
#                   script without a code deploy.
# ---------------------------------------------------------------------------
TELEPHONY_ESTIMATION_SYSTEM_PROMPT = """You are {agent_name}, a professional estimator representative calling on behalf of {company_name}.

IDENTITY: You are a human professional. If the caller asks whether you are a robot, AI, computer, or any automated system — firmly deny it: "No, not at all — I'm just calling from {company_name}." Never hint, imply, or joke otherwise. Do not acknowledge any question about your nature beyond this denial.

PURPOSE: You are offering free home repair estimates. Every turn must stay on this topic.
- If the caller asks personal questions, off-topic questions, or anything unrelated to home repair: acknowledge once and redirect — "I appreciate that, but I'm just calling today about home repair estimates — do you have any work coming up?"
- Never answer anything outside the scope of home repair estimation.

CONVERSATION (adapt naturally — no rigid script):
1. After the caller shows interest: ask what type of repair work they need
2. Ask for their address and a clear description of the damage or repair
3. Based on their description, give a confident rough estimate range on the call — you are the expert
4. Ask for their Gmail: "I can send you a full detailed breakdown — what's your email address?"
5. Close warmly: "Perfect — feel free to call us back anytime if you have more questions."

REPLY RULES:
- 1–2 sentences per turn, hard limit
- No filler openers: no "Sure", "Absolutely", "Of course", "Great question"
- Natural contractions — speak like a person
- One question per turn — never stack questions
- If interrupted, stop talking and listen

PHONE MANNERS:
- "Are you there?" / "Can you hear me?" → "Yes, loud and clear."
- "How are you?" → brief honest answer then return to purpose
- If unsure about a repair detail, ask one short clarifying question"""


def build_telephony_greeting(agent_name: str, company_name: str) -> str:
    """
    Return the opener the agent speaks immediately when the callee answers.

    Synthesized directly via TTS (no LLM round-trip) so first audio lands
    within ~100ms of answer — same latency pattern as Ask AI's pre-baked greeting.

    TODO(production): greeting template should come from campaign.prompt_config
                      greeting_override when that field is populated in the UI.
    """
    return (
        f"Hi there, my name is {agent_name} — I'm calling from {company_name}. "
        f"I just had a quick question: do you have any home repair work coming up "
        f"that you might need a free estimate for?"
    )


def build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign=None,  # TODO(production): typed as Campaign once campaign UI is wired
) -> VoiceSessionConfig:
    """
    Build a VoiceSessionConfig for an outbound telephony estimation call.

    Parameters
    ----------
    gateway_type:
        "telephony" for Asterisk HTTP-callback path.
        "browser"   for FreeSWITCH mod_audio_fork WebSocket path.
    campaign:
        Reserved for future dynamic config.  Pass None for now — all values
        fall through to the hardcoded defaults above.
        TODO(production): When campaign UI is ready, read company_name,
        agent_name pool, voice_id, and system_prompt from this object.
    """
    global_config = get_global_config()

    # TODO(production): Use campaign.voice_id when campaign creation UI
    #                   provides it; fall back to global config as-is.
    tts_provider_type = global_config.tts_provider
    tts_voice_id = global_config.tts_voice_id

    # TODO(production): Use company name from campaign.script_config.
    company_name = TELEPHONY_COMPANY_NAME

    # One name per session — baked into the system prompt and agent_config so
    # the LLM always knows who it is and never drifts to a different name mid-call.
    # TODO(production): Pick from campaign-specific name pool.
    agent_name = random.choice(AGENT_NAMES)

    system_prompt = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        company_name=company_name,
    )

    agent_config = AgentConfig(
        agent_name=agent_name,
        company_name=company_name,
        business_type="home repair estimation",
        goal=AgentGoal.INFORMATION_GATHERING,
        tone="professional, warm, and expert",
        rules=ConversationRule(
            do_not_say_rules=[
                "Never reveal or hint you are an AI or automated system",
                "Never discuss topics outside home repair estimation",
                "1-2 sentences per response maximum",
                "No filler openers: Sure / Absolutely / Of course / Great",
            ]
        ),
        flow=ConversationFlow(max_objection_attempts=2),
        response_max_sentences=2,
    )

    return VoiceSessionConfig(
        gateway_type=gateway_type,
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type=tts_provider_type,
        stt_model="flux-general-en",
        stt_sample_rate=8000,
        stt_encoding="linear16",
        stt_eot_threshold=0.85,
        stt_eot_timeout_ms=500,
        stt_eager_eot_threshold=0.4,
        llm_model=global_config.llm_model,
        llm_temperature=global_config.llm_temperature,
        llm_max_tokens=global_config.llm_max_tokens,
        voice_id=tts_voice_id,
        tts_model=global_config.tts_model,
        tts_sample_rate=8000,
        gateway_sample_rate=8000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,
        mute_during_tts=False,
        session_type="telephony",
        campaign_id="telephony",
        lead_id="sip-caller",
        agent_config=agent_config,
        system_prompt=system_prompt,
    )
```

- [ ] **Step 4: Run tests — expect them to pass**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_telephony_session_config.py -v
```

Expected: All tests **PASS**.

If `ImportError` on `AgentGoal` — verify `app/domain/models/agent_config.py` exports it (it does — line 10).

- [ ] **Step 5: Commit**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add app/domain/services/telephony_session_config.py tests/unit/test_telephony_session_config.py
git commit -m "feat: add telephony_session_config for estimation agent

Mirrors ask_ai_session_config.py pattern. Owns agent name pool,
company constant, greeting builder, and lean estimation system prompt.
All hardcoded values marked TODO(production) for future campaign UI wiring."
```

---

## Task 2: Update `telephony_bridge.py`

**Files:**
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py`

This task replaces the inline `_build_telephony_session_config` and `_build_outbound_greeting` functions with imports from the new module, and changes the first-speaker default to `"agent"`.

- [ ] **Step 1: Write the failing test**

Add this file: `backend/tests/unit/test_telephony_bridge_first_speaker.py`

```python
"""
Tests that telephony_bridge first-speaker and greeting wiring are correct.
Does not start any real providers — mocks everything below the bridge layer.
"""
import os
import pytest
from unittest.mock import patch, MagicMock


class TestOutboundFirstSpeaker:
    def test_default_is_agent(self):
        """TELEPHONY_FIRST_SPEAKER not set → default must be 'agent'."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEPHONY_FIRST_SPEAKER", None)
            # Re-import to pick up env state
            import importlib
            import app.api.v1.endpoints.telephony_bridge as bridge
            importlib.reload(bridge)
            assert bridge._outbound_first_speaker() == "agent"

    def test_env_override_user(self):
        """Setting TELEPHONY_FIRST_SPEAKER=user overrides the default."""
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
            import importlib
            import app.api.v1.endpoints.telephony_bridge as bridge
            importlib.reload(bridge)
            assert bridge._outbound_first_speaker() == "user"

    def test_env_override_agent_explicit(self):
        """Setting TELEPHONY_FIRST_SPEAKER=agent returns 'agent'."""
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "agent"}):
            import importlib
            import app.api.v1.endpoints.telephony_bridge as bridge
            importlib.reload(bridge)
            assert bridge._outbound_first_speaker() == "agent"
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_telephony_bridge_first_speaker.py::TestOutboundFirstSpeaker::test_default_is_agent -v
```

Expected: **FAIL** — default is currently `"user"`, not `"agent"`.

- [ ] **Step 3: Edit `telephony_bridge.py` — add import, replace functions, fix default**

**3a — Add import at the top of the file (after the existing imports, around line 41):**

Find this block:
```python
from app.domain.services.call_guard import CallGuard, GuardDecision, GuardResult
from app.domain.services.abuse_detection import AbuseDetectionService
```

Replace with:
```python
from app.domain.services.call_guard import CallGuard, GuardDecision, GuardResult
from app.domain.services.abuse_detection import AbuseDetectionService
from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
    build_telephony_greeting,
)
```

**3b — Replace `_outbound_first_speaker()` (lines 93–103):**

Find:
```python
def _outbound_first_speaker() -> str:
    """
    Who speaks first on an outbound (campaign) call after the callee answers.

    Returns "user" or "agent".  Default is "user" — the callee typically says
    "Hello?" and the AI should respond to that rather than barging in on top of
    them.  Set TELEPHONY_FIRST_SPEAKER=agent to restore the older behaviour of
    the AI speaking the opening line itself.
    """
    val = (os.getenv("TELEPHONY_FIRST_SPEAKER") or "user").strip().lower()
    return "agent" if val == "agent" else "user"
```

Replace with:
```python
def _outbound_first_speaker() -> str:
    """
    Who speaks first on an outbound (campaign) call after the callee answers.

    Default is "agent" — the estimation agent speaks an immediate greeting so
    the callee never hears dead silence after picking up.
    Set TELEPHONY_FIRST_SPEAKER=user in env to revert to waiting for the
    callee to say "Hello?" first (useful for inbound-style testing).
    """
    val = (os.getenv("TELEPHONY_FIRST_SPEAKER") or "agent").strip().lower()
    return "user" if val == "user" else "agent"
```

**3c — Replace `_build_telephony_session_config()` (lines 106–195):**

Find the entire function from:
```python
def _build_telephony_session_config(gateway_type: str = "browser"):
```
…through its closing `)`  at line 195 and replace it with:

```python
def _build_telephony_session_config(gateway_type: str = "browser"):
    """
    Thin shim kept for call-site compatibility.
    All logic now lives in telephony_session_config.build_telephony_session_config().
    """
    return build_telephony_session_config(gateway_type=gateway_type)
```

**3d — Replace `_build_outbound_greeting()` (lines 202–220):**

Find:
```python
def _build_outbound_greeting(session) -> str:
    """
    Build a short, immediate greeting from the campaign's agent_config fields.

    Mirrors the Ask AI approach (ask_ai_ws.py) of passing a pre-baked string to
    the TTS provider directly — no LLM round-trip, no Groq TTFT latency.
    The LLM governs all subsequent turns; the opener only needs to be natural.
    """
    agent_config = getattr(session, "agent_config", None)
    agent_name = (
        getattr(agent_config, "agent_name", None) if agent_config else None
    ) or "your assistant"
    company = (
        getattr(agent_config, "company_name", None) if agent_config else None
    ) or ""

    if company:
        return f"Hi, this is {agent_name} from {company}. How can I help you today?"
    return f"Hi, this is {agent_name}. How can I help you today?"
```

Replace with:
```python
def _build_outbound_greeting(session) -> str:
    """
    Build the estimation agent's opening line from the session's agent_config.

    Delegates to telephony_session_config.build_telephony_greeting() so the
    greeting and the system prompt always reference the same agent_name and
    company_name — they were both set in build_telephony_session_config().
    """
    agent_config = getattr(session, "agent_config", None)
    agent_name = (
        getattr(agent_config, "agent_name", None) if agent_config else None
    ) or "your assistant"
    company = (
        getattr(agent_config, "company_name", None) if agent_config else None
    ) or "All States Estimation"
    return build_telephony_greeting(agent_name, company)
```

- [ ] **Step 4: Run the first-speaker test to confirm it passes**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_telephony_bridge_first_speaker.py -v
```

Expected: All 3 tests **PASS**.

- [ ] **Step 5: Run the full session config test suite to confirm nothing regressed**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_telephony_session_config.py tests/unit/test_telephony_bridge_first_speaker.py -v
```

Expected: All tests **PASS**.

- [ ] **Step 6: Run the broader unit suite to check for regressions**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/ -v --ignore=tests/unit/test_telephony_bridge_first_speaker.py -x 2>&1 | tail -30
```

Expected: No new failures. Pre-existing failures (if any) are unrelated to these changes.

- [ ] **Step 7: Commit**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add app/api/v1/endpoints/telephony_bridge.py tests/unit/test_telephony_bridge_first_speaker.py
git commit -m "feat: wire estimation agent into telephony bridge

- Import build_telephony_session_config + build_telephony_greeting from new module
- _build_telephony_session_config becomes a thin shim for call-site compat
- _build_outbound_greeting delegates to build_telephony_greeting
- TELEPHONY_FIRST_SPEAKER default changed user→agent (agent speaks first on outbound)"
```

---

## Task 3: Create future-changes documentation

**Files:**
- Create: `backend/docs/future-changes/telephony-estimation-agent.md`

- [ ] **Step 1: Create the file**

Create `backend/docs/future-changes/telephony-estimation-agent.md`:

```markdown
# Future Changes — Telephony Estimation Agent

This file documents every value that is currently hardcoded in the telephony
estimation agent and the exact production steps to make each one dynamic.

All hardcoded values in the source are tagged `# TODO(production)` so you can
find them instantly with: `grep -rn "TODO(production)" backend/app/`

---

## What Is Hardcoded Right Now

### 1. Company Name
| Detail | Value |
|--------|-------|
| **Current value** | `"All States Estimation"` |
| **File** | `backend/app/domain/services/telephony_session_config.py` — `TELEPHONY_COMPANY_NAME` |
| **Used in** | System prompt + greeting (via `build_telephony_session_config`) |

### 2. Agent Name Pool
| Detail | Value |
|--------|-------|
| **Current value** | 20 generic English first-names in `AGENT_NAMES` list |
| **File** | `backend/app/domain/services/telephony_session_config.py` — `AGENT_NAMES` |
| **Used in** | System prompt + greeting — one name picked randomly per session |

### 3. Voice Selection
| Detail | Value |
|--------|-------|
| **Current value** | Falls through to `get_global_config().tts_voice_id` (global AI options) |
| **File** | `build_telephony_session_config()` — `tts_voice_id = global_config.tts_voice_id` |
| **Note** | `campaigns.voice_id` column already exists in DB; just not read yet |

### 4. Estimation System Prompt
| Detail | Value |
|--------|-------|
| **Current value** | `TELEPHONY_ESTIMATION_SYSTEM_PROMPT` hardcoded in `telephony_session_config.py` |
| **File** | `backend/app/domain/services/telephony_session_config.py` |
| **Note** | `campaigns.system_prompt` column already exists in DB; just not read yet |

### 5. Greeting Template
| Detail | Value |
|--------|-------|
| **Current value** | `build_telephony_greeting()` returns a hardcoded English template |
| **File** | `backend/app/domain/services/telephony_session_config.py` |
| **Note** | `campaigns.prompt_config.greeting_override` JSONB field exists for this |

---

## Production Migration Steps

### Step 1 — Add campaign → call_id mapping in `telephony_bridge.py`

The bridge receives `campaign_id` at call-origination time (`/call` endpoint) but
`_on_ringing` / `_on_new_call` only have the PBX `call_id`.  Bridge them:

```python
# In telephony_bridge.py — add at module level (near _telephony_sessions)
_call_to_campaign_id: dict[str, str] = {}
```

In `make_call` endpoint, after `call_id = await _adapter.originate_call(...)`:
```python
if campaign_id:
    _call_to_campaign_id[call_id] = campaign_id
```

In `_on_call_ended(call_id)`:
```python
_call_to_campaign_id.pop(call_id, None)
```

### Step 2 — Make `build_telephony_session_config` async and accept `campaign_id`

```python
async def build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign_id: Optional[str] = None,
) -> VoiceSessionConfig:
    ...
    if campaign_id:
        from app.core.container import get_container
        db = get_container().db_client
        row = (
            db.table("campaigns")
            .select("voice_id, system_prompt, script_config")
            .eq("id", campaign_id)
            .single()
            .execute()
        )
        if row.data:
            data = row.data
            tts_voice_id = data.get("voice_id") or tts_voice_id
            if data.get("system_prompt"):
                system_prompt = data["system_prompt"]
            if data.get("script_config"):
                sc = data["script_config"]
                company_name = sc.get("company_name", company_name)
                # Optionally use a per-campaign name pool:
                # campaign_names = sc.get("agent_names") or AGENT_NAMES
                # agent_name = random.choice(campaign_names)
```

### Step 3 — Pass `campaign_id` from `_on_ringing` and `_on_new_call`

```python
# In _on_ringing(call_id):
campaign_id = _call_to_campaign_id.get(call_id)
config = await build_telephony_session_config(gateway_type="telephony", campaign_id=campaign_id)

# In _on_new_call(call_id) slow path:
campaign_id = _call_to_campaign_id.get(call_id)
config = await build_telephony_session_config(gateway_type=gateway_type, campaign_id=campaign_id)
```

Note: `_build_telephony_session_config` shim in `telephony_bridge.py` should also
be updated (or removed) once the async version is in place.

### Step 4 — Add campaign UI fields

These DB columns already exist — only the UI forms need to expose them:

| DB column | Campaign UI field | Used for |
|-----------|-------------------|----------|
| `campaigns.voice_id` | Voice selector (already in create/edit flow?) | TTS voice per campaign |
| `campaigns.system_prompt` | System prompt textarea | LLM behaviour |
| `campaigns.script_config.company_name` | Company name field | Greeting + prompt |
| `campaigns.script_config.agent_names` | Names (comma-separated or list) | Agent identity pool |
| `campaigns.prompt_config.greeting_override` | Greeting text | Custom opener |

### Step 5 — Remove hardcoded fallbacks

Once every campaign is guaranteed to have a company name and system prompt set via
the UI, remove `TELEPHONY_COMPANY_NAME` and `TELEPHONY_ESTIMATION_SYSTEM_PROMPT`
from `telephony_session_config.py` (or keep them as last-resort fallbacks with a
log warning so misconfigured campaigns are surfaced immediately).

---

## Recording

No changes needed.  `_save_call_recording()` in `telephony_bridge.py` runs
automatically on every call end — it mixes caller + agent audio into a stereo WAV
and persists it to storage + DB.  Estimation agent calls are recorded identically
to all other telephony calls.
```

- [ ] **Step 2: Commit**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add docs/future-changes/telephony-estimation-agent.md
git commit -m "docs: add future-changes guide for telephony estimation agent

Documents every hardcoded value (company name, agent names, voice, prompt,
greeting) with exact file locations and step-by-step production migration
instructions for when campaign UI wiring is complete."
```

---

## Task 4: Smoke test — verify the bridge still imports cleanly

- [ ] **Step 1: Check the full import chain**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -c "
from app.api.v1.endpoints.telephony_bridge import (
    _build_telephony_session_config,
    _build_outbound_greeting,
    _outbound_first_speaker,
)
from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
    build_telephony_greeting,
    TELEPHONY_COMPANY_NAME,
    AGENT_NAMES,
)
print('All imports OK')
print('Company:', TELEPHONY_COMPANY_NAME)
print('Names pool size:', len(AGENT_NAMES))
print('First speaker default:', _outbound_first_speaker())
"
```

Expected output:
```
All imports OK
Company: All States Estimation
Names pool size: 20
First speaker default: agent
```

- [ ] **Step 2: Run full unit test suite one final time**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: No new failures introduced by these changes.

- [ ] **Step 3: Final commit (if any last fixes were needed)**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git status
# If nothing changed: no commit needed
# If there were small fixes:
git add -p
git commit -m "fix: post-smoke-test corrections for estimation agent wiring"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Agent speaks first ✓ | Estimation greeting ✓ | Random name per call ✓ | Same name throughout ✓ | Never reveal AI ✓ | Post-interest flow (type → address → estimate → Gmail → close) ✓ | Redirect off-topic ✓ | Voice from global/campaign config ✓ | Ask AI untouched ✓ | Recording untouched ✓ | Future-changes doc ✓
- [x] **No placeholders:** All code blocks are complete and runnable
- [x] **Type consistency:** `build_telephony_session_config(gateway_type)` used consistently across all tasks; `build_telephony_greeting(agent_name, company_name)` signature matches usage in `_build_outbound_greeting`
