"""
Unit Tests for AssistantAgentService

Day 28: Multi-step workflow orchestration tests
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from app.domain.models.action_plan import (
    ActionPlan,
    ActionStep,
    ActionStepResult,
    ActionPlanStatus,
    AllowedActionType,
    ActionStepCondition
)
from app.services.assistant_agent_service import (
    AssistantAgentService,
    ActionNotAllowedError,
    get_assistant_agent_service
)


class TestActionPlanModel:
    """Tests for ActionPlan domain model"""
    
    def test_create_valid_action_plan(self):
        """Test creating a valid action plan"""
        plan = ActionPlan(
            tenant_id="test-tenant",
            intent="Book meeting and send confirmation",
            context={"lead_id": "abc123"},
            actions=[
                ActionStep(type="book_meeting", parameters={"title": "Demo"}),
                ActionStep(type="send_email", parameters={"template": "confirmation"}, use_result_from=0)
            ]
        )
        
        assert plan.tenant_id == "test-tenant"
        assert plan.intent == "Book meeting and send confirmation"
        assert len(plan.actions) == 2
        assert plan.status == ActionPlanStatus.PENDING
    
    def test_allowlist_validation(self):
        """Test that invalid action types are rejected"""
        with pytest.raises(ValueError):
            # Invalid action type should fail Pydantic enum validation
            ActionStep(type="dangerous_action", parameters={})
    
    def test_result_reference_validation(self):
        """Test that invalid use_result_from references are rejected"""
        with pytest.raises(ValueError, match="references invalid step"):
            ActionPlan(
                tenant_id="test-tenant",
                intent="Test",
                actions=[
                    ActionStep(type="book_meeting", parameters={}),
                    ActionStep(type="send_email", parameters={}, use_result_from=5)  # Invalid reference
                ]
            )
    
    def test_allowed_action_types(self):
        """Verify all allowed action types"""
        allowed = {e.value for e in AllowedActionType}
        
        assert "book_meeting" in allowed
        assert "send_email" in allowed
        assert "send_sms" in allowed
        assert "schedule_reminder" in allowed
        assert "initiate_call" in allowed
        assert "start_campaign" in allowed
        assert "check_availability" in allowed
        assert "update_meeting" in allowed
        assert "cancel_meeting" in allowed
    
    def test_conditional_execution_values(self):
        """Test ActionStepCondition enum values"""
        assert ActionStepCondition.ALWAYS.value == "always"
        assert ActionStepCondition.IF_PREVIOUS_SUCCESS.value == "if_previous_success"
        assert ActionStepCondition.IF_PREVIOUS_FAILED.value == "if_previous_failed"
    
    def test_action_plan_properties(self):
        """Test computed properties on ActionPlan"""
        plan = ActionPlan(
            tenant_id="test-tenant",
            intent="Test",
            actions=[
                ActionStep(type="book_meeting", parameters={}),
                ActionStep(type="send_email", parameters={})
            ],
            step_results=[
                ActionStepResult(step_index=0, action_type="book_meeting", success=True),
                ActionStepResult(step_index=1, action_type="send_email", success=False, error="Failed")
            ]
        )
        
        assert plan.successful_steps == 1
        assert plan.failed_steps == 1
        assert plan.skipped_steps == 0


class TestAssistantAgentService:
    """Tests for AssistantAgentService"""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client"""
        mock = MagicMock()
        mock.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "plan-123"}]
        )
        mock.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        return mock
    
    @pytest.fixture
    def service(self, mock_supabase):
        """Create service instance with mock"""
        return AssistantAgentService(mock_supabase)
    
    @pytest.mark.asyncio
    async def test_create_plan_valid(self, service):
        """Test creating a valid action plan"""
        plan = await service.create_plan(
            tenant_id="test-tenant",
            intent="Book meeting and notify",
            context={"lead_id": "abc123"},
            actions=[
                {"type": "book_meeting", "title": "Demo", "time": "2026-01-13T15:00:00"},
                {"type": "send_email", "template": "confirmation", "use_result_from": 0}
            ]
        )
        
        assert plan.id == "plan-123"
        assert plan.tenant_id == "test-tenant"
        assert len(plan.actions) == 2
        assert plan.actions[0].type == "book_meeting"
        assert plan.actions[1].use_result_from == 0
    
    @pytest.mark.asyncio
    async def test_create_plan_invalid_action_type(self, service):
        """Test that invalid action types raise error"""
        with pytest.raises(ActionNotAllowedError):
            await service.create_plan(
                tenant_id="test-tenant",
                intent="Test",
                context={},
                actions=[
                    {"type": "hack_system"}  # Not in allowlist
                ]
            )
    
    @pytest.mark.asyncio
    async def test_evaluate_condition_always(self, service):
        """Test ALWAYS condition returns True"""
        plan = ActionPlan(
            tenant_id="test",
            intent="Test",
            actions=[]
        )
        
        result = service._evaluate_condition(ActionStepCondition.ALWAYS, plan, 0)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_evaluate_condition_if_previous_success(self, service):
        """Test IF_PREVIOUS_SUCCESS condition"""
        plan = ActionPlan(
            tenant_id="test",
            intent="Test",
            actions=[
                ActionStep(type="book_meeting", parameters={}),
                ActionStep(type="send_email", parameters={})
            ],
            step_results=[
                ActionStepResult(step_index=0, action_type="book_meeting", success=True)
            ]
        )
        
        result = service._evaluate_condition(ActionStepCondition.IF_PREVIOUS_SUCCESS, plan, 1)
        assert result is True
        
        # Change previous result to failure
        plan.step_results[0].success = False
        result = service._evaluate_condition(ActionStepCondition.IF_PREVIOUS_SUCCESS, plan, 1)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_apply_offset_hours(self, service):
        """Test applying hour offsets"""
        base = datetime(2026, 1, 13, 15, 0, 0)
        
        result = service._apply_offset(base, "-1h")
        assert result == datetime(2026, 1, 13, 14, 0, 0)
        
        result = service._apply_offset(base, "+2h")
        assert result == datetime(2026, 1, 13, 17, 0, 0)
    
    @pytest.mark.asyncio
    async def test_apply_offset_minutes(self, service):
        """Test applying minute offsets"""
        base = datetime(2026, 1, 13, 15, 0, 0)
        
        result = service._apply_offset(base, "-30m")
        assert result == datetime(2026, 1, 13, 14, 30, 0)
        
        result = service._apply_offset(base, "+10m")
        assert result == datetime(2026, 1, 13, 15, 10, 0)
    
    @pytest.mark.asyncio
    async def test_apply_offset_days(self, service):
        """Test applying day offsets"""
        base = datetime(2026, 1, 13, 15, 0, 0)
        
        result = service._apply_offset(base, "-1d")
        assert result == datetime(2026, 1, 12, 15, 0, 0)


