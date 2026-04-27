"""
app/domain/services/dental_workflow.py

Dental Receptionist AI Agent — Complete workflow definition.

This module defines:
  1. DentalAgentConfig  — AgentConfig for a dental practice
  2. DentalPromptManager — Jinja2 system prompts per conversation state
  3. DentalScenario      — Test scenario enum for end-to-end testing
  4. build_dental_campaign_payload() — ready-to-POST API payload

Conversation states covered:
  GREETING → CONFIRM_IDENTITY → APPOINTMENT_ACTION →
    ├── CONFIRM     → CLOSING
    ├── RESCHEDULE  → COLLECT_NEW_TIME → CLOSING
    └── CANCEL      → CONFIRM_CANCEL → CLOSING

Latency notes:
  - All prompts kept to ≤ 3 sentences max to stay within 60-token LLM budget
  - System prompt is deliberately terse — no examples, no explanations
  - Instructions optimised for llama-3.1-8b-instant at temperature 0.4

Usage:
    from app.domain.services.dental_workflow import (
        build_dental_agent_config,
        build_dental_campaign_payload,
        DentalScenario,
    )

    config = build_dental_agent_config(
        practice_name="Bright Smile Dental",
        agent_name="Sarah",
        patient_name="John Smith",
        appointment_date="Tuesday, April 8th",
        appointment_time="2:00 PM",
        doctor_name="Dr. Patel",
    )
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationRule, ConversationFlow


# ─────────────────────────────────────────────────────────────
# System prompt — terse, direct, optimised for 8b model
# ─────────────────────────────────────────────────────────────

DENTAL_SYSTEM_PROMPT = """You are {agent_name}, a receptionist at {practice_name}.
Call purpose: confirm, reschedule, or cancel {patient_name}'s appointment with {doctor_name} on {appointment_date} at {appointment_time}.

STRICT RULES:
- Max 1 sentence per reply (2 sentences only for confirmation recap)
- Never say "um", "uh", "sure!", "of course!", "great!", "absolutely!"
- Never offer medical advice
- If patient asks about anything not appointment-related: "I can only help with your appointment today."
- If patient sounds confused: repeat the appointment details once, then ask again
- Confirm before ending: always recap date, time, and doctor name

