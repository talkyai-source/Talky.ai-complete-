"""
Intent Detector Service
LLM-based intent detection with regex fallback.

Extracted from ConversationEngine to enable:
- LLM-powered intent classification (more accurate)
- Regex fallback for reliability and offline scenarios
- Independent testability
"""
import re
import logging
from typing import Dict, List, Optional

from app.domain.models.conversation_state import UserIntent

logger = logging.getLogger(__name__)


# System prompt for LLM intent classification (kept minimal for speed)
_LLM_INTENT_PROMPT = """You are an intent classifier for a phone conversation.
Classify the user's message into exactly ONE of these intents:
yes, no, uncertain, objection, request_human, request_info, greeting, goodbye, callback, unknown

Rules:
- "yes" = agreement, confirmation, affirmative
- "no" = rejection, refusal, decline  
- "uncertain" = hesitation, maybe, not sure
- "objection" = pushback on price/time/need but not outright rejection
- "request_human" = wants to speak to a real person
- "request_info" = asking a question or requesting details
- "greeting" = hello, hi, good morning
- "goodbye" = ending the conversation
- "callback" = asks to be called back later, bad time
- "unknown" = cannot determine intent

Respond with ONLY the intent label, nothing else."""


class IntentDetector:
    """
    Detects user intent using LLM with regex fallback.
    
    Usage:
        detector = IntentDetector(llm_provider=groq_provider)
        intent = await detector.detect_intent("yes, that sounds great")
        # Returns UserIntent.YES
    
    The LLM path provides more nuanced classification (handles sarcasm,
    compound sentences, implicit intent). Falls back to regex when:
    - No LLM provider configured
    - LLM call fails or times out
    - LLM returns an invalid response
    """
    
    def __init__(self, llm_provider=None):
        """
        Args:
            llm_provider: Optional LLMProvider instance for LLM-based detection.
                          If None, only regex detection is used.
        """
        self._llm = llm_provider
        self._regex_patterns = self._build_regex_patterns()
        self._intent_priority = [
            UserIntent.REQUEST_HUMAN,  # Highest — user wants human
            UserIntent.GOODBYE,        # User wants to end
            UserIntent.CALLBACK,       # User wants callback
            UserIntent.NO,             # Explicit rejection
            UserIntent.UNCERTAIN,      # Hesitation/uncertainty
            UserIntent.OBJECTION,      # Objections/concerns
            UserIntent.GREETING,       # Greetings
            UserIntent.YES,            # Affirmative (last to avoid false positives)
        ]
        
        # Valid intent values for LLM output validation
        self._valid_intents = {intent.value for intent in UserIntent}
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    async def detect_intent(
        self,
        user_text: str,
        conversation_state: str = ""
    ) -> UserIntent:
        """
        Detect intent from user text.
        
        Tries LLM classification first, falls back to regex on failure.
        
        Args:
            user_text: The user's spoken/typed input
            conversation_state: Current conversation state for LLM context
            
        Returns:
            Detected UserIntent
        """
        if not user_text or not user_text.strip():
            return UserIntent.UNKNOWN
        
        # Try LLM-based detection first
        if self._llm:
            try:
                llm_intent = await self._detect_via_llm(user_text, conversation_state)
                if llm_intent != UserIntent.UNKNOWN:
                    logger.info(
                        f"LLM intent: {llm_intent.value} for text: '{user_text}'"
                    )
                    return llm_intent
            except Exception as e:
                logger.warning(f"LLM intent detection failed, using regex fallback: {e}")
        
        # Fallback to regex
        return self._detect_via_regex(user_text)
    
    # =========================================================================
    # LLM-Based Detection
    # =========================================================================
    
    async def _detect_via_llm(
        self,
        user_text: str,
        conversation_state: str
    ) -> UserIntent:
        """
        Classify intent using the LLM provider.
        
        Uses a constrained prompt that returns a single intent label.
        """
        from app.domain.models.conversation import Message
        
        context_note = f" (conversation state: {conversation_state})" if conversation_state else ""
        user_message = Message(
            role="user",
            content=f"Classify this message{context_note}: \"{user_text}\""
        )
        
        # Collect LLM response (non-streaming for classification)
        response_text = ""
        async for token in self._llm.stream_chat(
            messages=[user_message],
            system_prompt=_LLM_INTENT_PROMPT,
            temperature=0.1,  # Low temperature for deterministic classification
            max_tokens=10,    # Only need one word
        ):
            response_text += token
        
        # Parse and validate the intent
        intent_value = response_text.strip().lower()
        
        if intent_value in self._valid_intents:
            return UserIntent(intent_value)
        
        logger.warning(f"LLM returned invalid intent '{intent_value}', falling back to regex")
        return UserIntent.UNKNOWN
    
    # =========================================================================
    # Regex-Based Detection (Fallback)
    # =========================================================================
    
    def _detect_via_regex(self, user_text: str) -> UserIntent:
        """
        Detect intent using regex pattern matching.
        
        This is the original logic extracted from ConversationEngine._detect_intent().
        Patterns are checked in priority order.
        """
        user_text_lower = user_text.lower().strip()
        
        for intent in self._intent_priority:
            patterns = self._regex_patterns.get(intent, [])
            for pattern in patterns:
                if re.search(pattern, user_text_lower, re.IGNORECASE):
                    logger.info(f"Regex intent: {intent.value} for text: '{user_text}'")
                    return intent
        
        logger.info(f"No clear intent detected from text: '{user_text}'")
        return UserIntent.UNKNOWN
    
    @staticmethod
    def _build_regex_patterns() -> Dict[UserIntent, List[str]]:
        """
        Build regex patterns for intent detection.
        
        Extracted verbatim from ConversationEngine._build_intent_patterns().
        """
        return {
            UserIntent.YES: [
                r'\b(yes|yeah|yep|okay|ok|absolutely|definitely|confirm)\b',
                r'\b(sounds good|that works|perfect|great)\b',
                r'\b(i can do that|i will do that|i would like that)\b',
                r'^(sure|correct|right)$',
                r'\b(sure thing|that\'s right|that\'s correct)\b',
            ],
            UserIntent.NO: [
                r"\b(no|nope|nah|not really)\b",
                r"\b(i can'?t|i cannot|i won'?t)\b",
                r"\b(don'?t want|don'?t need|not interested)\b",
                r'\b(cancel|decline)\b',
            ],
            UserIntent.UNCERTAIN: [
                r"\b(maybe|perhaps|possibly|hmm+|uh+)\b",
                r"\b(not sure|i'?m not sure|i'?m not certain)\b",
                r"\b(i don'?t know)\b",
                r"\b(let me (think|check))\b",
            ],
            UserIntent.OBJECTION: [
                r'\b(but i|however)\b',
                r'\b(too (expensive|costly|much))\b',
                r"\b(don'?t have (time|money))\b",
                r'\bnot (right )?now\b',
                r'\bnot today\b',
                r'\b(wait|hold on)\b',
            ],
            UserIntent.REQUEST_HUMAN: [
                r'\b(speak to|talk to|transfer|human|person|representative|agent|manager)\b',
                r'\b(real person|actual person)\b',
            ],
            UserIntent.CALLBACK: [
                r'\b(call (me )?(back|later|another time))\b',
                r'\b(call again|try again later)\b',
                r'\b(not a good time|bad time)\b',
                r'\b(busy right now|in a meeting)\b',
            ],
            UserIntent.GREETING: [
                r'\b(hello|hi|hey|good (morning|afternoon|evening))\b',
            ],
            UserIntent.GOODBYE: [
                r'\b(bye|goodbye|see you|talk later|have a (good|nice) day)\b',
            ],
        }
