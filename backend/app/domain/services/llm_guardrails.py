"""
LLM Guardrails Service
Provides timeout handling, fallback responses, and response validation.

Day 17: Ensures graceful degradation when LLM fails while maintaining
human-like conversation flow (no hints that it's an AI).
"""
import re
import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Tuple, Optional, List, Union
from pydantic import BaseModel, Field

from app.domain.models.conversation_state import ConversationState, CallOutcomeType
from app.domain.models.agent_config import ConversationRule

logger = logging.getLogger(__name__)


# HARD RULE 10's executable half.  Prompt text is advisory; these patterns are
# the output gate that stops an unsupported action-completion claim before TTS.
# They intentionally match completion language, not a request or an honest
# limitation ("I can't send that").
_ACTION_COMPLETION_PATTERNS = {
    "schedule_callback": re.compile(
        r"(?:\b(?:scheduled|booked|arranged|confirmed|set up)\b.{0,35}"
        r"\b(?:callback|call\s*back|follow[- ]?up call)\b|"
        r"\b(?:callback|call\s*back|follow[- ]?up call)\b.{0,35}"
        r"\b(?:scheduled|booked|arranged|confirmed|set up)\b|"
        r"\b(?:i|we)(?:'ve| have)\s+(?:(?:just|now)\s+)?"
        r"(?:scheduled|booked|arranged|set up)\s+(?:it|that)\b)",
        re.IGNORECASE,
    ),
    "send_email": re.compile(
        r"(?:\b(?:sent|emailed|delivered)\b.{0,35}"
        r"\b(?:e-?mail|information|details|quote|estimate)\b|"
        r"\b(?:e-?mail|information|details|quote|estimate)\b.{0,35}"
        r"\b(?:sent|emailed|delivered|on (?:its|the) way)\b|"
        r"\b(?:check|in) your inbox\b|"
        r"\b(?:i|we)(?:'ve| have)\s+(?:(?:just|now)\s+)?"
        r"(?:sent|emailed|delivered)\s+(?:it|that|this)\b)",
        re.IGNORECASE,
    ),
    "submit_form": re.compile(
        r"(?:\b(?:submitted|filed|completed)\b.{0,35}"
        r"\b(?:form|application|request)\b|"
        r"\b(?:form|application|request)\b.{0,35}"
        r"\b(?:submitted|filed|completed)\b|"
        r"\b(?:i|we)(?:'ve| have)\s+(?:(?:just|now)\s+)?"
        r"(?:submitted|filed|completed)\s+(?:it|that|this)\b)",
        re.IGNORECASE,
    ),
    "transfer_call": re.compile(
        r"\b(?:transferring you now|connecting you now|putting you through|"
        r"transfer (?:has )?(?:started|completed)|transfer is complete|"
        r"(?:i|we)(?:'ve| have)\s+(?:(?:just|now)\s+)?"
        r"(?:transferred|connected)\s+(?:you|the call))\b",
        re.IGNORECASE,
    ),
    "end_call": re.compile(
        r"\b(?:i(?:'ve| have) ended the call|the call (?:has )?ended|"
        r"hangup (?:has )?(?:started|completed)|hangup is complete|"
        r"i(?:'m| am)\s+(?:ending the call|hanging up))\b",
        re.IGNORECASE,
    ),
}