class TestToolIntegration:
    """Tests for tool functions"""
    
    @pytest.mark.asyncio
    async def test_schedule_reminder_import(self):
        """Test schedule_reminder can be imported"""
        from app.infrastructure.assistant.tools import schedule_reminder
        assert callable(schedule_reminder)
    
    @pytest.mark.asyncio
    async def test_execute_action_plan_import(self):
        """Test execute_action_plan can be imported"""
        from app.infrastructure.assistant.tools import execute_action_plan
        assert callable(execute_action_plan)
    
    @pytest.mark.asyncio
    async def test_all_tools_includes_new_tools(self):
        """Test ALL_TOOLS includes new Day 28 tools"""
        from app.infrastructure.assistant.tools import ALL_TOOLS
        
        assert "schedule_reminder" in ALL_TOOLS
        assert "execute_action_plan" in ALL_TOOLS
    
    @pytest.mark.asyncio
    async def test_tool_count(self):
        """Verify expected number of tools"""
        from app.infrastructure.assistant.tools import ALL_TOOLS, QUERY_TOOLS, ACTION_TOOLS
        
        # 6 query tools + 10 action tools = 16 total
        assert len(QUERY_TOOLS) == 6
        assert len(ACTION_TOOLS) == 10  # 8 original + 2 new
        assert len(ALL_TOOLS) == 16


class TestSingletonPattern:
    """Tests for singleton service pattern"""
    
    def test_get_assistant_agent_service_singleton(self):
        """Test that singleton returns same instance"""
        mock_supabase = MagicMock()
        
        # Reset singleton
        import app.services.assistant_agent_service as module
        module._assistant_agent_service = None
        
        service1 = get_assistant_agent_service(mock_supabase)
        service2 = get_assistant_agent_service(mock_supabase)
        
        assert service1 is service2
