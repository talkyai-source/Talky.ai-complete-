"""
Unit tests for Prompt Manager
Tests template rendering and prompt generation
"""
import pytest
from app.domain.services.prompt_manager import PromptManager, PromptTemplate
from app.domain.models.conversation_state import ConversationState
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
            do_not_say_rules=["No medical advice", "No pricing discussions"],
            max_follow_up_questions=2
        ),
        tone="polite, professional, conversational",
        max_conversation_turns=10,
        response_max_sentences=2
    )


@pytest.fixture
def prompt_manager():
    """Create prompt manager instance"""
    return PromptManager()


class TestPromptTemplate:
    """Test PromptTemplate model"""
    
    def test_template_creation(self):
        """Test creating a prompt template"""
        template = PromptTemplate(
            name="test_template",
            template="Hello {{ name }}!",
            variables=["name"]
        )
        assert template.name == "test_template"
        assert "{{ name }}" in template.template
    
    def test_template_rendering(self):
        """Test rendering a template with variables"""
        template = PromptTemplate(
            name="test_template",
            template="Hello {{ name }}, you are {{ age }} years old.",
            variables=["name", "age"]
        )
        rendered = template.render(name="John", age=30)
        assert rendered == "Hello John, you are 30 years old."
    
    def test_template_with_loop(self):
        """Test template with Jinja2 loop"""
        template = PromptTemplate(
            name="list_template",
            template="Items:\n{% for item in items %}- {{ item }}\n{% endfor %}",
            variables=["items"]
        )
        rendered = template.render(items=["apple", "banana", "orange"])
        assert "- apple" in rendered
        assert "- banana" in rendered
        assert "- orange" in rendered


class TestPromptManager:
    """Test PromptManager functionality"""
    
    def test_manager_initialization(self, prompt_manager):
        """Test prompt manager initializes with default templates"""
        assert len(prompt_manager.templates) > 0
        assert "base_system" in prompt_manager.templates
        assert "greeting_state" in prompt_manager.templates
    
    def test_list_templates(self, prompt_manager):
        """Test listing all templates"""
        templates = prompt_manager.list_templates()
        assert isinstance(templates, list)
        assert "base_system" in templates
        assert "greeting_state" in templates
        assert "closing_state" in templates
    
    def test_get_template(self, prompt_manager):
        """Test getting a template by name"""
        template = prompt_manager.get_template("base_system")
        assert template is not None
        assert template.name == "base_system"
        assert "{{ agent_name }}" in template.template
    
    def test_get_nonexistent_template(self, prompt_manager):
        """Test getting a template that doesn't exist"""
        template = prompt_manager.get_template("nonexistent")
        assert template is None
    
    def test_add_custom_template(self, prompt_manager):
        """Test adding a custom template"""
        custom_template = PromptTemplate(
            name="custom_test",
            template="Custom: {{ value }}",
            variables=["value"]
        )
        prompt_manager.add_template(custom_template)
        
        retrieved = prompt_manager.get_template("custom_test")
        assert retrieved is not None
        assert retrieved.name == "custom_test"