_ACTION_NEGATED_COMPLETION_PATTERNS = {
    "schedule_callback": re.compile(
        r"(?:\b(?:callback|call\s*back|follow[- ]?up call)\b.{0,24}"
        r"\b(?:wasn't|isn't|hasn't been|was not|is not|has not been)\s+"
        r"(?:scheduled|booked|arranged|confirmed|set up)\b|"
        r"\b(?:i|we)\s+(?:haven't|have not|didn't|did not)\s+"
        r"(?:schedule|scheduled|book|booked|arrange|arranged|set up)\b"
        r".{0,24}\b(?:callback|call\s*back|follow[- ]?up call|it|that)\b)",
        re.IGNORECASE,
    ),
    "send_email": re.compile(
        r"(?:\b(?:e-?mail|information|details|quote|estimate)\b.{0,24}"
        r"\b(?:wasn't|isn't|hasn't been|was not|is not|has not been)\s+"
        r"(?:sent|emailed|delivered)\b|"
        r"\b(?:i|we)\s+(?:haven't|have not|didn't|did not)\s+"
        r"(?:send|sent|e-?mail|emailed|deliver|delivered)\b.{0,24}"
        r"\b(?:e-?mail|information|details|quote|estimate|it|that|this)\b)",
        re.IGNORECASE,
    ),
    "submit_form": re.compile(
        r"(?:\b(?:form|application|request)\b.{0,24}"
        r"\b(?:wasn't|isn't|hasn't been|was not|is not|has not been)\s+"
        r"(?:submitted|filed|completed)\b|"
        r"\b(?:i|we)\s+(?:haven't|have not|didn't|did not)\s+"
        r"(?:submit|submitted|file|filed|complete|completed)\b.{0,24}"
        r"\b(?:form|application|request|it|that|this)\b)",
        re.IGNORECASE,
    ),
    "transfer_call": re.compile(
        r"(?:\btransfer\b.{0,24}\b(?:wasn't|isn't|hasn't been|was not|is not|"
        r"has not been)\s+(?:started|completed)\b|"
        r"\b(?:i|we)\s+(?:haven't|have not|didn't|did not)\s+"
        r"(?:transfer|transferred|connect|connected)\s+(?:you|the call)\b)",
        re.IGNORECASE,
    ),
    "end_call": re.compile(
        r"(?:\b(?:call|hangup)\b.{0,24}\b(?:wasn't|isn't|hasn't been|was not|"
        r"is not|has not been)\s+(?:ended|started|completed)\b|"
        r"\b(?:i|we)\s+(?:haven't|have not|didn't|did not)\s+"
        r"(?:end|ended)\s+(?:this |the )?call\b)",
        re.IGNORECASE,
    ),
}


def _completed_action_claims(response: str) -> list[str]:
    """Return action names claimed as completed, excluding explicit failures."""
    claims: list[str] = []
    for action, pattern in _ACTION_COMPLETION_PATTERNS.items():
        negated_spans = [
            negated.span()
            for negated in _ACTION_NEGATED_COMPLETION_PATTERNS[action].finditer(response)
        ]
        for match in pattern.finditer(response):
            # Exempt only an explicit negation whose span overlaps THIS exact
            # completion predicate. An unrelated "didn't" (or an earlier
            # failed attempt followed by a later success claim) is not a
            # blanket bypass for the action.
            if any(
                match.start() < negated_end and negated_start < match.end()
                for negated_start, negated_end in negated_spans
            ):
                continue
            claims.append(action)
            break
    return claims


class LLMTimeoutError(Exception):
    """Raised when LLM response times out"""
    pass


class LLMGuardrailsConfig(BaseModel):
    """Configuration for LLM guardrails"""
    max_response_tokens: int = Field(default=150, ge=50, le=500, description="Max tokens per response")
    max_response_time_seconds: float = Field(default=10.0, ge=1.0, le=30.0, description="Max LLM response time")
    max_llm_errors_before_goodbye: int = Field(default=3, ge=1, le=5, description="Max LLM errors before ending call")
    max_sentences: int = Field(default=3, ge=1, le=5, description="Max sentences per response")


