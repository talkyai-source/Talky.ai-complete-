"""
Unit tests for Conversation Engine
Tests state machine logic, intent detection, and state transitions
"""
import pytest
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.models.conversation_state import (
    ConversationState,
    UserIntent,
    ConversationContext
)
from app.domain.models.agent_config import (
    AgentConfig,
    AgentGoal,
    ConversationRule,
    ConversationFlow
)
from app.domain.models.conversation import Message, MessageRole


@pytest.fixture
def agent_config():
    """Create test agent configuration"""
    return AgentConfig(
        goal=AgentGoal.APPOINTMENT_CONFIRMATION,
        business_type="dental clinic",
        agent_name="Sarah",
        company_name="Bright Smile Dental",
        rules=ConversationRule(
            allowed_phrases=["appointment", "confirm", "reschedule"],
            forbidden_phrases=["discount", "free"],
            do_not_say_rules=["No medical advice", "No pricing discussions"],
            max_follow_up_questions=2
        ),
        flow=ConversationFlow(
            on_yes="closing",
            on_no="goodbye",
            on_uncertain="objection_handling",
            on_objection="objection_handling",
            max_objection_attempts=2
        ),
        tone="polite, professional, conversational",
        max_conversation_turns=10
    )


@pytest.fixture
def conversation_engine(agent_config):
    """Create conversation engine instance"""
    return ConversationEngine(agent_config)


class TestIntentDetection:
    """Test intent detection from user input"""
    
    def test_detect_yes_intent(self, conversation_engine):
        """Test detection of YES intent"""
        test_cases = [
            "yes",
            "yeah sure",
            "okay that works",
            "sounds good",
            "absolutely",
            "I can do that"
        ]
        
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.YES, f"Failed to detect YES in: '{text}'"
    
    def test_detect_no_intent(self, conversation_engine):
        """Test detection of NO intent"""
        test_cases = [
            "no",
            "nope",
            "not interested",
            "I can't make it",
            "don't want to",
            "cancel"
        ]
        
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.NO, f"Failed to detect NO in: '{text}'"
    
    def test_detect_uncertain_intent(self, conversation_engine):
        """Test detection of UNCERTAIN intent"""
        test_cases = [
            "maybe",
            "I'm not sure",
            "let me think",
            "hmm",
            "I don't know",
            "possibly"
        ]
        
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.UNCERTAIN, f"Failed to detect UNCERTAIN in: '{text}'"
    
    def test_detect_objection_intent(self, conversation_engine):
        """Test detection of OBJECTION intent"""
        test_cases = [
            "but I'm busy",
            "too expensive",
            "not right now",
            "don't have time",
            "wait a minute"
        ]
        
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.OBJECTION, f"Failed to detect OBJECTION in: '{text}'"
    
    def test_detect_request_human_intent(self, conversation_engine):
        """Test detection of REQUEST_HUMAN intent"""
        test_cases = [
            "speak to a person",
            "transfer me to an agent",
            "I want to talk to a human",
            "get me a representative",
            "real person please"
        ]
        
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.REQUEST_HUMAN, f"Failed to detect REQUEST_HUMAN in: '{text}'"
    
    def test_detect_greeting_intent(self, conversation_engine):
        """Test detection of GREETING intent"""
        test_cases = [
            "hello",
            "hi there",
            "good morning",
            "hey"
        ]
        
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.GREETING, f"Failed to detect GREETING in: '{text}'"
    
    def test_detect_unknown_intent(self, conversation_engine):
        """Test detection of UNKNOWN intent for unclear input"""
        test_cases = [
            "what?",
            "huh?",
            "random text here",
            "xyz123"
        ]
        
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.UNKNOWN, f"Should detect UNKNOWN for: '{text}'"


