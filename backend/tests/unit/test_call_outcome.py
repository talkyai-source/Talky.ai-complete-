"""Persisted conversation context outcome fields; no legacy engine inference."""
from app.domain.models.conversation_state import ConversationContext, CallOutcomeType











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
