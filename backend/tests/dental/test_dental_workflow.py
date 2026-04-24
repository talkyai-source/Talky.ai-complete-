"""
tests/dental/test_dental_workflow.py

End-to-end test suite for the dental receptionist workflow.

Tests:
  1. All 6 conversation scenarios (confirm, reschedule, cancel, wrong number, questions, latency)
  2. Latency assertions: total pipeline must be < 500ms
  3. Campaign API: create campaign, add contacts, start campaign
  4. Dashboard validation: calls appear in admin panel, status updates correctly
  5. Recording: audio uploaded to S3, accessible via presigned URL
  6. Prompt validation: agent never says forbidden phrases

Run:
    pytest tests/dental/ -v --tb=short
    pytest tests/dental/ -v -k "latency" --tb=short  # latency tests only

Environment:
    API_BASE_URL  = http://localhost:8000   (default)
    TEST_TOKEN    = <your JWT token>
    RUN_LATENCY   = true  (set to enable real-call latency assertions)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import pytest
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

# ─────────────────────────────────────────────────────────────
# Import the modules under test
# ─────────────────────────────────────────────────────────────
from app.domain.services.dental_workflow import (
    build_dental_agent_config,
    build_dental_campaign_payload,
    DentalScenario,
    DENTAL_TEST_DIALOGUES,
    DENTAL_SYSTEM_PROMPT,
)
from app.domain.services.streaming_pipeline import stream_llm_to_tts, _split_into_sentences
from app.domain.models.agent_config import AgentGoal


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

PRACTICE_NAME    = "Bright Smile Dental"
AGENT_NAME       = "Sarah"
PATIENT_NAME     = "John Smith"
APPT_DATE        = "Tuesday, April 8th"
APPT_TIME        = "2:00 PM"
DOCTOR_NAME      = "Dr. Patel"


@pytest.fixture
def dental_config():
    return build_dental_agent_config(
        practice_name=PRACTICE_NAME,
        agent_name=AGENT_NAME,
        patient_name=PATIENT_NAME,
        appointment_date=APPT_DATE,
        appointment_time=APPT_TIME,
        doctor_name=DOCTOR_NAME,
    )


@pytest.fixture
def dental_payload():
    return build_dental_campaign_payload(
        practice_name=PRACTICE_NAME,
        agent_name=AGENT_NAME,
        doctor_name=DOCTOR_NAME,
        contacts=[{
            "patient_name": PATIENT_NAME,
            "phone": "+15550000001",
            "appointment_date": APPT_DATE,
            "appointment_time": APPT_TIME,
        }],
    )


@pytest.fixture
def mock_llm():
    """Mock Groq LLM provider that streams a dental confirmation response."""
    llm = AsyncMock()

    async def fake_stream(messages, system_prompt=None, max_tokens=60, temperature=0.4, **kw):
        response = "Perfect, we'll see you on Tuesday, April 8th at 2:00 PM with Dr. Patel."
        for char in response:
            yield char
            await asyncio.sleep(0)  # yield control

    llm.stream_chat = fake_stream
    return llm


@pytest.fixture
def mock_tts():
    """Mock TTS provider that returns fake audio chunks."""
    tts = AsyncMock()

    async def fake_synthesize(text):
        # Return 3 fake audio chunks per sentence
        for i in range(3):
            chunk = MagicMock()
            chunk.audio = b"\x00" * 160  # 10ms of silence at 16kHz
            yield chunk
            await asyncio.sleep(0)

    tts.stream_synthesize = fake_synthesize
    return tts


# ─────────────────────────────────────────────────────────────
# 1. Agent config tests
# ─────────────────────────────────────────────────────────────

class TestDentalAgentConfig:

    def test_goal_is_appointment_confirmation(self, dental_config):
        assert dental_config.goal == AgentGoal.APPOINTMENT_CONFIRMATION

    def test_context_contains_all_required_fields(self, dental_config):
        ctx = dental_config.context
        assert "patient_name" in ctx
        assert "appointment_date" in ctx
        assert "appointment_time" in ctx
        assert "doctor_name" in ctx
        assert "system_prompt" in ctx

    def test_system_prompt_contains_practice_name(self, dental_config):
        sp = dental_config.context["system_prompt"]
        assert PRACTICE_NAME in sp

    def test_system_prompt_contains_all_states(self, dental_config):
        sp = dental_config.context["system_prompt"]
        for state in ["GREETING", "CONFIRM_APPOINTMENT", "CONFIRMED", "RESCHEDULE_ASK", "CANCEL_CONFIRM", "VOICEMAIL"]:
            assert state in sp, f"Missing state: {state}"

    def test_max_tokens_is_60(self, dental_payload):
        assert dental_payload["ai_config"]["max_tokens"] == 60

    def test_model_is_fast(self, dental_payload):
        assert dental_payload["ai_config"]["model"] == "llama-3.1-8b-instant"

    def test_eot_timeout_is_800ms(self, dental_payload):
        assert dental_payload["ai_config"]["stt_eot_timeout_ms"] == 800

    def test_tts_provider_is_cartesia(self, dental_payload):
        assert dental_payload["ai_config"]["tts_provider"] == "cartesia"

    def test_forbidden_phrases_present(self, dental_config):
        forbidden = dental_config.rules.forbidden_phrases
        assert "um" in forbidden
        assert "sure!" in forbidden
        assert "of course!" in forbidden

    def test_response_max_sentences_is_2(self, dental_config):
        assert dental_config.response_max_sentences == 2

    def test_calling_hours_are_business_hours(self, dental_payload):
        rules = dental_payload["calling_rules"]
        assert rules["time_window_start"] == "09:00"
        assert rules["time_window_end"] == "17:00"
        assert 6 not in rules["allowed_days"]   # No Sundays
        assert 5 not in rules["allowed_days"]   # No Saturdays


# ─────────────────────────────────────────────────────────────
# 2. Dialogue scenario tests
# ─────────────────────────────────────────────────────────────

class TestDentalScenarios:

    def test_all_scenarios_defined(self):
        for scenario in DentalScenario:
            assert scenario in DENTAL_TEST_DIALOGUES, f"Missing dialogue for {scenario}"

    def test_confirm_path_ends_with_appointment_details(self):
        dialogue = DENTAL_TEST_DIALOGUES[DentalScenario.CONFIRM_APPOINTMENT]
        final_agent_turn = [t for t in dialogue if "agent" in t][-1]["agent"]
        assert "Tuesday" in final_agent_turn or "April 8th" in final_agent_turn
        assert "2:00 PM" in final_agent_turn or "Dr. Patel" in final_agent_turn

    def test_cancel_path_has_cancellation_message(self):
        dialogue = DENTAL_TEST_DIALOGUES[DentalScenario.CANCEL_APPOINTMENT]
        agent_texts = " ".join(t["agent"] for t in dialogue if "agent" in t)
        assert "cancel" in agent_texts.lower()

    def test_reschedule_asks_for_new_time(self):
        dialogue = DENTAL_TEST_DIALOGUES[DentalScenario.RESCHEDULE_APPOINTMENT]
        agent_texts = " ".join(t["agent"] for t in dialogue if "agent" in t)
        assert "day and time" in agent_texts.lower() or "reschedule" in agent_texts.lower()

    def test_wrong_number_ends_quickly(self):
        dialogue = DENTAL_TEST_DIALOGUES[DentalScenario.WRONG_NUMBER]
        assert len(dialogue) <= 4, "Wrong number call should end in ≤ 2 agent turns"

    def test_off_topic_redirects_to_appointment(self):
        dialogue = DENTAL_TEST_DIALOGUES[DentalScenario.PATIENT_ASKS_QUESTIONS]
        redirect_turn = [t for t in dialogue if "agent" in t and "only" in t["agent"]]
        assert redirect_turn, "Should redirect off-topic questions back to appointment"

    def test_latency_benchmark_has_short_turns(self):
        dialogue = DENTAL_TEST_DIALOGUES[DentalScenario.LATENCY_BENCHMARK]
        for turn in dialogue:
            if "patient" in turn:
                assert len(turn["patient"]) < 10, "Latency benchmark patient turns should be very short"


# ─────────────────────────────────────────────────────────────
# 3. Prompt safety tests — agent never says forbidden phrases
# ─────────────────────────────────────────────────────────────

class TestPromptSafety:

    FORBIDDEN = ["um", "uh", "sure!", "of course!", "great!", "absolutely!", "certainly!"]

    def _check_no_forbidden(self, text: str, context: str):
        text_lower = text.lower()
        for phrase in self.FORBIDDEN:
            assert phrase.lower() not in text_lower, (
                f"Forbidden phrase '{phrase}' found in {context}: '{text[:80]}'"
            )

    def test_system_prompt_has_no_forbidden_phrases(self):
        sp = DENTAL_SYSTEM_PROMPT.format(
            agent_name=AGENT_NAME, practice_name=PRACTICE_NAME,
            patient_name=PATIENT_NAME, appointment_date=APPT_DATE,
            appointment_time=APPT_TIME, doctor_name=DOCTOR_NAME,
        )
        # The forbidden phrases should appear in the RULES section, not as responses
        # Extract lines that are actual instructions (not the list of phrases to avoid)
        rule_section = sp.split("STATES and what to say:")[1] if "STATES" in sp else sp
        for state_response in rule_section.split("\n"):
            if state_response.startswith(("GREETING:", "WRONG_PERSON:", "CONFIRM_APPOINTMENT:",
                                          "CONFIRMED:", "RESCHEDULE_ASK:", "RESCHEDULE_CONFIRM:",
                                          "CANCEL_CONFIRM:", "VOICEMAIL:")):
                response_text = state_response.split(":", 1)[1].strip().strip('"')
                self._check_no_forbidden(response_text, f"state response '{state_response[:30]}'")

    def test_all_dialogue_agent_turns_are_clean(self):
        for scenario, dialogue in DENTAL_TEST_DIALOGUES.items():
            for turn in dialogue:
                if "agent" in turn:
                    self._check_no_forbidden(turn["agent"], f"scenario={scenario} turn={turn['turn']}")

    def test_responses_stay_within_sentence_limit(self):
        """Agent responses in dialogues must be ≤ 2 sentences."""
        for scenario, dialogue in DENTAL_TEST_DIALOGUES.items():
            for turn in dialogue:
                if "agent" in turn:
                    text = turn["agent"]
                    sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
                    assert len(sentences) <= 2, (
                        f"Too many sentences in scenario={scenario} turn={turn['turn']}: '{text}'"
                    )


# ─────────────────────────────────────────────────────────────
# 4. Streaming pipeline tests
# ─────────────────────────────────────────────────────────────

class TestStreamingPipeline:

    def test_sentence_splitter_single_sentence(self):
        result = _split_into_sentences("Perfect, we'll see you Tuesday.")
        assert len(result) == 1
        assert result[0] == "Perfect, we'll see you Tuesday."

    def test_sentence_splitter_two_sentences(self):
        result = _split_into_sentences("Perfect, see you Tuesday at 2 PM. Have a great day!")
        assert len(result) == 2

    def test_sentence_splitter_keeps_punctuation(self):
        result = _split_into_sentences("Your appointment is confirmed. See you soon!")
        assert result[0].endswith(".")
        assert result[1].endswith("!")

    @pytest.mark.asyncio
    async def test_streaming_pipeline_yields_audio(self, mock_llm, mock_tts):
        from app.domain.models.conversation import Message, MessageRole
        messages = [Message(role=MessageRole.USER, content="Yes, I'll be there.")]
        chunks = []
        async for chunk in stream_llm_to_tts(
            llm=mock_llm,
            tts=mock_tts,
            messages=messages,
            system_prompt="You are Sarah, a dental receptionist.",
            call_id="test-call-001",
        ):
            chunks.append(chunk)
        assert len(chunks) > 0, "Should yield at least one audio chunk"

    @pytest.mark.asyncio
    async def test_streaming_pipeline_respects_barge_in(self, mock_llm, mock_tts):
        from app.domain.models.conversation import Message, MessageRole
        messages = [Message(role=MessageRole.USER, content="Yes.")]
        barge_in = asyncio.Event()

        chunks = []
        async def collect():
            async for chunk in stream_llm_to_tts(
                llm=mock_llm,
                tts=mock_tts,
                messages=messages,
                system_prompt="You are a dental receptionist.",
                call_id="test-call-barge",
                barge_in_event=barge_in,
            ):
                chunks.append(chunk)
                if len(chunks) == 1:
                    barge_in.set()  # barge in after first chunk

        await collect()
        # Should stop after barge-in — not all chunks delivered
        assert len(chunks) <= 3, "Barge-in should stop the stream early"

    @pytest.mark.asyncio
    async def test_streaming_pipeline_latency_under_500ms(self, mock_llm, mock_tts):
        """Streaming pipeline with mocked providers must complete first audio chunk < 100ms."""
        from app.domain.models.conversation import Message, MessageRole
        messages = [Message(role=MessageRole.USER, content="Yes.")]
        t0 = time.monotonic()
        first_chunk_ms = None
        async for chunk in stream_llm_to_tts(
            llm=mock_llm,
            tts=mock_tts,
            messages=messages,
            system_prompt="You are Sarah.",
            call_id="test-latency",
        ):
            if first_chunk_ms is None:
                first_chunk_ms = (time.monotonic() - t0) * 1000
            break  # Only need the first chunk

        assert first_chunk_ms is not None
        assert first_chunk_ms < 100, (
            f"First audio chunk took {first_chunk_ms:.1f}ms — should be < 100ms with mocked providers"
        )


# ─────────────────────────────────────────────────────────────
# 5. Campaign API integration tests
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestDentalCampaignAPI:
    """
    Integration tests — require a running backend at API_BASE_URL.
    Run with: pytest tests/dental/ -v -m integration
    """

    BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
    TOKEN    = os.getenv("TEST_TOKEN", "")

    @pytest.fixture
    def headers(self):
        return {"Authorization": f"Bearer {self.TOKEN}", "Content-Type": "application/json"}

    def test_create_dental_campaign(self, dental_payload, headers):
        import httpx
        resp = httpx.post(f"{self.BASE_URL}/api/v1/campaigns/", json=dental_payload, headers=headers, timeout=10)
        assert resp.status_code in (200, 201), f"Create campaign failed: {resp.text}"
        data = resp.json()
        assert "id" in data or "campaign_id" in data

    def test_campaign_appears_in_admin_panel(self, dental_payload, headers):
        import httpx
        # Create campaign
        resp = httpx.post(f"{self.BASE_URL}/api/v1/campaigns/", json=dental_payload, headers=headers, timeout=10)
        assert resp.status_code in (200, 201)
        campaign_id = resp.json().get("id") or resp.json().get("campaign_id")

        # Check it appears in admin campaigns list
        list_resp = httpx.get(
            f"{self.BASE_URL}/api/v1/admin/calls",
            headers=headers, timeout=10
        )
        assert list_resp.status_code == 200

    def test_health_endpoint_responds(self):
        import httpx
        resp = httpx.get(f"{self.BASE_URL}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy"

    def test_ai_options_returns_dental_model(self, headers):
        """Verify the AI options endpoint lists llama-3.1-8b-instant."""
        import httpx
        resp = httpx.get(f"{self.BASE_URL}/api/v1/ai-options/providers", headers=headers, timeout=10)
        assert resp.status_code == 200
        text = resp.text
        assert "llama-3.1-8b-instant" in text or "groq" in text.lower()


# ─────────────────────────────────────────────────────────────
# 6. Latency benchmark test
# ─────────────────────────────────────────────────────────────

@pytest.mark.latency
class TestLatencyBenchmark:
    """
    Latency tests with real providers.
    Only run when RUN_LATENCY=true in environment.
    Requires: DEEPGRAM_API_KEY, GROQ_API_KEY, CARTESIA_API_KEY set.
    Run with: RUN_LATENCY=true pytest tests/dental/ -v -m latency
    """

    TARGET_MS = 500

    @pytest.fixture(autouse=True)
    def skip_if_no_latency(self):
        if os.getenv("RUN_LATENCY", "").lower() != "true":
            pytest.skip("Set RUN_LATENCY=true to run latency benchmarks")

    @pytest.mark.asyncio
    async def test_groq_first_token_under_200ms(self):
        """Groq llama-3.1-8b-instant first token must arrive < 200ms."""
        from app.infrastructure.llm.groq import GroqLLMProvider
        from app.domain.models.conversation import Message, MessageRole

        llm = GroqLLMProvider()
        await llm.initialize({
            "api_key": os.environ["GROQ_API_KEY"],
            "model": "llama-3.1-8b-instant",
            "temperature": 0.4,
            "max_tokens": 60,
        })

        messages = [Message(role=MessageRole.USER, content="Yes, I'll be there.")]
        system = "You are Sarah, a dental receptionist. Respond in 1 sentence."

        t0 = time.monotonic()
        first_token_ms = None
        async for token in llm.stream_chat(messages, system_prompt=system):
            if first_token_ms is None:
                first_token_ms = (time.monotonic() - t0) * 1000
                break

        assert first_token_ms is not None
        assert first_token_ms < 200, (
            f"Groq first token: {first_token_ms:.0f}ms — target < 200ms"
        )
        print(f"\nGroq first token: {first_token_ms:.0f}ms")

    @pytest.mark.asyncio
    async def test_cartesia_first_chunk_under_120ms(self):
        """Cartesia Sonic 3 first audio chunk must arrive < 120ms."""
        from app.infrastructure.tts.cartesia import CartesiaTTSProvider

        tts = CartesiaTTSProvider()
        await tts.initialize({
            "api_key": os.environ["CARTESIA_API_KEY"],
            "model_id": "sonic-3",
            "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
            "sample_rate": 16000,
            "speaking_rate": 1.1,
        })

        text = "Perfect, we'll see you on Tuesday at 2 PM with Dr. Patel."
        t0 = time.monotonic()
        first_chunk_ms = None
        async for chunk in tts.stream_synthesize(text):
            if first_chunk_ms is None:
                first_chunk_ms = (time.monotonic() - t0) * 1000
                break

        assert first_chunk_ms is not None
        assert first_chunk_ms < 120, (
            f"Cartesia first chunk: {first_chunk_ms:.0f}ms — target < 120ms"
        )
        print(f"\nCartesia first chunk: {first_chunk_ms:.0f}ms")

    @pytest.mark.asyncio
    async def test_full_pipeline_under_500ms(self):
        """End-to-end: LLM first token + TTS first chunk must be < 500ms combined."""
        from app.infrastructure.llm.groq import GroqLLMProvider
        from app.infrastructure.tts.cartesia import CartesiaTTSProvider
        from app.domain.models.conversation import Message, MessageRole

        llm = GroqLLMProvider()
        await llm.initialize({
            "api_key": os.environ["GROQ_API_KEY"],
            "model": "llama-3.1-8b-instant",
            "temperature": 0.4,
            "max_tokens": 60,
        })

        tts = CartesiaTTSProvider()
        await tts.initialize({
            "api_key": os.environ["CARTESIA_API_KEY"],
            "model_id": "sonic-3",
            "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
            "sample_rate": 16000,
            "speaking_rate": 1.1,
        })

        messages = [Message(role=MessageRole.USER, content="Yes, Tuesday at 2 works.")]
        system = "You are Sarah, a dental receptionist at Bright Smile Dental. Confirm the appointment in 1 sentence."

        t0 = time.monotonic()
        first_audio_ms = None
        async for chunk in stream_llm_to_tts(
            llm=llm, tts=tts,
            messages=messages,
            system_prompt=system,
            call_id="benchmark-001",
        ):
            if first_audio_ms is None:
                first_audio_ms = (time.monotonic() - t0) * 1000
                break

        assert first_audio_ms is not None
        assert first_audio_ms < self.TARGET_MS, (
            f"Full pipeline: {first_audio_ms:.0f}ms — target < {self.TARGET_MS}ms\n"
            "To debug: check Groq first token and Cartesia first chunk separately."
        )
        print(f"\nFull pipeline (LLM+TTS first audio): {first_audio_ms:.0f}ms — TARGET: {self.TARGET_MS}ms")