class TestSystemPromptRendering:
    """Test system prompt rendering"""
    
    def test_render_base_system_prompt(self, prompt_manager, agent_config):
        """Test rendering base system prompt"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING
        )
        
        # Check that agent details are included
        assert "Sarah" in prompt
        assert "Bright Smile Dental" in prompt
        assert "dental clinic" in prompt
        assert "polite, professional, conversational" in prompt
        
        # Check that rules are included
        assert "No medical advice" in prompt
        assert "No pricing discussions" in prompt
    
    def test_render_greeting_state_prompt(self, prompt_manager, agent_config):
        """Test rendering greeting state prompt"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING,
            greeting_context="I'm calling to confirm your appointment."
        )
        
        assert "GREETING" in prompt
        assert "Sarah" in prompt
        assert "Bright Smile Dental" in prompt
    
    def test_render_qualification_state_prompt(self, prompt_manager, agent_config):
        """Test rendering qualification state prompt"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.QUALIFICATION,
            qualification_instruction="Ask about their preferred time"
        )
        
        assert "QUALIFICATION" in prompt
        assert "Ask about their preferred time" in prompt
    
    def test_render_objection_handling_prompt(self, prompt_manager, agent_config):
        """Test rendering objection handling prompt"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.OBJECTION_HANDLING,
            user_concern="not sure about timing",
            objection_count=1,
            max_objections=2
        )
        
        assert "OBJECTION HANDLING" in prompt
        assert "not sure about timing" in prompt
        assert "attempt 1" in prompt or "1 of 2" in prompt
    
    def test_render_closing_state_prompt(self, prompt_manager, agent_config):
        """Test rendering closing state prompt"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.CLOSING,
            confirmation_details="tomorrow at 2 PM"
        )
        
        assert "CLOSING" in prompt
        assert "tomorrow at 2 PM" in prompt
    
    def test_render_transfer_state_prompt(self, prompt_manager, agent_config):
        """Test rendering transfer state prompt"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.TRANSFER
        )
        
        assert "TRANSFER" in prompt
        assert "human" in prompt.lower() or "person" in prompt.lower()
    
    def test_render_goodbye_state_prompt(self, prompt_manager, agent_config):
        """Test rendering goodbye state prompt"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GOODBYE,
            reason="declined"
        )
        
        assert "GOODBYE" in prompt


class TestStateInstructionRendering:
    """Test state instruction rendering"""
    
    def test_render_greeting_instruction(self, prompt_manager):
        """Test rendering greeting instruction"""
        instruction = prompt_manager.render_state_instruction(
            state=ConversationState.GREETING,
            agent_name="Sarah",
            company_name="Bright Smile Dental",
            greeting_context="confirming appointment"
        )
        
        assert "Sarah" in instruction
        assert "Bright Smile Dental" in instruction
    
    def test_render_qualification_instruction(self, prompt_manager):
        """Test rendering qualification instruction"""
        instruction = prompt_manager.render_state_instruction(
            state=ConversationState.QUALIFICATION,
            qualification_instruction="Ask about availability"
        )
        
        assert "Ask about availability" in instruction
    
    def test_render_unknown_state_instruction(self, prompt_manager):
        """Test rendering instruction for unknown state"""
        # This should handle gracefully even for states without templates
        instruction = prompt_manager.render_state_instruction(
            state=ConversationState.CLOSING
        )
        
        assert isinstance(instruction, str)
        assert len(instruction) > 0


class TestPromptContent:
    """Test prompt content quality"""
    
    def test_prompts_enforce_brevity(self, prompt_manager, agent_config):
        """Test that prompts enforce brevity"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING
        )
        
        # Should mention sentence limit
        assert "2 sentences" in prompt or "brief" in prompt.lower()
    
    def test_prompts_include_tone(self, prompt_manager, agent_config):
        """Test that prompts include tone guidance"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING
        )
        
        assert agent_config.tone in prompt
    
    def test_prompts_include_rules(self, prompt_manager, agent_config):
        """Test that prompts include do-not-say rules"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING
        )
        
        for rule in agent_config.rules.do_not_say_rules:
            assert rule in prompt
    
    def test_prompts_are_conversational(self, prompt_manager, agent_config):
        """Test that prompts encourage conversational tone"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING
        )
        
        assert "natural" in prompt.lower() or "conversational" in prompt.lower()
        assert "robotic" in prompt.lower()  # Should say "not robotic"


class TestPromptVariableSubstitution:
    """Test variable substitution in prompts"""
    
    def test_all_variables_substituted(self, prompt_manager, agent_config):
        """Test that all template variables are properly substituted"""
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.GREETING,
            greeting_context="test context"
        )
        
        # Should not contain any unsubstituted Jinja2 variables
        assert "{{" not in prompt
        assert "}}" not in prompt
        assert "{%" not in prompt
        assert "%}" not in prompt
    
    def test_context_variables_passed_through(self, prompt_manager, agent_config):
        """Test that context variables are passed through correctly"""
        test_context = "This is a test context value"
        prompt = prompt_manager.render_system_prompt(
            agent_config=agent_config,
            state=ConversationState.QUALIFICATION,
            qualification_instruction=test_context
        )
        
        assert test_context in prompt
