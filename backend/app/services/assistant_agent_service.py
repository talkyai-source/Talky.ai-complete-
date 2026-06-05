"""
AssistantAgentService - Multi-Step Workflow Orchestrator

Day 28: Assistant Agent orchestrates multi-step workflows with safety guardrails.

This service transforms user intent + context into executable action plans,
enabling complex workflows like:
- Book meeting → Send confirmation email → Schedule reminder

Key Features:
- Hard allowlist enforcement (no free-form actions)
- Result chaining between steps
- Conditional execution
- Atomic action logging
- Full audit trail
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.core.postgres_adapter import Client

from app.domain.models.action_plan import (
    ActionPlan,
    ActionStep,
    ActionPlanStatus,
    ActionStepResult,
    AllowedActionType,
    ActionStepCondition
)

from app.services.assistant_plan_steps import (
    evaluate_condition,
    apply_offset,
    execute_action,
    schedule_reminder,
)

logger = logging.getLogger(__name__)


class ActionNotAllowedError(Exception):
    """Raised when an action type is not in the allowlist."""
    def __init__(self, action_type: str, allowed_types: set):
        self.action_type = action_type
        self.allowed_types = allowed_types
        self.message = f"Action '{action_type}' is not allowed. Permitted actions: {allowed_types}"
        super().__init__(self.message)


class AssistantAgentService:
    """
    Orchestrates multi-step action workflows with safety guardrails.
    
    This service sits above the individual tools (book_meeting, send_email, etc.)
    and coordinates their execution as part of larger workflows.
    
    Integration Points:
    - Triggerable from: Assistant chat, Voice agent outcome, REST API
    - Uses existing tools from infrastructure/assistant/tools.py
    - Logs all plans to action_plans table for audit
    
    Example Usage:
        service = get_assistant_agent_service(db_client)
        
        plan = await service.create_plan(
            tenant_id="...",
            intent="Book meeting and notify",
            context={"lead_id": "abc123"},
            actions=[
                {"type": "book_meeting", "title": "Demo", "time": "2026-01-13T15:00:00"},
                {"type": "send_email", "template": "confirmation", "use_result_from": 0}
            ]
        )
        
        result = await service.execute_plan(plan)
        print(result.status)  # "completed"
    """
    
    ALLOWED_ACTIONS = {e.value for e in AllowedActionType}
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
    
    async def create_plan(
        self,
        tenant_id: str,
        intent: str,
        context: Dict[str, Any],
        actions: List[Dict[str, Any]],
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> ActionPlan:
        """
        Create and validate an action plan.
        
        Args:
            tenant_id: Tenant ID
            intent: Natural language description of the workflow
            context: Context data (lead_id, campaign_id, etc.)
            actions: List of action dictionaries
            conversation_id: Optional conversation ID for chat context
            user_id: Optional user ID
            
        Returns:
            Validated ActionPlan object with database ID
            
        Raises:
            ActionNotAllowedError: If any action type is not in allowlist
            ValueError: If action references are invalid
        """
        # Validate and convert actions
        validated_actions = []
        for i, action in enumerate(actions):
            action_type = action.get("type")
            
            # Check allowlist
            if action_type not in self.ALLOWED_ACTIONS:
                raise ActionNotAllowedError(action_type, self.ALLOWED_ACTIONS)
            
            # Extract parameters (everything except type, use_result_from, condition)
            parameters = {
                k: v for k, v in action.items() 
                if k not in ("type", "use_result_from", "condition")
            }
            
            # Get condition
            condition_str = action.get("condition", "always")
            try:
                condition = ActionStepCondition(condition_str)
            except ValueError:
                condition = ActionStepCondition.ALWAYS
            
            validated_actions.append(ActionStep(
                type=action_type,
                parameters=parameters,
                use_result_from=action.get("use_result_from"),
                condition=condition
            ))
        
        # Create plan object (validates internally)
        plan = ActionPlan(
            tenant_id=tenant_id,
            intent=intent,
            context=context,
            actions=validated_actions,
            conversation_id=conversation_id,
            user_id=user_id,
            created_at=datetime.utcnow()
        )
        
        # Persist to database
        plan_data = {
            "tenant_id": tenant_id,
            "intent": intent,
            "context": context,
            "actions": [a.model_dump() for a in validated_actions],
            "status": "pending",
            "current_step": 0,
            "step_results": [],
            "conversation_id": conversation_id,
            "user_id": user_id
        }
        
        try:
            response = self.db_client.table("action_plans").insert(plan_data).execute()
            if response.data:
                plan.id = response.data[0]["id"]
                logger.info(f"Created action plan {plan.id} with {len(validated_actions)} steps")
        except Exception as e:
            logger.error(f"Failed to persist action plan: {e}")
            # Continue without persistence for now
        
        return plan
    
    async def execute_plan(self, plan: ActionPlan) -> ActionPlan:
        """
        Execute all steps in an action plan sequentially.
        
        Args:
            plan: ActionPlan to execute
            
        Returns:
            Updated ActionPlan with results and final status
        """
        plan.status = ActionPlanStatus.RUNNING
        plan.started_at = datetime.utcnow()
        
        # Update status in DB
        self._update_plan_in_db(plan)
        
        logger.info(f"Executing action plan {plan.id} with {len(plan.actions)} steps")
        
        for i, action in enumerate(plan.actions):
            plan.current_step = i
            step_start = datetime.utcnow()
            
            try:
                # Check condition
                should_execute = evaluate_condition(action.condition, plan, i)
                
                if not should_execute:
                    result = ActionStepResult(
                        step_index=i,
                        action_type=action.type,
                        success=True,
                        skipped=True,
                        skip_reason=f"Condition '{action.condition}' not met",
                        executed_at=datetime.utcnow()
                    )
                    plan.step_results.append(result)
                    logger.info(f"Step {i} ({action.type}) skipped: condition not met")
                    continue
                
                # Get chained result if needed
                params = action.parameters.copy()
                if action.use_result_from is not None:
                    prev_result = plan.get_step_result(action.use_result_from)
                    if prev_result and prev_result.success:
                        params["_chained_result"] = prev_result.result
                
                # Execute action
                action_result = await execute_action(
                    self.db_client,
                    action_type=action.type,
                    tenant_id=plan.tenant_id,
                    params=params,
                    context=plan.context,
                    conversation_id=plan.conversation_id
                )
                
                duration_ms = int((datetime.utcnow() - step_start).total_seconds() * 1000)
                
                result = ActionStepResult(
                    step_index=i,
                    action_type=action.type,
                    success=action_result.get("success", True),
                    result=action_result,
                    error=action_result.get("error"),
                    executed_at=datetime.utcnow(),
                    duration_ms=duration_ms
                )
                plan.step_results.append(result)
                
                if result.success:
                    logger.info(f"Step {i} ({action.type}) completed successfully")
                else:
                    logger.warning(f"Step {i} ({action.type}) failed: {result.error}")
                    
            except Exception as e:
                logger.error(f"Error executing step {i} ({action.type}): {e}")
                
                result = ActionStepResult(
                    step_index=i,
                    action_type=action.type,
                    success=False,
                    error=str(e),
                    executed_at=datetime.utcnow(),
                    duration_ms=int((datetime.utcnow() - step_start).total_seconds() * 1000)
                )
                plan.step_results.append(result)
                plan.error = str(e)
                # Continue to next step rather than stopping
        
        # Determine final status
        if plan.successful_steps == len(plan.actions):
            plan.status = ActionPlanStatus.COMPLETED
        elif plan.successful_steps > 0:
            plan.status = ActionPlanStatus.PARTIALLY_COMPLETED
        elif plan.failed_steps == len(plan.actions):
            plan.status = ActionPlanStatus.FAILED
        else:
            plan.status = ActionPlanStatus.PARTIALLY_COMPLETED
        
        plan.completed_at = datetime.utcnow()
        plan.current_step = len(plan.actions)
        
        # Final DB update
        self._update_plan_in_db(plan)
        
        logger.info(
            f"Action plan {plan.id} completed: {plan.status} "
            f"({plan.successful_steps}/{len(plan.actions)} steps successful)"
        )
        
        return plan
    
    # Shim methods kept for backward-compatibility with tests that call them
    # directly on the service instance.  They delegate to the module functions.
    def _evaluate_condition(
        self,
        condition: "ActionStepCondition",
        plan: ActionPlan,
        current_index: int,
    ) -> bool:
        return evaluate_condition(condition, plan, current_index)

    def _apply_offset(self, base_time: datetime, offset: str) -> datetime:
        return apply_offset(base_time, offset)

    def _update_plan_in_db(self, plan: ActionPlan) -> None:
        """Update plan status in database."""
        if not plan.id:
            return
        
        try:
            update_data = {
                "status": plan.status if isinstance(plan.status, str) else plan.status.value,
                "current_step": plan.current_step,
                "step_results": [r.model_dump() for r in plan.step_results],
                "error": plan.error
            }
            
            if plan.started_at:
                update_data["started_at"] = plan.started_at.isoformat()
            if plan.completed_at:
                update_data["completed_at"] = plan.completed_at.isoformat()
            
            self.db_client.table("action_plans").update(
                update_data
            ).eq("id", plan.id).execute()
            
        except Exception as e:
            logger.error(f"Failed to update action plan in DB: {e}")
    
    async def get_plan(self, plan_id: str, tenant_id: str) -> Optional[ActionPlan]:
        """Get an action plan by ID."""
        try:
            response = self.db_client.table("action_plans").select(
                "*"
            ).eq("id", plan_id).eq("tenant_id", tenant_id).single().execute()
            
            if not response.data:
                return None
            
            data = response.data
            return ActionPlan(
                id=data["id"],
                tenant_id=data["tenant_id"],
                conversation_id=data.get("conversation_id"),
                user_id=data.get("user_id"),
                intent=data["intent"],
                context=data.get("context", {}),
                actions=[ActionStep(**a) for a in data.get("actions", [])],
                status=data.get("status", "pending"),
                current_step=data.get("current_step", 0),
                step_results=[ActionStepResult(**r) for r in data.get("step_results", [])],
                error=data.get("error"),
                created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")) if data.get("created_at") else None,
                started_at=datetime.fromisoformat(data["started_at"].replace("Z", "+00:00")) if data.get("started_at") else None,
                completed_at=datetime.fromisoformat(data["completed_at"].replace("Z", "+00:00")) if data.get("completed_at") else None
            )
        except Exception as e:
            logger.error(f"Error fetching action plan: {e}")
            return None
    
    async def list_plans(
        self,
        tenant_id: str,
        status: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """List action plans for a tenant."""
        try:
            query = self.db_client.table("action_plans").select(
                "id, intent, status, current_step, created_at, completed_at"
            ).eq("tenant_id", tenant_id)
            
            if status:
                query = query.eq("status", status)
            
            response = query.order("created_at", desc=True).limit(limit).execute()
            
            return response.data or []
        except Exception as e:
            logger.error(f"Error listing action plans: {e}")
            return []


# Singleton instance helper
_assistant_agent_service: Optional[AssistantAgentService] = None


def get_assistant_agent_service(db_client: Client) -> AssistantAgentService:
    """Get or create AssistantAgentService instance."""
    global _assistant_agent_service
    if _assistant_agent_service is None:
        _assistant_agent_service = AssistantAgentService(db_client)
    return _assistant_agent_service