class LLMGuardrails:
    """
    LLM response guardrails with human-like fallback handling.
    
    Key principles:
    - Fallback responses sound 100% human (no "I'm having trouble" or "system error")
    - State-appropriate responses that continue the conversation naturally
    - Retry mechanism before graceful goodbye
    """
    
    # Human-like fallback responses per state
    # These are designed to sound like natural pauses in conversation
    # NO hints about AI, system errors, or technical issues
    FALLBACK_RESPONSES = {
        ConversationState.GREETING: [
            "Oh sorry, could you say that again? I missed that.",
            "Apologies, go ahead, I'm listening.",
            "Sorry about that, please continue."
        ],
        ConversationState.QUALIFICATION: [
            "Right, let me just note that down. So what works best for you?",
            "Got it. And what time would be ideal?",
            "I see. Could you tell me a bit more about that?"
        ],
        ConversationState.OBJECTION_HANDLING: [
            "I completely understand. What would work better for you?",
            "That makes sense. Is there anything else on your mind?",
            "I hear you. Let me see what options we have."
        ],
        ConversationState.CLOSING: [
            "Perfect. Just to confirm everything's set, is there anything else?",
            "Great. You're all set then. Any final questions?",
            "Wonderful. We'll see you then!"
        ],
        ConversationState.TRANSFER: [
            "Absolutely, let me get someone for you right now.",
            "Of course, I'll transfer you immediately.",
            "No problem at all, connecting you now."
        ],
        ConversationState.GOODBYE: [
            "Thank you so much. Have a great day!",
            "Thanks for your time. Take care!",
            "Appreciate it. Goodbye!"
        ]
    }
    
    # Graceful goodbye when max errors reached (still human-like)
    GRACEFUL_GOODBYE_RESPONSES = [
        "I apologize, but I need to step away for a moment. Someone will call you back shortly. Thank you!",
        "I have to take another call, but we'll reach back out to you soon. Thanks so much!",
        "Let me have a colleague follow up with you directly. Thank you for your time!"
    ]
    
    def __init__(self, config: LLMGuardrailsConfig = None):
        self.config = config or LLMGuardrailsConfig()
        self._fallback_index = {}  # Tracks which fallback to use per call

    @staticmethod
    def _normalize_state(state: Union[ConversationState, str, None]) -> ConversationState:
        """
        Normalize state input to ConversationState.

        Some runtime session models serialize enums as raw strings; this guardrail
        accepts both forms to prevent fallback-path crashes.
        """
        if isinstance(state, ConversationState):
            return state
        if isinstance(state, str):
            try:
                return ConversationState(state)
            except ValueError:
                logger.warning("Unknown conversation state '%s', defaulting to greeting", state)
        return ConversationState.GREETING
    
    def get_fallback_response(
        self,
        state: Union[ConversationState, str, None],
        call_id: str = None,
        error_count: int = 0
    ) -> Tuple[str, bool]:
        """
        Get appropriate human-like fallback response for current state.
        
        Args:
            state: Current conversation state
            call_id: Call identifier for cycling through fallbacks
            error_count: Number of LLM errors so far
            
        Returns:
            Tuple of (response_text, should_end_call)
        """
        # Check if we should end the call due to too many errors
        if error_count >= self.config.max_llm_errors_before_goodbye:
            import random
            response = random.choice(self.GRACEFUL_GOODBYE_RESPONSES)
            logger.warning(f"Max LLM errors reached ({error_count}), using graceful goodbye")
            return response, True
        
        normalized_state = self._normalize_state(state)
        state_key = normalized_state.value

        # Get state-specific fallbacks
        fallbacks = self.FALLBACK_RESPONSES.get(
            normalized_state,
            self.FALLBACK_RESPONSES[ConversationState.GREETING]
        )
        
        # Cycle through fallbacks for variety
        key = f"{call_id}_{state_key}" if call_id else state_key
        idx = self._fallback_index.get(key, 0)
        response = fallbacks[idx % len(fallbacks)]
        self._fallback_index[key] = idx + 1
        
        logger.info(f"Using fallback response for state={state_key}: '{response[:50]}...'")
        return response, False
    
    def truncate_response(self, response: str, max_sentences: int = None) -> str:
        """
        Truncate response to max sentences for voice brevity.
        
        Args:
            response: Full LLM response
            max_sentences: Override max sentences (uses config default if None)
            
        Returns:
            Truncated response
        """
        if not response:
            return response
            
        max_sentences = max_sentences or self.config.max_sentences

        # Split by sentence-ending punctuation
        sentences = re.split(r'(?<=[.!?])\s+', response.strip())

        if len(sentences) > max_sentences:
            truncated = ' '.join(sentences[:max_sentences])
            if truncated and truncated[-1] not in '.!?':
                truncated += '.'
            logger.debug(f"Truncated response from {len(sentences)} to {max_sentences} sentences")
            response = truncated

        # Secondary cap: enforce max characters per sentence to catch run-on sentences
        # that the punctuation splitter won't break (e.g. one long comma-joined sentence).
        # ~80 chars per sentence is generous but still limits the worst case.
        max_chars = max_sentences * 80
        if len(response) > max_chars:
            truncated = response[:max_chars]
            # Prefer ending at the last sentence-ending punctuation within the limit
            for punct in ('.', '!', '?'):
                last = truncated.rfind(punct)
                if last > max_chars // 2:  # only use if not too early in the string
                    truncated = response[:last + 1]
                    break
            else:
                # No sentence punctuation — cut at last comma
                last_comma = truncated.rfind(',')
                if last_comma > max_chars // 2:
                    truncated = response[:last_comma] + '.'
                else:
                    truncated = truncated.rstrip() + '.'
            logger.debug(f"Char-capped response from {len(response)} to {max_chars} chars")
            response = truncated

        return response
    
    def validate_response(
        self,
        response: str,
        rules: ConversationRule = None,
        *,
        action_results: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate response doesn't contain forbidden phrases.
        
        Args:
            response: LLM response to validate
            rules: Conversation rules with forbidden_phrases
            action_results: Latest deterministic result for each connected
                voice action.  A completion claim is valid only when the
                matching result explicitly permits confirmation.
            
        Returns:
            Tuple of (is_valid, reason_if_invalid)
        """
        if not response:
            return False, "empty_response"

        # Enforce HARD RULE 10 even when a campaign has no custom ConversationRule.
        # This runs before the historical ``if not rules`` fast-path because an
        # absent tenant rule must never mean "imaginary side effects are allowed".
        results = action_results or {}
        for action in _completed_action_claims(response):
            result = results.get(action)
            if not isinstance(result, Mapping):
                logger.warning("Blocked unconfirmed voice action claim: %s", action)
                return False, f"unconfirmed_action:{action}"
            if not (
                result.get("success") is True
                and result.get("confirmation_allowed") is True
            ):
                status = str(result.get("status") or "unconfirmed")
                logger.warning(
                    "Blocked failed/unconfirmed voice action claim: %s status=%s",
                    action,
                    status,
                )
                return False, f"action_failed:{action}:{status}"
        
        if not rules:
            return True, None
        
        response_lower = response.lower()
        
        # Check forbidden phrases
        for phrase in rules.forbidden_phrases:
            if phrase.lower() in response_lower:
                logger.warning(f"Response contains forbidden phrase: '{phrase}'")
                return False, f"contains_forbidden_phrase:{phrase}"
        
        # Check do_not_say rules (more flexible matching)
        for rule in rules.do_not_say_rules:
            rule_lower = rule.lower()

            # Special handling for scheduling restrictions:
            # mention of a feature is allowed; explicit scheduling intent is not.
            if "book appointments" in rule_lower or "schedule calls" in rule_lower:
                if self._looks_like_scheduling_attempt(response_lower):
                    logger.warning(f"Response may violate rule: '{rule}'")
                    return False, f"may_violate_rule:{rule}"
                continue

            # Extract key terms from rule
            terms = [t.strip().lower() for t in rule.split() if len(t) > 3]
            matches = sum(1 for t in terms if t in response_lower)
            if matches >= 2:  # If 2+ key terms match, likely violation
                logger.warning(f"Response may violate rule: '{rule}'")
                return False, f"may_violate_rule:{rule}"
        
        return True, None
    
    def clean_response(self, response: str, *, preserve_audio_tags: bool = False, tts_model_id=None, protected_values=None) -> str:
        """
        Clean LLM response by removing common artifacts.

        Removes:
        - Thinking patterns ("Well, ", "So, ", "Actually, ")
        - Hidden reasoning blocks / stray tags
        - Markdown formatting markers
        - Excessive whitespace
        - Incomplete sentences at the end

        ``preserve_audio_tags``: when False (default), inline bracket audio tags
        like [laughs]/[sighs]/[pause] are STRIPPED — most TTS engines can't
        perform them and would read them aloud. Pass True ONLY when the live
        voice supports them (ElevenLabs eleven_v3), so they reach the engine
        intact. Plain-word fillers ("um", "hmm") are never affected either way.
        """
        if not response:
            return response

        cleaned = response.strip()

        # Remove hidden reasoning or XML-like wrappers before anything else.
        cleaned = re.sub(r'<think\b[^>]*>[\s\S]*?</think>', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'<reasoning\b[^>]*>[\s\S]*?</reasoning>', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'<analysis\b[^>]*>[\s\S]*?</analysis>', ' ', cleaned, flags=re.IGNORECASE)

        # Collapse markdown links into plain text — MUST run before audio-tag
        # stripping so "[text](url)" becomes "text" and isn't mistaken for a tag.
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)

        # Hard gate for audio tags: physically remove [laughs]/[sighs]/etc unless
        # the voice can perform them. This is the production safety net — the
        # prompt also gates them, but a disobedient LLM must never leak a tag as
        # spoken text on a non-supporting engine (Cartesia/Google/Deepgram/flash).
        from app.domain.services.voice_pipeline.expressive_caps import (
            strip_audio_tags, strip_stage_directions, strip_unsupported_audio_tags,
        )
        # Always remove *asterisk*/(paren)-wrapped stage directions ("*laughs*",
        # "(sighs)") — wrong format on every engine, and the markdown pass below
        # would otherwise leave the bare word "laughs" to be read aloud.
        cleaned = strip_stage_directions(cleaned)
        # Also drop parenthetical ASIDES the model narrates about itself — e.g.
        # "(waiting for the number to be provided)" leaked to TTS in a real call.
        # Require a 3+ letter word inside, so numeric groups like a phone area
        # code "(077)" survive; word-containing parens are never meant to be
        # spoken on a voice call.
        cleaned = re.sub(r'\([^)]*[A-Za-z]{3,}[^)]*\)', ' ', cleaned)
        # Bracket audio tags ([laughs]) — keep only the ones the LIVE engine
        # performs (per-provider, default-deny). tts_model_id is the precise path;
        # preserve_audio_tags is the legacy binary fallback for callers without it.
        if tts_model_id is not None:
            cleaned = strip_unsupported_audio_tags(cleaned, tts_model_id)
        elif not preserve_audio_tags:
            cleaned = strip_audio_tags(cleaned)
        cleaned = re.sub(r'```[\s\S]*?```', ' ', cleaned)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
        cleaned = re.sub(r'^\s*#{1,6}\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*>\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*(?:[-*+•]|\d+[.)])\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\*\*\*?|\*\*?|__?|~~', '', cleaned)
        cleaned = re.sub(r'<[^>]+>', ' ', cleaned)

        # Remove common filler starts.
        # Strip ONLY the corporate / assistant-y canned openers that make an
        # agent sound like a bot. We deliberately DO NOT strip natural human
        # discourse markers ("Well,", "So,", "Okay,", "Alright,", "Actually,")
        # anymore — those are exactly the conversational openers the persona now
        # asks for. Removing them was deleting the very naturalness we want, so
        # "So, I'm calling because..." was reaching TTS as "I'm calling
        # because..." (the filler vanished). Keep the canned-politeness strips.
        # Multi-word phrases must come before single-word so the longer match wins.
        # 2026-08-13: every pattern here requires a SEPARATOR after the filler
        # (`\s+`, or the sentence ending). "Sure thing" alone used `\s*`, which
        # matches the empty string — so "Sure thing." stripped to a bare "."
        # and "Sure thing. The weather today is actually quite nice." stripped
        # to ". The weather today is actually quite nice.". Both were spoken on
        # production calls that day.
        #
        # The bare "." was the worse of the two: turn_streamer drops any
        # sentence with no speakable content, so the agent said NOTHING that
        # turn. From the caller's side that is dead air in the middle of a
        # conversation — the model had answered, and the answer was deleted
        # between the LLM and the wire by a stray quantifier.
        # Each filler must be followed by a SEPARATOR or the end of the text.
        # `(?:\s+|$)` rather than `\s+` matters for the longest-match ordering
        # below: with `\s+`, "Sure thing." failed the two-word pattern (no
        # trailing space) and fell through to the one-word `^Sure` pattern,
        # which ate "Sure " and left "thing." — the bug simply moved.
        # Anchoring on `$` lets the longer phrase win even when it IS the whole
        # message, and the "filler was everything" guard below then restores it.
        filler_starts = [
            r'^Sure thing(?:\s*[!,.])?(?:\s+|$)',
            r'^No problem(?:\s*[!,.])?(?:\s+|$)',
            r'^Happy to help(?:\s*[!,.])?(?:\s+|$)',
            r'^Sure(?:\s*[!,.])?(?:\s+|$)',
            r'^Of course(?:\s*[!,.])?(?:\s+|$)',
            r'^Absolutely(?:\s*[!,.])?(?:\s+|$)',
            r'^Certainly(?:\s*[!,.])?(?:\s+|$)',
            r'^Definitely(?:\s*[!,.])?(?:\s+|$)',
            r'^(Great[!,]\s+)',         # "Great!" or "Great," as opener only
        ]

        before_fillers = cleaned
        for pattern in filler_starts:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

        if cleaned != before_fillers:
            # Strip punctuation orphaned by the filler removal. The em-dash case
            # below was the known one ("Sure thing! —I'm offering..."); the same
            # thing happens with ordinary sentence punctuation and went unhandled
            # until it reached production as a spoken ". The weather today is
            # actually quite nice."
            #
            # Generalised deliberately rather than adding "." to the dash class:
            # the defect is not which character was left behind, it is that
            # removing a leading phrase can leave ANY leading punctuation
            # dangling. Gated on the text having actually changed, so a reply
            # that legitimately opens with punctuation is untouched.
            cleaned = re.sub(r'^[\s—–\-.,;:!?]+', '', cleaned)

            # If the filler WAS the whole message, keep the original. Speaking
            # "Sure thing." is right; speaking "." is not, and — because a
            # sentence with no speakable content is dropped downstream — saying
            # nothing at all is worse than either.
            if not re.search(r'[A-Za-z0-9]', cleaned):
                cleaned = before_fillers
            else:
                # Restore sentence case. The filler carried the capital, so
                # "Sure, take your time." became "take your time." — which is
                # what the persisted transcript and every QA review then shows.
                # Only touches a lowercase ASCII letter, so "iPhone" or a
                # capitalised name is never rewritten.
                if cleaned[:1].islower():
                    cleaned = cleaned[0].upper() + cleaned[1:]
        else:
            # Unchanged text: keep the original narrow dash strip so behaviour
            # is identical for every reply that had no filler to remove.
            cleaned = re.sub(r'^[—–\-]+\s*', '', cleaned)

        # Clean up whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Numbered markdown lists can collapse onto one line during streaming,
        # which makes "1." / "2." look like sentence endings and truncates
        # package answers incorrectly. Strip those inline list markers here.
        cleaned = re.sub(r'(?:(?<=\s)|^)\d+[.)]\s+(?=[A-Za-z])', '', cleaned)

        # Output-side safety net (OWASP LLM02 — treat model output as untrusted):
        # a disobedient or jailbroken model must never speak the technical
        # disclosure the prompt forbids (model/vendor names, system prompt,
        # infra). Redact the offending sentence(s) before TTS. The honest
        # "I'm an AI assistant for {company}" admission is intentionally allowed.
        from app.services.scripts.prompts.prompt_safety import scan_output_for_leakage
        leaked, cleaned = scan_output_for_leakage(cleaned, protected_values or ())
        if leaked:
            logger.warning("Redacted technical disclosure from agent reply before TTS")

        return cleaned

    @staticmethod
    def _looks_like_scheduling_attempt(response_lower: str) -> bool:
        """
        Detect explicit assistant-led scheduling attempts.

        This avoids false positives where the assistant merely describes product
        features like "appointment booking" without trying to schedule now.
        """
        patterns = [
            r"\b(let me|i can|i will|we can|would you like to)\b.{0,40}\b(schedule|book|set up|arrange)\b.{0,40}\b(call|appointment|meeting)\b",
            r"\b(what|which)\s+(time|day)\s+(works|is best)\b",
            r"\bshall i\s+(book|schedule|set up)\b",
            r"\bcan i\s+(book|schedule|set up)\b",
        ]
        return any(re.search(pattern, response_lower) for pattern in patterns)
    
    def reset_call_tracking(self, call_id: str):
        """Reset fallback tracking for a call (call cleanup)"""
        keys_to_remove = [k for k in self._fallback_index.keys() if k.startswith(f"{call_id}_")]
        for key in keys_to_remove:
            del self._fallback_index[key]


# Singleton instance for easy access
_guardrails_instance: Optional[LLMGuardrails] = None


def get_guardrails(config: LLMGuardrailsConfig = None) -> LLMGuardrails:
    """Get or create guardrails singleton"""
    global _guardrails_instance
    if _guardrails_instance is None or config is not None:
        _guardrails_instance = LLMGuardrails(config)
    return _guardrails_instance
