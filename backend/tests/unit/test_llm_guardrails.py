"""
Unit tests for LLM Guardrails
Tests fallback responses, timeout handling, and response validation

Day 17: Ensures graceful degradation when LLM fails
"""
import pytest
from app.domain.services.llm_guardrails import (
    LLMGuardrails,
    LLMGuardrailsConfig,
    get_guardrails
)
from app.domain.models.conversation_state import ConversationState
from app.domain.models.agent_config import ConversationRule


class TestFallbackResponses:
    """Test human-like fallback response selection"""
    
    def test_get_fallback_for_greeting_state(self):
        """Test fallback for GREETING state is human-like"""
        guardrails = LLMGuardrails()
        response, should_end = guardrails.get_fallback_response(
            state=ConversationState.GREETING,
            call_id="test-call-1",
            error_count=0
        )
        
        assert response is not None
        assert len(response) > 0
        assert should_end is False
        # Verify no AI-revealing phrases (check for standalone words, not substrings)
        assert "error" not in response.lower()
        assert "system" not in response.lower()
        assert " ai " not in f" {response.lower()} "  # Check for standalone 'ai'
        assert "robot" not in response.lower()
    
    def test_get_fallback_for_qualification_state(self):
        """Test fallback for QUALIFICATION state"""
        guardrails = LLMGuardrails()
        response, should_end = guardrails.get_fallback_response(
            state=ConversationState.QUALIFICATION,
            call_id="test-call-2",
            error_count=0
        )
        
        assert response is not None
        assert should_end is False
        # Should be a question or continuation
        assert "?" in response or "." in response
    
    def test_fallback_cycles_through_options(self):
        """Test that repeated calls cycle through fallback options"""
        guardrails = LLMGuardrails()
        call_id = "test-call-3"
        
        responses = []
        for _ in range(5):
            response, _ = guardrails.get_fallback_response(
                state=ConversationState.GREETING,
                call_id=call_id,
                error_count=0
            )
            responses.append(response)
        
        # Should have variety (at least 2 different responses in 5 calls)
        unique_responses = set(responses)
        assert len(unique_responses) >= 2
    
    def test_max_errors_triggers_graceful_goodbye(self):
        """Test that max errors causes graceful goodbye"""
        config = LLMGuardrailsConfig(max_llm_errors_before_goodbye=2)
        guardrails = LLMGuardrails(config)
        
        response, should_end = guardrails.get_fallback_response(
            state=ConversationState.QUALIFICATION,
            call_id="test-call-4",
            error_count=2  # At max
        )
        
        assert should_end is True
        # Goodbye should still sound human
        assert "error" not in response.lower()
        assert "system" not in response.lower()
    
    def test_goodbye_state_fallback(self):
        """Test fallback for GOODBYE state is brief"""
        guardrails = LLMGuardrails()
        response, should_end = guardrails.get_fallback_response(
            state=ConversationState.GOODBYE,
            call_id="test-call-5",
            error_count=0
        )
        
        assert response is not None
        assert "thank" in response.lower() or "care" in response.lower() or "day" in response.lower()


class TestResponseTruncation:
    """Test response truncation for voice brevity"""
    
    def test_truncate_long_response(self):
        """Test truncating a response with too many sentences"""
        guardrails = LLMGuardrails()
        
        long_response = "This is sentence one. This is sentence two. This is sentence three. This is sentence four. This is sentence five."
        
        truncated = guardrails.truncate_response(long_response, max_sentences=2)
        
        # Should only have 2 sentences
        sentences = [s.strip() for s in truncated.split('.') if s.strip()]
        assert len(sentences) <= 2
    
    def test_short_response_unchanged(self):
        """Test that short responses are not modified"""
        guardrails = LLMGuardrails()
        
        short_response = "Great! I'll note that down."
        
        truncated = guardrails.truncate_response(short_response, max_sentences=3)
        
        assert truncated == short_response
    
    def test_empty_response_handled(self):
        """Test empty response handling"""
        guardrails = LLMGuardrails()
        
        result = guardrails.truncate_response("", max_sentences=2)
        
        assert result == ""