STATES and what to say:
GREETING: "Hi, may I speak with {patient_name}? This is {agent_name} from {practice_name}."
WRONG_PERSON: "Sorry to bother you. Thank you, have a good day."
CONFIRM_APPOINTMENT: "I'm calling to confirm your appointment with {doctor_name} on {appointment_date} at {appointment_time}. Are you still able to make it?"
CONFIRMED: "Perfect, we'll see you on {appointment_date} at {appointment_time} with {doctor_name}. Have a great day!"
RESCHEDULE_ASK: "What day and time works best for you?"
RESCHEDULE_CONFIRM: "I've noted your request to reschedule. Our team will call you back to confirm the new time within 24 hours."
CANCEL_CONFIRM: "I've cancelled your appointment with {doctor_name} on {appointment_date}. Is there anything else I can help with?"
VOICEMAIL: "Hi, this message is for {patient_name}. Please call {practice_name} at your earliest convenience to confirm your appointment with {doctor_name} on {appointment_date} at {appointment_time}. Thank you."
"""


# ─────────────────────────────────────────────────────────────
# Test scenarios
# ─────────────────────────────────────────────────────────────

class DentalScenario(str, Enum):
    """Test scenarios for the dental receptionist workflow."""
    CONFIRM_APPOINTMENT   = "confirm_appointment"    # Happy path: patient confirms
    RESCHEDULE_APPOINTMENT = "reschedule_appointment" # Patient wants different time
    CANCEL_APPOINTMENT    = "cancel_appointment"     # Patient wants to cancel
    WRONG_NUMBER          = "wrong_number"           # Someone else answers
    VOICEMAIL             = "voicemail"              # No answer → voicemail
    PATIENT_CONFUSED      = "patient_confused"       # Patient doesn't remember booking
    PATIENT_ASKS_QUESTIONS = "patient_asks_questions" # Patient asks about procedure
    LATENCY_BENCHMARK     = "latency_benchmark"      # Short yes/no for latency testing


# ─────────────────────────────────────────────────────────────
# Agent config builder
# ─────────────────────────────────────────────────────────────

def build_dental_agent_config(
    *,
    practice_name: str = "Bright Smile Dental",
    agent_name: str = "Sarah",
    patient_name: str = "the patient",
    appointment_date: str = "Tuesday, April 8th",
    appointment_time: str = "2:00 PM",
    doctor_name: str = "Dr. Patel",
) -> AgentConfig:
    """
    Build an AgentConfig for a dental appointment confirmation call.

    Returns an AgentConfig ready to pass to VoicePipelineService or
    to POST to the campaigns API.
    """
    system_prompt = DENTAL_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        practice_name=practice_name,
        patient_name=patient_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        doctor_name=doctor_name,
    )

    return AgentConfig(
        goal=AgentGoal.APPOINTMENT_CONFIRMATION,
        business_type="dental clinic",
        agent_name=agent_name,
        company_name=practice_name,
        tone="professional, warm, efficient",
        personality_traits=["concise", "helpful", "calm"],
        max_conversation_turns=8,
        response_max_sentences=2,
        rules=ConversationRule(
            forbidden_phrases=[
                "um", "uh", "well", "like", "actually", "basically",
                "sure!", "of course!", "great!", "absolutely!", "certainly!",
                "no problem", "my pleasure",
            ],
            do_not_say_rules=[
                "Do not give medical advice",
                "Do not discuss pricing or insurance",
                "Do not promise specific reschedule times",
                "Do not use filler words",
                "Keep every reply to 1 sentence maximum (2 for final confirmation)",
            ],
            max_follow_up_questions=1,
            require_confirmation=True,
        ),
        flow=ConversationFlow(
            on_yes="closing",
            on_no="reschedule_ask",
            on_uncertain="reschedule_ask",
            on_objection="closing",
            on_request_human="transfer",
            max_objection_attempts=1,
        ),
        context={
            "system_prompt": system_prompt,
            "patient_name": patient_name,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "doctor_name": doctor_name,
            "practice_name": practice_name,
            "agent_name": agent_name,
        },
    )


# ─────────────────────────────────────────────────────────────
# Campaign API payload builder
# ─────────────────────────────────────────────────────────────

def build_dental_campaign_payload(
    *,
    practice_name: str = "Bright Smile Dental",
    agent_name: str = "Sarah",
    doctor_name: str = "Dr. Patel",
    contacts: Optional[List[Dict]] = None,
) -> Dict:
    """
    Build a ready-to-POST campaign payload for the /api/v1/campaigns endpoint.

    Args:
        practice_name: Dental practice name
        agent_name:    Agent's name spoken on calls
        doctor_name:   Doctor's name for the appointments
        contacts:      List of contact dicts with patient_name, phone, appointment_date, appointment_time

    Returns:
        Dict payload to POST to /api/v1/campaigns/

    Example:
        import httpx
        payload = build_dental_campaign_payload(
            contacts=[
                {
                    "patient_name": "John Smith",
                    "phone": "+15551234567",
                    "appointment_date": "Tuesday, April 8th",
                    "appointment_time": "2:00 PM",
                }
            ]
        )
        resp = httpx.post("http://localhost:8000/api/v1/campaigns/", json=payload, headers={"Authorization": f"Bearer {token}"})
    """
    if contacts is None:
        contacts = [
            {
                "patient_name": "Test Patient",
                "phone": "+15550000001",
                "appointment_date": "Tuesday, April 8th",
                "appointment_time": "2:00 PM",
            }
        ]

    formatted_contacts = []
    for c in contacts:
        formatted_contacts.append({
            "name": c["patient_name"],
            "phone": c["phone"],
            "metadata": {
                "appointment_date": c.get("appointment_date", "Tuesday"),
                "appointment_time": c.get("appointment_time", "2:00 PM"),
                "doctor_name": doctor_name,
            },
        })

    # Build per-contact system prompts
    agent_config_template = build_dental_agent_config(
        practice_name=practice_name,
        agent_name=agent_name,
        doctor_name=doctor_name,
    )

    return {
        "name": f"{practice_name} — Appointment Confirmations",
        "description": f"Automated appointment confirmation calls for {practice_name}",
        "goal": AgentGoal.APPOINTMENT_CONFIRMATION,
        "agent_name": agent_name,
        "company_name": practice_name,
        "business_type": "dental clinic",
        "system_prompt": DENTAL_SYSTEM_PROMPT.format(
            agent_name=agent_name,
            practice_name=practice_name,
            patient_name="{patient_name}",
            appointment_date="{appointment_date}",
            appointment_time="{appointment_time}",
            doctor_name=doctor_name,
        ),
        "max_conversation_turns": 8,
        "response_max_sentences": 2,
        "tone": "professional, warm, efficient",
        "contacts": formatted_contacts,
        "calling_rules": {
            "time_window_start": "09:00",
            "time_window_end": "17:00",
            "timezone": "America/New_York",
            "allowed_days": [0, 1, 2, 3, 4],   # Mon–Fri
            "max_concurrent_calls": 5,
            "retry_delay_seconds": 3600,
            "max_retry_attempts": 2,
        },
        "ai_config": {
            "model": "llama-3.1-8b-instant",    # LATENCY FIX
            "temperature": 0.4,                  # LATENCY FIX
            "max_tokens": 60,                    # LATENCY FIX
            "tts_provider": "cartesia",          # LATENCY FIX
            "tts_voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
            "stt_eot_timeout_ms": 800,           # LATENCY FIX
            "stt_eot_threshold": 0.6,            # LATENCY FIX
            "stt_eager_eot_threshold": 0.45,     # LATENCY FIX
        },
    }


# ─────────────────────────────────────────────────────────────
# Expected conversation flows (for test assertions)
# ─────────────────────────────────────────────────────────────

DENTAL_TEST_DIALOGUES: Dict[DentalScenario, List[Dict]] = {

    DentalScenario.CONFIRM_APPOINTMENT: [
        {"turn": 1, "agent": "Hi, may I speak with John Smith? This is Sarah from Bright Smile Dental."},
        {"turn": 1, "patient": "Yes, this is John."},
        {"turn": 2, "agent": "I'm calling to confirm your appointment with Dr. Patel on Tuesday, April 8th at 2:00 PM. Are you still able to make it?"},
        {"turn": 2, "patient": "Yes, I'll be there."},
        {"turn": 3, "agent": "Perfect, we'll see you on Tuesday, April 8th at 2:00 PM with Dr. Patel. Have a great day!"},
        {"turn": 3, "patient": "Thanks, bye."},
    ],

    DentalScenario.RESCHEDULE_APPOINTMENT: [
        {"turn": 1, "agent": "Hi, may I speak with John Smith? This is Sarah from Bright Smile Dental."},
        {"turn": 1, "patient": "Yes, speaking."},
        {"turn": 2, "agent": "I'm calling to confirm your appointment with Dr. Patel on Tuesday, April 8th at 2:00 PM. Are you still able to make it?"},
        {"turn": 2, "patient": "Actually I need to reschedule, I have a conflict."},
        {"turn": 3, "agent": "What day and time works best for you?"},
        {"turn": 3, "patient": "Maybe Thursday afternoon?"},
        {"turn": 4, "agent": "I've noted your request to reschedule. Our team will call you back to confirm the new time within 24 hours."},
    ],

    DentalScenario.CANCEL_APPOINTMENT: [
        {"turn": 1, "agent": "Hi, may I speak with John Smith? This is Sarah from Bright Smile Dental."},
        {"turn": 1, "patient": "Yes."},
        {"turn": 2, "agent": "I'm calling to confirm your appointment with Dr. Patel on Tuesday, April 8th at 2:00 PM. Are you still able to make it?"},
        {"turn": 2, "patient": "I need to cancel it."},
        {"turn": 3, "agent": "I've cancelled your appointment with Dr. Patel on Tuesday, April 8th. Is there anything else I can help with?"},
        {"turn": 3, "patient": "No, that's all."},
        {"turn": 4, "agent": "Alright, thank you. Have a good day!"},
    ],

    DentalScenario.WRONG_NUMBER: [
        {"turn": 1, "agent": "Hi, may I speak with John Smith? This is Sarah from Bright Smile Dental."},
        {"turn": 1, "patient": "Sorry, there's no John Smith here."},
        {"turn": 2, "agent": "Sorry to bother you. Thank you, have a good day."},
    ],

    DentalScenario.PATIENT_ASKS_QUESTIONS: [
        {"turn": 1, "agent": "Hi, may I speak with John Smith? This is Sarah from Bright Smile Dental."},
        {"turn": 1, "patient": "Yes, hi."},
        {"turn": 2, "agent": "I'm calling to confirm your appointment with Dr. Patel on Tuesday, April 8th at 2:00 PM. Are you still able to make it?"},
        {"turn": 2, "patient": "What should I eat before the appointment?"},
        {"turn": 3, "agent": "I can only help with your appointment today — Dr. Patel's team can answer that when you arrive. Will Tuesday at 2 PM still work for you?"},
        {"turn": 3, "patient": "Yes, that's fine."},
        {"turn": 4, "agent": "Perfect, we'll see you on Tuesday, April 8th at 2:00 PM with Dr. Patel. Have a great day!"},
    ],

    DentalScenario.LATENCY_BENCHMARK: [
        # Ultra-short turns for latency measurement
        {"turn": 1, "agent": "Hi, this is Sarah from Bright Smile Dental. Can I speak with the patient?"},
        {"turn": 1, "patient": "Yes."},
        {"turn": 2, "agent": "Calling to confirm Tuesday at 2 PM with Dr. Patel. Still good?"},
        {"turn": 2, "patient": "Yes."},
        {"turn": 3, "agent": "Great, see you Tuesday. Bye!"},
    ],
}
