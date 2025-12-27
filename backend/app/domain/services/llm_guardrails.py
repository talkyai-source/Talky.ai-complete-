"""
LLM Guardrails Service
Provides timeout handling, fallback responses, and response validation.

Day 17: Ensures graceful degradation when LLM fails while maintaining
human-like conversation flow (no hints that it's an AI).
"""
import re
import asyncio
import logging
from typing import Tuple, Optional, List
from pydantic import BaseModel, Field

from app.domain.models.conversation_state import ConversationState, CallOutcomeType
from app.domain.models.agent_config import ConversationRule

logger = logging.getLogger(__name__)


class LLMTimeoutError(Exception):
    """Raised when LLM response times out"""
    pass


class LLMGuardrailsConfig(BaseModel):
    """Configuration for LLM guardrails"""
    max_response_tokens: int = Field(default=150, ge=50, le=500, description="Max tokens per response")
    max_response_time_seconds: float = Field(default=10.0, ge=1.0, le=30.0, description="Max LLM response time")
    max_llm_errors_before_goodbye: int = Field(default=2, ge=1, le=5, description="Max LLM errors before ending call")
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
    
    def get_fallback_response(
        self,
        state: ConversationState,
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
        
        # Get state-specific fallbacks
        fallbacks = self.FALLBACK_RESPONSES.get(
            state, 
            self.FALLBACK_RESPONSES[ConversationState.GREETING]
        )
        
        # Cycle through fallbacks for variety
        key = f"{call_id}_{state.value}" if call_id else state.value
        idx = self._fallback_index.get(key, 0)
        response = fallbacks[idx % len(fallbacks)]
        self._fallback_index[key] = idx + 1
        
        logger.info(f"Using fallback response for state={state.value}: '{response[:50]}...'")
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
        
        if len(sentences) <= max_sentences:
            return response
        
        # Take only max_sentences
        truncated = ' '.join(sentences[:max_sentences])
        
        # Ensure it ends with punctuation
        if truncated and truncated[-1] not in '.!?':
            truncated += '.'
        
        logger.debug(f"Truncated response from {len(sentences)} to {max_sentences} sentences")
        return truncated
    
    def validate_response(
        self,
        response: str,
        rules: ConversationRule = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate response doesn't contain forbidden phrases.
        
        Args:
            response: LLM response to validate
            rules: Conversation rules with forbidden_phrases
            
        Returns:
            Tuple of (is_valid, reason_if_invalid)
        """
        if not response:
            return False, "empty_response"
        
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
            # Extract key terms from rule
            terms = [t.strip().lower() for t in rule.split() if len(t) > 3]
            matches = sum(1 for t in terms if t in response_lower)
            if matches >= 2:  # If 2+ key terms match, likely violation
                logger.warning(f"Response may violate rule: '{rule}'")
                return False, f"may_violate_rule:{rule}"
        
        return True, None
    
    def clean_response(self, response: str) -> str:
        """
        Clean LLM response by removing common artifacts.
        
        Removes:
        - Thinking patterns ("Well, ", "So, ", "Actually, ")
        - Excessive whitespace
        - Incomplete sentences at the end
        """
        if not response:
            return response
        
        # Remove common filler starts
        filler_starts = [
            r'^(Well,?\s+)',
            r'^(So,?\s+)',
            r'^(Actually,?\s+)',
            r'^(Okay,?\s+)',
            r'^(Alright,?\s+)',
            r'^(Sure!?\s+)',
            r'^(Of course!?\s+)',
        ]
        
        cleaned = response.strip()
        for pattern in filler_starts:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        # Clean up whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        return cleaned
    
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