class TestStateTransitions:
    """Test state transition logic"""
    
    def test_greeting_to_qualification_on_yes(self, conversation_engine):
        """Test transition from GREETING to QUALIFICATION on YES"""
        context = ConversationContext()
        new_state = conversation_engine._transition_state(
            ConversationState.GREETING,
            UserIntent.YES,
            context
        )
        assert new_state == ConversationState.QUALIFICATION
    
    def test_greeting_to_goodbye_on_no(self, conversation_engine):
        """Test transition from GREETING to GOODBYE on NO"""
        context = ConversationContext()
        new_state = conversation_engine._transition_state(
            ConversationState.GREETING,
            UserIntent.NO,
            context
        )
        assert new_state == ConversationState.GOODBYE
    
    def test_greeting_to_objection_on_uncertain(self, conversation_engine):
        """Test transition from GREETING to OBJECTION_HANDLING on UNCERTAIN"""
        context = ConversationContext()
        new_state = conversation_engine._transition_state(
            ConversationState.GREETING,
            UserIntent.UNCERTAIN,
            context
        )
        assert new_state == ConversationState.OBJECTION_HANDLING
    
    def test_qualification_to_closing_on_yes(self, conversation_engine):
        """Test transition from QUALIFICATION to CLOSING on YES"""
        context = ConversationContext()
        new_state = conversation_engine._transition_state(
            ConversationState.QUALIFICATION,
            UserIntent.YES,
            context
        )
        assert new_state == ConversationState.CLOSING
    
    def test_objection_handling_to_closing_on_yes(self, conversation_engine):
        """Test transition from OBJECTION_HANDLING to CLOSING on YES"""
        context = ConversationContext()
        new_state = conversation_engine._transition_state(
            ConversationState.OBJECTION_HANDLING,
            UserIntent.YES,
            context
        )
        assert new_state == ConversationState.CLOSING
    
    def test_any_state_to_transfer_on_request_human(self, conversation_engine):
        """Test transition to TRANSFER from any state on REQUEST_HUMAN"""
        context = ConversationContext()
        states = [
            ConversationState.GREETING,
            ConversationState.QUALIFICATION,
            ConversationState.OBJECTION_HANDLING
        ]
        
        for state in states:
            new_state = conversation_engine._transition_state(
                state,
                UserIntent.REQUEST_HUMAN,
                context
            )
            assert new_state == ConversationState.TRANSFER, f"Failed from state: {state}"
    
    def test_max_objections_forces_goodbye(self, conversation_engine):
        """Test that max objection attempts forces GOODBYE"""
        context = ConversationContext()
        context.objection_count = 2  # Max is 2
        
        new_state = conversation_engine._transition_state(
            ConversationState.OBJECTION_HANDLING,
            UserIntent.UNCERTAIN,
            context
        )
        assert new_state == ConversationState.GOODBYE


class TestConversationContext:
    """Test conversation context tracking"""
    
    def test_context_initialization(self):
        """Test context initializes with correct defaults"""
        context = ConversationContext()
        assert context.objection_count == 0
        assert context.follow_up_count == 0
        assert context.user_confirmed is False
        assert context.transfer_requested is False
    
    def test_increment_objection(self):
        """Test objection counter increment"""
        context = ConversationContext()
        count = context.increment_objection()
        assert count == 1
        assert context.objection_count == 1
        
        count = context.increment_objection()
        assert count == 2
        assert context.objection_count == 2
    
    def test_increment_follow_up(self):
        """Test follow-up counter increment"""
        context = ConversationContext()
        count = context.increment_follow_up()
        assert count == 1
        assert context.follow_up_count == 1
    
    def test_reset_objection_tracking(self):
        """Test resetting objection tracking"""
        context = ConversationContext()
        context.increment_objection()
        context.increment_follow_up()
        
        context.reset_objection_tracking()
        assert context.objection_count == 0
        assert context.follow_up_count == 0


