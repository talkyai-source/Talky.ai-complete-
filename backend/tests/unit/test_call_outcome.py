"""
Unit tests for Call Outcome tracking
Tests explicit outcome determination for QA

Day 17: Ensures deterministic outcome tracking
"""
import pytest
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.models.conversation_state import (
    ConversationState,
    ConversationContext,
    CallOutcomeType,
    UserIntent
)
from app.domain.models.agent_config import (
    AgentConfig,
    AgentGoal,
    ConversationFlow,
    ConversationRule
)


@pytest.fixture
def agent_config():
    """Create test agent configuration"""
    return AgentConfig(
        goal=AgentGoal.APPOINTMENT_CONFIRMATION,
        business_type="dental clinic",
        agent_name="Sarah",
        company_name="Bright Smile Dental",
        rules=ConversationRule(
            allowed_phrases=["appointment", "confirm"],
            forbidden_phrases=["discount", "free"],
            do_not_say_rules=["No medical advice"]
        ),
        flow=ConversationFlow(
            max_objection_attempts=2
        ),
        max_conversation_turns=10
    )


@pytest.fixture
def conversation_engine(agent_config):
    """Create conversation engine instance"""
    return ConversationEngine(agent_config)


class TestDetermineOutcome:
    """Test outcome determination logic"""
    
    def test_outcome_success_when_user_confirmed(self, conversation_engine):
        """Test SUCCESS outcome when user confirms"""
        context = ConversationContext()
        context.user_confirmed = True
        
        outcome = conversation_engine.determine_outcome(
            final_state=ConversationState.CLOSING,
            context=context,
            turn_count=5
        )
        
        assert outcome == CallOutcomeType.SUCCESS
        assert context.goal_achieved is True
        assert context.call_outcome == CallOutcomeType.SUCCESS
    
    def test_outcome_declined_on_goodbye_early(self, conversation_engine):
        """Test DECLINED outcome when user says no early in conversation"""
        context = ConversationContext()
        context.objection_count = 0  # No objections handled
        
        outcome = conversation_engine.determine_outcome(
            final_state=ConversationState.GOODBYE,
            context=context,
            turn_count=2
        )
        
        assert outcome == CallOutcomeType.DECLINED
        assert context.outcome_reason == "user_declined"
    
    def test_outcome_not_interested_after_max_objections(self, conversation_engine):
        """Test NOT_INTERESTED outcome after max objections"""
        context = ConversationContext()
        context.objection_count = 2  # At max
        
        outcome = conversation_engine.determine_outcome(
            final_state=ConversationState.GOODBYE,
            context=context,
            turn_count=8
        )
        
        assert outcome == CallOutcomeType.NOT_INTERESTED
        assert context.outcome_reason == "max_objections"
    
    def test_outcome_transfer_to_human(self, conversation_engine):
        """Test TRANSFER_TO_HUMAN outcome when user requests transfer"""
        context = ConversationContext()
        context.transfer_requested = True
        
        outcome = conversation_engine.determine_outcome(
            final_state=ConversationState.TRANSFER,
            context=context,
            turn_count=4
        )
        
        assert outcome == CallOutcomeType.TRANSFER_TO_HUMAN
    
    def test_outcome_callback_requested(self, conversation_engine):
        """Test CALLBACK_REQUESTED outcome"""
        context = ConversationContext()
        context.callback_requested = True
        
        outcome = conversation_engine.determine_outcome(
            final_state=ConversationState.GOODBYE,
            context=context,
            turn_count=3
        )
        
        assert outcome == CallOutcomeType.CALLBACK_REQUESTED
    
    def test_outcome_max_turns_reached(self, conversation_engine):
        """Test MAX_TURNS_REACHED outcome when turn limit hit"""
        context = ConversationContext()
        
        outcome = conversation_engine.determine_outcome(
            final_state=ConversationState.QUALIFICATION,
            context=context,
            turn_count=10  # At max
        )
        
        assert outcome == CallOutcomeType.MAX_TURNS_REACHED
        assert "turn_limit" in context.outcome_reason
    
    def test_outcome_error_on_llm_failures(self, conversation_engine):
        """Test ERROR outcome when too many LLM errors"""
        context = ConversationContext()
        context.llm_error_count = 2
        
        outcome = conversation_engine.determine_outcome(
            final_state=ConversationState.QUALIFICATION,
            context=context,
            turn_count=5
        )
        
        assert outcome == CallOutcomeType.ERROR
        assert context.outcome_reason == "max_llm_errors"


class TestCallbackIntent:
    """Test CALLBACK intent detection"""
    
    def test_detect_callback_intent_call_back(self, conversation_engine):
        """Test detection of 'call me back' as CALLBACK intent"""
        intent = conversation_engine._detect_intent("Can you call me back later?")
        assert intent == UserIntent.CALLBACK
    
    def test_detect_callback_intent_bad_time(self, conversation_engine):
        """Test detection of 'bad time' as CALLBACK intent"""
        intent = conversation_engine._detect_intent("This is not a good time")
        assert intent == UserIntent.CALLBACK
    
    def test_detect_callback_intent_busy(self, conversation_engine):
        """Test detection of 'busy right now' as CALLBACK intent"""
        intent = conversation_engine._detect_intent("I'm busy right now")
        assert intent == UserIntent.CALLBACK
    
    def test_callback_not_confused_with_objection(self, conversation_engine):
        """Test callback intent is distinct from objection"""
        # 'not now' should be objection, 'call back' should be callback
        intent1 = conversation_engine._detect_intent("not now")
        intent2 = conversation_engine._detect_intent("call me back")
        
        assert intent1 == UserIntent.OBJECTION
        assert intent2 == UserIntent.CALLBACK


class TestConversationContextOutcomeTracking:
    """Test ConversationContext outcome tracking methods"""
    
    def test_set_outcome_success_sets_goal_achieved(self):
        """Test that SUCCESS outcome sets goal_achieved flag"""
        context = ConversationContext()
        
        context.set_outcome(CallOutcomeType.SUCCESS, "appointment_confirmed")
        
        assert context.call_outcome == CallOutcomeType.SUCCESS
        assert context.outcome_reason == "appointment_confirmed"
        assert context.goal_achieved is True
    
    def test_set_outcome_declined_does_not_set_goal_achieved(self):
        """Test that DECLINED outcome does not set goal_achieved"""
        context = ConversationContext()
        
        context.set_outcome(CallOutcomeType.DECLINED, "user_said_no")
        
        assert context.call_outcome == CallOutcomeType.DECLINED
        assert context.goal_achieved is False
    
    def test_increment_llm_error(self):
        """Test LLM error counter increment"""
        context = ConversationContext()
        
        count1 = context.increment_llm_error()
        count2 = context.increment_llm_error()
        
        assert count1 == 1
        assert count2 == 2
        assert context.llm_error_count == 2
    
    def test_context_default_values(self):
        """Test context initializes with correct defaults"""
        context = ConversationContext()
        
        assert context.call_outcome is None
        assert context.outcome_reason is None
        assert context.goal_achieved is False
        assert context.callback_requested is False
        assert context.llm_error_count == 0
