"""
Conversation Engine
Manages conversation state machine and transitions.

Day 17: Added explicit outcome determination for QA tracking.
"""
import re
from typing import Tuple, List, Dict, Optional
import logging

from app.domain.models.conversation_state import (
    ConversationState,
    UserIntent,
    StateTransition,
    ConversationContext,
    CallOutcomeType
)
from app.domain.models.agent_config import AgentConfig
from app.domain.models.conversation import Message

logger = logging.getLogger(__name__)


class ConversationEngine:
    """
    Manages conversation state and transitions
    Sits on top of LLM to control conversation flow
    """
    
    def __init__(self, agent_config: AgentConfig):
        """
        Initialize conversation engine
        
        Args:
            agent_config: Agent configuration
        """
        self.agent_config = agent_config
        self.state_transitions = self._build_transition_map()
        self.intent_patterns = self._build_intent_patterns()
    
    def _build_transition_map(self) -> List[StateTransition]:
        """
        Build state transition map based on agent config
        
        Returns:
            List of state transitions
        """
        transitions = [
            # GREETING transitions
            StateTransition(
                from_state=ConversationState.GREETING,
                to_state=ConversationState.QUALIFICATION,
                trigger=UserIntent.YES,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.GREETING,
                to_state=ConversationState.QUALIFICATION,
                trigger=UserIntent.GREETING,
                priority=9
            ),
            StateTransition(
                from_state=ConversationState.GREETING,
                to_state=ConversationState.GOODBYE,
                trigger=UserIntent.NO,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.GREETING,
                to_state=ConversationState.OBJECTION_HANDLING,
                trigger=UserIntent.UNCERTAIN,
                priority=8
            ),
            StateTransition(
                from_state=ConversationState.GREETING,
                to_state=ConversationState.TRANSFER,
                trigger=UserIntent.REQUEST_HUMAN,
                priority=10
            ),
            
            # QUALIFICATION transitions
            StateTransition(
                from_state=ConversationState.QUALIFICATION,
                to_state=ConversationState.CLOSING,
                trigger=UserIntent.YES,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.QUALIFICATION,
                to_state=ConversationState.GOODBYE,
                trigger=UserIntent.NO,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.QUALIFICATION,
                to_state=ConversationState.OBJECTION_HANDLING,
                trigger=UserIntent.UNCERTAIN,
                priority=8
            ),
            StateTransition(
                from_state=ConversationState.QUALIFICATION,
                to_state=ConversationState.OBJECTION_HANDLING,
                trigger=UserIntent.OBJECTION,
                priority=8
            ),
            StateTransition(
                from_state=ConversationState.QUALIFICATION,
                to_state=ConversationState.TRANSFER,
                trigger=UserIntent.REQUEST_HUMAN,
                priority=10
            ),
            
            # OBJECTION_HANDLING transitions
            StateTransition(
                from_state=ConversationState.OBJECTION_HANDLING,
                to_state=ConversationState.CLOSING,
                trigger=UserIntent.YES,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.OBJECTION_HANDLING,
                to_state=ConversationState.GOODBYE,
                trigger=UserIntent.NO,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.OBJECTION_HANDLING,
                to_state=ConversationState.TRANSFER,
                trigger=UserIntent.REQUEST_HUMAN,
                priority=10
            ),
            
            # CLOSING transitions
            StateTransition(
                from_state=ConversationState.CLOSING,
                to_state=ConversationState.GOODBYE,
                trigger=UserIntent.YES,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.CLOSING,
                to_state=ConversationState.GOODBYE,
                trigger=UserIntent.NO,
                priority=10
            ),
            StateTransition(
                from_state=ConversationState.CLOSING,
                to_state=ConversationState.GOODBYE,
                trigger=UserIntent.GOODBYE,
                priority=10
            ),
            
            # TRANSFER - terminal state
            # GOODBYE - terminal state
        ]
        
        return sorted(transitions, key=lambda t: t.priority, reverse=True)
    
    def _build_intent_patterns(self) -> Dict[UserIntent, List[str]]:
        """
        Build regex patterns for intent detection
        
        Returns:
            Dictionary mapping intents to regex patterns
        """
        # Note: Order matters! More specific/negative intents should be checked first
        # to avoid false positives from broader patterns like YES
        return {
            UserIntent.YES: [
                r'\b(yes|yeah|yep|okay|ok|absolutely|definitely|confirm)\b',
                r'\b(sounds good|that works|perfect|great)\b',
                r'\b(i can do that|i will do that|i would like that)\b',
                r'^(sure|correct|right)$',  # Only match if it's the whole response
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
    
    def _detect_intent(self, user_text: str) -> UserIntent:
        """
        Detect user intent from text using pattern matching
        
        Args:
            user_text: User's input text
        
        Returns:
            Detected intent
        """
        user_text_lower = user_text.lower().strip()
        
        # Check intents in priority order: more specific/negative intents first
        # to avoid false positives from broader patterns like YES
        intent_priority = [
            UserIntent.REQUEST_HUMAN,  # Highest priority - user wants human
            UserIntent.GOODBYE,        # User wants to end
            UserIntent.CALLBACK,       # User wants callback (Day 17)
            UserIntent.NO,             # Explicit rejection
            UserIntent.UNCERTAIN,      # Hesitation/uncertainty
            UserIntent.OBJECTION,      # Objections/concerns
            UserIntent.GREETING,       # Greetings
            UserIntent.YES,            # Affirmative (checked last to avoid false positives)
        ]
        
        for intent in intent_priority:
            patterns = self.intent_patterns.get(intent, [])
            for pattern in patterns:
                if re.search(pattern, user_text_lower, re.IGNORECASE):
                    logger.info(f"Detected intent: {intent.value} from text: '{user_text}'")
                    return intent
        
        # Default to UNKNOWN
        logger.info(f"No clear intent detected from text: '{user_text}'")
        return UserIntent.UNKNOWN
    
    def _transition_state(
        self,
        current_state: ConversationState,
        intent: UserIntent,
        context: ConversationContext
    ) -> ConversationState:
        """
        Determine next state based on current state, intent, and context
        
        Args:
            current_state: Current conversation state (can be enum or string)
            intent: Detected user intent
            context: Conversation context
        
        Returns:
            Next conversation state (always returns enum)
        """
        # Normalize current_state to string for comparison
        current_state_value = current_state.value if hasattr(current_state, 'value') else str(current_state)
        
        # Ensure we have an enum for return value
        current_state_enum = current_state if isinstance(current_state, ConversationState) else ConversationState(current_state)
        
        # Handle objection limits
        if current_state_value == ConversationState.OBJECTION_HANDLING.value:
            max_attempts = self.agent_config.flow.max_objection_attempts
            if context.objection_count >= max_attempts and intent in [UserIntent.UNCERTAIN, UserIntent.OBJECTION]:
                logger.info(f"Max objection attempts ({max_attempts}) reached, moving to GOODBYE")
                return ConversationState.GOODBYE
        
        # Find matching transition
        # Note: StateTransition uses use_enum_values=True, so we need to compare string values
        intent_value = intent.value if hasattr(intent, 'value') else str(intent)
        
        for transition in self.state_transitions:
            trans_from = transition.from_state if isinstance(transition.from_state, str) else transition.from_state.value
            trans_trigger = transition.trigger if isinstance(transition.trigger, str) else transition.trigger.value
            trans_to = transition.to_state
            
            if trans_from == current_state_value and trans_trigger == intent_value:
                # Return as enum
                if isinstance(trans_to, str):
                    result_state = ConversationState(trans_to)
                else:
                    result_state = trans_to
                logger.info(f"State transition: {current_state_value} -> {result_state.value} (intent: {intent_value})")
                return result_state
        
        # No transition found, stay in current state (return as enum)
        logger.warning(f"No transition found for state={current_state_value}, intent={intent_value}. Staying in current state.")
        return current_state_enum
    
    def _get_state_instruction(
        self,
        state: ConversationState,
        intent: UserIntent,
        context: ConversationContext
    ) -> str:
        """
        Get instruction for LLM based on state and intent
        
        Args:
            state: Current conversation state
            intent: Detected user intent
            context: Conversation context
        
        Returns:
            Instruction string for LLM
        """
        instructions = {
            ConversationState.GREETING: (
                f"You are starting the conversation. Greet the person warmly, "
                f"introduce yourself as {self.agent_config.agent_name} from {self.agent_config.company_name}, "
                f"and briefly state the purpose of your call: to {self.agent_config.get_goal_description()}. "
                f"Keep it brief and friendly."
            ),
            ConversationState.QUALIFICATION: (
                f"Ask qualifying questions to {self.agent_config.get_goal_description()}. "
                f"Be specific and direct. Listen for their needs and concerns."
            ),
            ConversationState.OBJECTION_HANDLING: (
                f"The user expressed uncertainty or an objection. "
                f"Address their concern empathetically and provide helpful information. "
                f"Ask a clarifying question to understand their hesitation better. "
                f"This is objection #{context.objection_count + 1}."
            ),
            ConversationState.CLOSING: (
                f"The user has shown interest. Confirm the next steps clearly. "
                f"If this is an appointment confirmation, restate the time and date. "
                f"Thank them and end positively."
            ),
            ConversationState.TRANSFER: (
                f"The user requested to speak with a human. "
                f"Acknowledge their request politely and inform them you're transferring them now. "
                f"Thank them for their time."
            ),
            ConversationState.GOODBYE: (
                f"End the conversation politely. "
                f"Thank them for their time and wish them well. "
                f"Keep it brief and warm."
            ),
        }
        
        base_instruction = instructions.get(state, "Continue the conversation naturally.")
        
        # Add intent-specific guidance
        if intent == UserIntent.NO and state != ConversationState.GOODBYE:
            base_instruction += " The user declined. Be gracious and don't push."
        elif intent == UserIntent.UNCERTAIN:
            base_instruction += " The user seems uncertain. Be patient and helpful."
        
        return base_instruction
    
    async def handle_user_input(
        self,
        current_state: ConversationState,
        user_text: str,
        conversation_history: List[Message],
        context: Optional[ConversationContext] = None
    ) -> Tuple[ConversationState, str, UserIntent]:
        """
        Process user input and determine next state + agent instruction
        
        Args:
            current_state: Current conversation state (can be enum or string due to Pydantic use_enum_values)
            user_text: User's input text
            conversation_history: Full conversation history
            context: Conversation context (created if None)
        
        Returns:
            Tuple of (new_state, agent_instruction, detected_intent)
        """
        # Initialize context if not provided
        if context is None:
            context = ConversationContext()
        
        # Normalize current_state to enum (handles Pydantic use_enum_values=True)
        if isinstance(current_state, str):
            current_state = ConversationState(current_state)
        
        # 1. Detect user intent
        intent = self._detect_intent(user_text)
        
        # 2. Update context based on intent
        if intent in [UserIntent.UNCERTAIN, UserIntent.OBJECTION]:
            context.increment_objection()
        
        if intent == UserIntent.REQUEST_HUMAN:
            context.transfer_requested = True
        
        if intent == UserIntent.YES and current_state == ConversationState.CLOSING:
            context.user_confirmed = True
        
        # 3. Determine next state (always returns enum)
        new_state = self._transition_state(current_state, intent, context)
        
        # 4. Generate agent instruction for LLM
        agent_instruction = self._get_state_instruction(new_state, intent, context)
        
        logger.info(
            f"ConversationEngine: state={current_state.value}->{new_state.value}, "
            f"intent={intent.value}, objections={context.objection_count}"
        )
        
        return new_state, agent_instruction, intent
    
    def should_end_conversation(
        self,
        state: ConversationState,
        turn_count: int,
        context: ConversationContext
    ) -> Tuple[bool, str]:
        """
        Determine if conversation should end
        
        Args:
            state: Current conversation state
            turn_count: Number of turns so far
            context: Conversation context
        
        Returns:
            Tuple of (should_end, reason)
        """
        # Terminal states
        if state in [ConversationState.GOODBYE, ConversationState.TRANSFER]:
            return True, f"terminal_state_{state.value}"
        
        # Max turns exceeded
        if turn_count >= self.agent_config.max_conversation_turns:
            return True, "max_turns_exceeded"
        
        # User confirmed in closing state
        if state == ConversationState.CLOSING and context.user_confirmed:
            return True, "user_confirmed"
        
        return False, ""
    
    def determine_outcome(
        self,
        final_state: ConversationState,
        context: ConversationContext,
        turn_count: int = 0
    ) -> CallOutcomeType:
        """
        Determine explicit call outcome for QA tracking.
        
        This method provides deterministic outcome calculation based on
        final conversation state and context, enabling consistent QA.
        
        Args:
            final_state: Final conversation state when call ended
            context: Conversation context with tracking data
            turn_count: Number of conversation turns
            
        Returns:
            CallOutcomeType - explicit outcome for analytics
        """
        # Check for error condition (LLM failures)
        if context.llm_error_count >= 2:
            context.set_outcome(CallOutcomeType.ERROR, "max_llm_errors")
            return CallOutcomeType.ERROR
        
        # Check for callback request
        if context.callback_requested:
            context.set_outcome(CallOutcomeType.CALLBACK_REQUESTED, "user_requested_callback")
            return CallOutcomeType.CALLBACK_REQUESTED
        
        # Check for transfer to human
        if final_state == ConversationState.TRANSFER or context.transfer_requested:
            context.set_outcome(CallOutcomeType.TRANSFER_TO_HUMAN, "user_requested_human")
            return CallOutcomeType.TRANSFER_TO_HUMAN
        
        # Check for success (goal achieved)
        if context.user_confirmed or context.goal_achieved:
            context.set_outcome(CallOutcomeType.SUCCESS, "goal_achieved")
            return CallOutcomeType.SUCCESS
        
        # Check for max turns reached
        if turn_count >= self.agent_config.max_conversation_turns:
            context.set_outcome(CallOutcomeType.MAX_TURNS_REACHED, f"turn_limit_{turn_count}")
            return CallOutcomeType.MAX_TURNS_REACHED
        
        # Determine based on final state
        if final_state == ConversationState.GOODBYE:
            # Check objection history to determine if declined or not interested
            if context.objection_count >= self.agent_config.flow.max_objection_attempts:
                context.set_outcome(CallOutcomeType.NOT_INTERESTED, "max_objections")
                return CallOutcomeType.NOT_INTERESTED
            else:
                context.set_outcome(CallOutcomeType.DECLINED, "user_declined")
                return CallOutcomeType.DECLINED
        
        if final_state == ConversationState.CLOSING:
            # Reached closing but didn't confirm
            context.set_outcome(CallOutcomeType.UNKNOWN, "closing_not_confirmed")
            return CallOutcomeType.UNKNOWN
        
        # Fallback
        context.set_outcome(CallOutcomeType.UNKNOWN, f"state_{final_state.value}")
        return CallOutcomeType.UNKNOWN