@pytest.mark.asyncio
class TestHandleUserInput:
    """Test the main handle_user_input method"""
    
    async def test_handle_yes_from_greeting(self, conversation_engine):
        """Test handling YES from GREETING state"""
        new_state, instruction, intent = await conversation_engine.handle_user_input(
            current_state=ConversationState.GREETING,
            user_text="yes, I'm here",
            conversation_history=[]
        )
        
        assert new_state == ConversationState.QUALIFICATION
        assert intent == UserIntent.YES
        assert "qualifying" in instruction.lower() or "question" in instruction.lower()
    
    async def test_handle_no_from_greeting(self, conversation_engine):
        """Test handling NO from GREETING state"""
        new_state, instruction, intent = await conversation_engine.handle_user_input(
            current_state=ConversationState.GREETING,
            user_text="no thanks",
            conversation_history=[]
        )
        
        assert new_state == ConversationState.GOODBYE
        assert intent == UserIntent.NO
        assert "end" in instruction.lower() or "thank" in instruction.lower()
    
    async def test_handle_uncertain_increments_context(self, conversation_engine):
        """Test that uncertain intent increments objection count"""
        context = ConversationContext()
        
        new_state, instruction, intent = await conversation_engine.handle_user_input(
            current_state=ConversationState.QUALIFICATION,
            user_text="I'm not sure",
            conversation_history=[],
            context=context
        )
        
        assert new_state == ConversationState.OBJECTION_HANDLING
        assert intent == UserIntent.UNCERTAIN
        assert context.objection_count == 1
    
    async def test_handle_request_human_sets_transfer_flag(self, conversation_engine):
        """Test that request human sets transfer flag"""
        context = ConversationContext()
        
        new_state, instruction, intent = await conversation_engine.handle_user_input(
            current_state=ConversationState.QUALIFICATION,
            user_text="I want to speak to a person",
            conversation_history=[],
            context=context
        )
        
        assert new_state == ConversationState.TRANSFER
        assert intent == UserIntent.REQUEST_HUMAN
        assert context.transfer_requested is True


class TestShouldEndConversation:
    """Test conversation ending logic"""
    
    def test_should_end_on_goodbye_state(self, conversation_engine):
        """Test should end on GOODBYE state"""
        context = ConversationContext()
        should_end, reason = conversation_engine.should_end_conversation(
            state=ConversationState.GOODBYE,
            turn_count=3,
            context=context
        )
        assert should_end is True
        assert "terminal_state" in reason
    
    def test_should_end_on_transfer_state(self, conversation_engine):
        """Test should end on TRANSFER state"""
        context = ConversationContext()
        should_end, reason = conversation_engine.should_end_conversation(
            state=ConversationState.TRANSFER,
            turn_count=3,
            context=context
        )
        assert should_end is True
        assert "terminal_state" in reason
    
    def test_should_end_on_max_turns(self, conversation_engine):
        """Test should end when max turns exceeded"""
        context = ConversationContext()
        should_end, reason = conversation_engine.should_end_conversation(
            state=ConversationState.QUALIFICATION,
            turn_count=10,  # Max is 10
            context=context
        )
        assert should_end is True
        assert "max_turns" in reason
    
    def test_should_end_on_user_confirmed(self, conversation_engine):
        """Test should end when user confirmed in CLOSING state"""
        context = ConversationContext()
        context.user_confirmed = True
        
        should_end, reason = conversation_engine.should_end_conversation(
            state=ConversationState.CLOSING,
            turn_count=3,
            context=context
        )
        assert should_end is True
        assert "confirmed" in reason
    
    def test_should_not_end_in_active_conversation(self, conversation_engine):
        """Test should not end during active conversation"""
        context = ConversationContext()
        should_end, reason = conversation_engine.should_end_conversation(
            state=ConversationState.QUALIFICATION,
            turn_count=3,
            context=context
        )
        assert should_end is False
        assert reason == ""


class TestAgentConfig:
    """Test agent configuration"""
    
    def test_agent_config_creation(self, agent_config):
        """Test agent config creates successfully"""
        assert agent_config.goal == AgentGoal.APPOINTMENT_CONFIRMATION
        assert agent_config.agent_name == "Sarah"
        assert agent_config.company_name == "Bright Smile Dental"
    
    def test_get_goal_description(self, agent_config):
        """Test goal description generation"""
        description = agent_config.get_goal_description()
        assert "confirm" in description.lower()
        assert "appointment" in description.lower()
    
    def test_validate_rules_no_conflicts(self, agent_config):
        """Test rule validation with no conflicts"""
        assert agent_config.validate_rules() is True
    
    def test_validate_rules_with_conflicts(self):
        """Test rule validation detects conflicts"""
        config = AgentConfig(
            goal=AgentGoal.APPOINTMENT_CONFIRMATION,
            business_type="dental clinic",
            agent_name="Sarah",
            company_name="Test Dental",
            rules=ConversationRule(
                allowed_phrases=["discount", "appointment"],
                forbidden_phrases=["discount", "free"]  # Conflict!
            )
        )
        
        with pytest.raises(ValueError, match="Conflicting phrases"):
            config.validate_rules()
