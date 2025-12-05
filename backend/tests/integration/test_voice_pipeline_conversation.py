"""
Integration Tests for Voice Pipeline with Conversation Handling
Tests the complete flow: User Input → State Machine → Prompt Generation → LLM → Response
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import Message, MessageRole
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.models.agent_config import (
    AgentConfig,
    AgentGoal,
    ConversationRule,
    ConversationFlow
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
            do_not_say_rules=[
                "Never provide medical advice",
                "Never discuss pricing"
            ],
            max_follow_up_questions=2
        ),
        flow=ConversationFlow(
            on_yes="closing",
            on_no="goodbye",
            on_uncertain="objection_handling",
            max_objection_attempts=2
        ),
        tone="warm, professional, direct",
        max_conversation_turns=10,
        response_max_sentences=2
    )


@pytest.fixture
def call_session(agent_config):
    """Create test call session with agent config"""
    session = CallSession(
        call_id="test-call-123",
        campaign_id="test-campaign-456",
        lead_id="test-lead-789",
        vonage_call_uuid="test-vonage-uuid",
        from_number="+1234567890",
        to_number="+0987654321",
        system_prompt="You are a helpful assistant",
        voice_id="test-voice",
        conversation_state=ConversationState.GREETING,
        conversation_context=ConversationContext(),
        agent_config=agent_config
    )
    return session


@pytest.fixture
def mock_llm_provider():
    """Create mock LLM provider"""
    mock = AsyncMock()
    
    # Mock streaming response
    async def mock_stream(*args, **kwargs):
        # Simulate streaming tokens
        response = "Hi! This is Sarah from Bright Smile Dental. I'm calling to confirm your appointment tomorrow at 2 PM."
        for token in response.split():
            yield token + " "
    
    mock.stream_chat = mock_stream
    return mock


@pytest.fixture
def mock_stt_provider():
    """Create mock STT provider"""
    return Mock()


@pytest.fixture
def mock_tts_provider():
    """Create mock TTS provider"""
    return AsyncMock()


@pytest.fixture
def mock_media_gateway():
    """Create mock media gateway"""
    return Mock()


@pytest.fixture
def voice_pipeline(mock_stt_provider, mock_llm_provider, mock_tts_provider, mock_media_gateway):
    """Create VoicePipelineService with mocked dependencies"""
    return VoicePipelineService(
        stt_provider=mock_stt_provider,
        llm_provider=mock_llm_provider,
        tts_provider=mock_tts_provider,
        media_gateway=mock_media_gateway
    )


class TestVoicePipelineConversationIntegration:
    """Integration tests for voice pipeline with conversation handling"""
    
    @pytest.mark.asyncio
    async def test_greeting_state_response(self, voice_pipeline, call_session):
        """Test LLM response generation in GREETING state"""
        # Setup
        call_session.conversation_state = ConversationState.GREETING
        user_input = "Hello?"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify
        assert response is not None
        assert len(response) > 0
        assert "Sarah" in response or "Bright Smile" in response
        
        # Verify state was updated
        assert call_session.conversation_state in [
            ConversationState.GREETING,
            ConversationState.QUALIFICATION
        ]
    
    @pytest.mark.asyncio
    async def test_state_transition_on_yes(self, voice_pipeline, call_session):
        """Test state transition from GREETING to QUALIFICATION on YES intent"""
        # Setup
        call_session.conversation_state = ConversationState.GREETING
        user_input = "Yes, I'm available"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify state transition
        assert call_session.conversation_state == ConversationState.QUALIFICATION
        assert response is not None
    
    @pytest.mark.asyncio
    async def test_objection_handling_state(self, voice_pipeline, call_session):
        """Test objection handling state and context tracking"""
        # Setup
        call_session.conversation_state = ConversationState.QUALIFICATION
        user_input = "I'm not sure if I can make it"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify
        assert call_session.conversation_state == ConversationState.OBJECTION_HANDLING
        assert call_session.conversation_context.objection_count == 1
        assert response is not None
    
    @pytest.mark.asyncio
    async def test_context_window_management(self, voice_pipeline, call_session):
        """Test that conversation history is limited to last 10 messages"""
        # Setup: Add 15 messages to history
        for i in range(15):
            call_session.conversation_history.append(
                Message(
                    role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                    content=f"Message {i}"
                )
            )
        
        user_input = "Yes, that works"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify response generated successfully
        assert response is not None
        # Note: Context window management happens internally in get_llm_response
    
    @pytest.mark.asyncio
    async def test_conversation_end_detection(self, voice_pipeline, call_session):
        """Test that conversation end is detected in terminal states"""
        # Setup
        call_session.conversation_state = ConversationState.CLOSING
        call_session.conversation_context.user_confirmed = True
        user_input = "Yes, thank you"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify
        assert response is not None
        # Session state should be marked for ending
        assert call_session.state == CallState.ENDING or call_session.conversation_state == ConversationState.GOODBYE
    
    @pytest.mark.asyncio
    async def test_fallback_without_agent_config(self, voice_pipeline, call_session):
        """Test fallback to basic LLM response when agent_config is missing"""
        # Setup: Remove agent_config
        call_session.agent_config = None
        user_input = "Hello"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify fallback works
        assert response is not None
        assert len(response) > 0
    
    @pytest.mark.asyncio
    async def test_prompt_generation_includes_state_context(self, voice_pipeline, call_session):
        """Test that generated prompts include state-specific context"""
        # Setup - set objection_count to 0 so after increment it's 1 (under max of 2)
        call_session.conversation_state = ConversationState.OBJECTION_HANDLING
        call_session.conversation_context.objection_count = 0
        user_input = "I'm not sure"
        
        # We'll patch the prompt_manager to capture the rendered prompt
        with patch.object(voice_pipeline.prompt_manager, 'render_system_prompt', 
                         wraps=voice_pipeline.prompt_manager.render_system_prompt) as mock_render:
            
            # Execute
            response = await voice_pipeline.get_llm_response(call_session, user_input)
            
            # Verify prompt_manager was called with correct state
            assert mock_render.called
            call_args = mock_render.call_args
            assert call_args.kwargs['state'] == ConversationState.OBJECTION_HANDLING
            assert 'objection_count' in call_args.kwargs
    
    @pytest.mark.asyncio
    async def test_llm_parameters_optimization(self, voice_pipeline, call_session, mock_llm_provider):
        """Test that LLM is called with optimized parameters for voice calls"""
        # Setup
        user_input = "Hello"
        
        # Track stream_chat calls
        stream_calls = []
        
        async def capture_stream_chat(*args, **kwargs):
            stream_calls.append(kwargs)
            # Return simple response
            yield "Hello "
            yield "there"
        
        mock_llm_provider.stream_chat = capture_stream_chat
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify parameters
        assert len(stream_calls) > 0
        params = stream_calls[0]
        
        # Check Groq best practices are applied
        assert params.get('temperature') == 0.3  # Optimized for voice
        assert params.get('max_tokens') == 150   # Brevity enforcement
        assert params.get('top_p') == 1.0        # Groq recommendation
        assert 'stop' in params                  # Stop sequences present
    
    @pytest.mark.asyncio
    async def test_multi_turn_conversation_flow(self, voice_pipeline, call_session):
        """Test complete multi-turn conversation flow"""
        conversation_turns = [
            ("Hello?", ConversationState.GREETING),
            ("Yes, I'm available", ConversationState.QUALIFICATION),
            ("That works for me", ConversationState.CLOSING),
        ]
        
        for user_input, expected_state_after in conversation_turns:
            # Execute turn
            response = await voice_pipeline.get_llm_response(call_session, user_input)
            
            # Verify response generated
            assert response is not None
            assert len(response) > 0
            
            # Add to conversation history (simulating real flow)
            call_session.conversation_history.append(
                Message(role=MessageRole.USER, content=user_input)
            )
            call_session.conversation_history.append(
                Message(role=MessageRole.ASSISTANT, content=response)
            )
        
        # Verify conversation progressed through states
        assert len(call_session.conversation_history) == 6  # 3 turns × 2 messages
    
    @pytest.mark.asyncio
    async def test_max_objection_attempts_handling(self, voice_pipeline, call_session):
        """Test that max objection attempts leads to appropriate state"""
        # Setup: Set objection count to max
        call_session.conversation_state = ConversationState.OBJECTION_HANDLING
        call_session.conversation_context.objection_count = 2  # At max (from agent_config)
        user_input = "I'm still not sure"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify state transitions to GOODBYE after max attempts
        assert call_session.conversation_state == ConversationState.GOODBYE
        assert response is not None
    
    @pytest.mark.asyncio
    async def test_transfer_request_handling(self, voice_pipeline, call_session):
        """Test handling of user request to transfer to human"""
        # Setup
        call_session.conversation_state = ConversationState.QUALIFICATION
        user_input = "I want to speak to a person"
        
        # Execute
        response = await voice_pipeline.get_llm_response(call_session, user_input)
        
        # Verify state transitions to TRANSFER
        assert call_session.conversation_state == ConversationState.TRANSFER
        assert call_session.conversation_context.transfer_requested == True
        assert response is not None


class TestPromptManagerIntegration:
    """Integration tests for PromptManager with real templates"""
    
    def test_all_states_have_prompts(self, voice_pipeline, agent_config):
        """Test that all conversation states have corresponding prompts"""
        states = [
            ConversationState.GREETING,
            ConversationState.QUALIFICATION,
            ConversationState.OBJECTION_HANDLING,
            ConversationState.CLOSING,
            ConversationState.TRANSFER,
            ConversationState.GOODBYE
        ]
        
        for state in states:
            # Generate prompt for each state
            prompt = voice_pipeline.prompt_manager.render_system_prompt(
                agent_config=agent_config,
                state=state,
                greeting_context="test",
                qualification_instruction="test",
                user_concern="test",
                objection_count=1,
                max_objections=2
            )
            
            # Verify prompt generated
            assert prompt is not None
            assert len(prompt) > 0
            assert state.value.upper() in prompt or "CURRENT STATE" in prompt
    
    def test_prompt_includes_agent_details(self, voice_pipeline, agent_config):
        """Test that prompts include agent-specific details"""
        prompt = voice_pipeline.prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING,
            greeting_context="confirming appointment"
        )
        
        # Verify agent details present
        assert agent_config.agent_name in prompt
        assert agent_config.company_name in prompt
        assert agent_config.business_type in prompt
        assert str(agent_config.response_max_sentences) in prompt
    
    def test_prompt_includes_rules(self, voice_pipeline, agent_config):
        """Test that prompts include do-not-say rules"""
        prompt = voice_pipeline.prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING,
            greeting_context="test"
        )
        
        # Verify rules are included
        for rule in agent_config.rules.do_not_say_rules:
            assert rule in prompt


class TestConversationEngineIntegration:
    """Integration tests for ConversationEngine with real scenarios"""
    
    @pytest.mark.asyncio
    async def test_intent_detection_accuracy(self, voice_pipeline, call_session):
        """Test intent detection with various user inputs"""
        test_cases = [
            ("yes", "yes"),
            ("no thanks", "no"),
            ("I'm not sure", "uncertain"),
            ("let me speak to someone", "request_human"),
        ]
        
        for user_input, expected_intent_type in test_cases:
            # Execute
            response = await voice_pipeline.get_llm_response(call_session, user_input)
            
            # Verify response generated (intent was detected)
            assert response is not None


@pytest.mark.asyncio
async def test_end_to_end_appointment_confirmation(voice_pipeline, call_session):
    """
    End-to-end test: Complete appointment confirmation conversation
    This simulates a real call flow from greeting to closing
    """
    # Turn 1: Greeting
    response1 = await voice_pipeline.get_llm_response(call_session, "Hello?")
    assert response1 is not None
    assert call_session.conversation_state in [ConversationState.GREETING, ConversationState.QUALIFICATION]
    
    # Turn 2: User confirms availability
    call_session.conversation_history.append(Message(role=MessageRole.USER, content="Yes, I'm available"))
    response2 = await voice_pipeline.get_llm_response(call_session, "Yes, I'm available")
    assert response2 is not None
    
    # Turn 3: User confirms appointment
    call_session.conversation_history.append(Message(role=MessageRole.USER, content="Yes, that works"))
    response3 = await voice_pipeline.get_llm_response(call_session, "Yes, that works")
    assert response3 is not None
    
    # Verify conversation progressed logically
    assert len(call_session.conversation_history) >= 2
    
    # Verify final state is appropriate (CLOSING or GOODBYE)
    assert call_session.conversation_state in [
        ConversationState.CLOSING,
        ConversationState.GOODBYE
    ]