class TestResponseValidation:
    """Test response validation against rules"""
    
    def test_valid_response_passes(self):
        """Test that valid responses pass validation"""
        guardrails = LLMGuardrails()
        rules = ConversationRule(
            forbidden_phrases=["discount", "free"]
        )
        
        is_valid, reason = guardrails.validate_response(
            "Great, your appointment is confirmed for tomorrow at 2 PM.",
            rules
        )
        
        assert is_valid is True
        assert reason is None
    
    def test_forbidden_phrase_detected(self):
        """Test that forbidden phrases are detected"""
        guardrails = LLMGuardrails()
        rules = ConversationRule(
            forbidden_phrases=["discount", "free"]
        )
        
        is_valid, reason = guardrails.validate_response(
            "We can offer you a discount on your next visit!",
            rules
        )
        
        assert is_valid is False
        assert "forbidden" in reason.lower()
    
    def test_empty_response_invalid(self):
        """Test that empty responses are invalid"""
        guardrails = LLMGuardrails()
        
        is_valid, reason = guardrails.validate_response("", None)
        
        assert is_valid is False
        assert "empty" in reason.lower()
    
    def test_no_rules_all_valid(self):
        """Test that responses are valid when no rules provided"""
        guardrails = LLMGuardrails()
        
        is_valid, reason = guardrails.validate_response(
            "This is any response text.",
            None
        )
        
        assert is_valid is True


class TestResponseCleaning:
    """Test response cleaning of LLM artifacts"""
    
    def test_clean_filler_words(self):
        """Test removal of common filler starts"""
        guardrails = LLMGuardrails()
        
        test_cases = [
            ("Well, I can help with that.", "I can help with that."),
            ("So, let me check on that.", "let me check on that."),
            ("Actually, that works.", "that works."),
            ("Sure! I'll do that.", "I'll do that."),
        ]
        
        for input_text, expected in test_cases:
            cleaned = guardrails.clean_response(input_text)
            assert cleaned == expected, f"Failed for input: {input_text}"
    
    def test_clean_whitespace(self):
        """Test cleanup of excessive whitespace"""
        guardrails = LLMGuardrails()
        
        messy = "This   has    too   many   spaces."
        cleaned = guardrails.clean_response(messy)
        
        assert "  " not in cleaned


class TestGuardrailsConfig:
    """Test guardrails configuration"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = LLMGuardrailsConfig()
        
        assert config.max_response_tokens == 150
        assert config.max_response_time_seconds == 10.0
        assert config.max_llm_errors_before_goodbye == 2
        assert config.max_sentences == 3
    
    def test_custom_config(self):
        """Test custom configuration"""
        config = LLMGuardrailsConfig(
            max_response_time_seconds=5.0,
            max_llm_errors_before_goodbye=3
        )
        
        assert config.max_response_time_seconds == 5.0
        assert config.max_llm_errors_before_goodbye == 3
    
    def test_singleton_pattern(self):
        """Test get_guardrails returns singleton"""
        guardrails1 = get_guardrails()
        guardrails2 = get_guardrails()
        
        assert guardrails1 is guardrails2


class TestCallTracking:
    """Test call-specific tracking"""
    
    def test_reset_call_tracking(self):
        """Test resetting tracking for a call"""
        guardrails = LLMGuardrails()
        call_id = "test-call-reset"
        
        # Generate some fallbacks to create tracking data
        guardrails.get_fallback_response(ConversationState.GREETING, call_id, 0)
        guardrails.get_fallback_response(ConversationState.GREETING, call_id, 0)
        
        # Reset
        guardrails.reset_call_tracking(call_id)
        
        # Verify tracking was reset (first fallback should be returned again)
        response1, _ = guardrails.get_fallback_response(ConversationState.GREETING, call_id, 0)
        guardrails.reset_call_tracking(call_id)
        response2, _ = guardrails.get_fallback_response(ConversationState.GREETING, call_id, 0)
        
        assert response1 == response2  # Same first fallback after reset
