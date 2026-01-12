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
from datetime import datetime, timedelta
from supabase import Client

from app.domain.models.action_plan import (
    ActionPlan,
    ActionStep,
    ActionPlanStatus,
    ActionStepResult,
    AllowedActionType,
    ActionStepCondition
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
        service = get_assistant_agent_service(supabase)
        
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
    
    def __init__(self, supabase: Client):
        self.supabase = supabase
    
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
            response = self.supabase.table("action_plans").insert(plan_data).execute()
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
                should_execute = self._evaluate_condition(action.condition, plan, i)
                
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
                action_result = await self._execute_action(
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
    
    def _evaluate_condition(
        self, 
        condition: ActionStepCondition, 
        plan: ActionPlan, 
        current_index: int
    ) -> bool:
        """Evaluate whether a step should execute based on its condition."""
        if condition == ActionStepCondition.ALWAYS:
            return True
        
        if current_index == 0:
            # First step has no previous result
            return condition == ActionStepCondition.ALWAYS
        
        prev_result = plan.get_step_result(current_index - 1)
        if prev_result is None:
            return True
        
        if condition == ActionStepCondition.IF_PREVIOUS_SUCCESS:
            return prev_result.success and not prev_result.skipped
        
        if condition == ActionStepCondition.IF_PREVIOUS_FAILED:
            return not prev_result.success and not prev_result.skipped
        
        return True
    
    async def _execute_action(
        self,
        action_type: str,
        tenant_id: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a single action by routing to the appropriate tool.
        
        Uses existing tools from app.infrastructure.assistant.tools
        """
        # Import tools here to avoid circular imports
        from app.infrastructure.assistant.tools import (
            book_meeting,
            send_email,
            send_sms,
            initiate_call,
            start_campaign,
            check_availability,
            update_meeting_tool,
            cancel_meeting_tool
        )
        
        # Merge context into params
        merged_params = {**context, **params}
        chained = merged_params.pop("_chained_result", {})
        
        try:
            if action_type == AllowedActionType.BOOK_MEETING.value:
                return await book_meeting(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    title=merged_params.get("title", "Meeting"),
                    start_time=merged_params.get("time") or merged_params.get("start_time", ""),
                    duration_minutes=merged_params.get("duration_minutes", 30),
                    attendees=merged_params.get("attendees", []),
                    lead_id=merged_params.get("lead_id"),
                    description=merged_params.get("description"),
                    add_video_conference=merged_params.get("add_video_conference", True),
                    conversation_id=conversation_id
                )
            
            elif action_type == AllowedActionType.SEND_EMAIL.value:
                # Use chained result for meeting info if available
                template_context = merged_params.get("template_context", {})
                
                if chained.get("join_link"):
                    template_context["join_link"] = chained["join_link"]
                if chained.get("meeting_id"):
                    template_context["meeting_id"] = chained["meeting_id"]
                if chained.get("title"):
                    template_context["title"] = chained["title"]
                if chained.get("start_time"):
                    template_context["start_time"] = chained["start_time"]
                
                return await send_email(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    to=merged_params.get("to", []),
                    subject=merged_params.get("subject", ""),
                    body=merged_params.get("body", ""),
                    template_name=merged_params.get("template"),
                    template_context=template_context,
                    conversation_id=conversation_id
                )
            
            elif action_type == AllowedActionType.SEND_SMS.value:
                return await send_sms(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    to=merged_params.get("to", []),
                    message=merged_params.get("message", ""),
                    conversation_id=conversation_id
                )
            
            elif action_type == AllowedActionType.SCHEDULE_REMINDER.value:
                return await self._schedule_reminder(
                    tenant_id=tenant_id,
                    params=merged_params,
                    chained_result=chained,
                    conversation_id=conversation_id
                )
            
            elif action_type == AllowedActionType.INITIATE_CALL.value:
                return await initiate_call(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    phone_number=merged_params.get("phone_number", ""),
                    campaign_id=merged_params.get("campaign_id"),
                    conversation_id=conversation_id
                )
            
            elif action_type == AllowedActionType.START_CAMPAIGN.value:
                return await start_campaign(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    campaign_id=merged_params.get("campaign_id", ""),
                    conversation_id=conversation_id
                )
            
            elif action_type == AllowedActionType.CHECK_AVAILABILITY.value:
                return await check_availability(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    date_str=merged_params.get("date", ""),
                    duration_minutes=merged_params.get("duration_minutes", 30)
                )
            
            elif action_type == AllowedActionType.UPDATE_MEETING.value:
                return await update_meeting_tool(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    meeting_id=merged_params.get("meeting_id", ""),
                    new_time=merged_params.get("new_time"),
                    new_title=merged_params.get("new_title"),
                    conversation_id=conversation_id
                )
            
            elif action_type == AllowedActionType.CANCEL_MEETING.value:
                return await cancel_meeting_tool(
                    tenant_id=tenant_id,
                    supabase=self.supabase,
                    meeting_id=merged_params.get("meeting_id", ""),
                    reason=merged_params.get("reason"),
                    conversation_id=conversation_id
                )
            
            else:
                return {"success": False, "error": f"Unknown action type: {action_type}"}
                
        except Exception as e:
            logger.error(f"Action execution error ({action_type}): {e}")
            return {"success": False, "error": str(e)}
    
    async def _schedule_reminder(
        self,
        tenant_id: str,
        params: Dict[str, Any],
        chained_result: Dict[str, Any],
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Schedule a reminder based on offset from meeting or absolute time.
        
        Args:
            tenant_id: Tenant ID
            params: Reminder parameters (offset, scheduled_at, message, etc.)
            chained_result: Result from previous action (may contain meeting info)
            conversation_id: Optional conversation ID
        """
        # Calculate scheduled_at
        offset = params.get("offset", "")
        
        if offset and chained_result.get("start_time"):
            # Relative to meeting start time
            start_time_str = chained_result["start_time"]
            if isinstance(start_time_str, str):
                base_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            else:
                base_time = start_time_str
            scheduled_at = self._apply_offset(base_time, offset)
        elif params.get("scheduled_at"):
            # Absolute time
            scheduled_at_str = params["scheduled_at"]
            if isinstance(scheduled_at_str, str):
                scheduled_at = datetime.fromisoformat(scheduled_at_str.replace("Z", "+00:00"))
            else:
                scheduled_at = scheduled_at_str
        else:
            # Default to 1 hour from now
            scheduled_at = datetime.utcnow() + timedelta(hours=1)
        
        # Don't create reminders in the past
        if scheduled_at <= datetime.utcnow():
            return {
                "success": False,
                "error": "Cannot schedule reminder in the past"
            }
        
        # Build reminder data
        reminder_data = {
            "tenant_id": tenant_id,
            "meeting_id": chained_result.get("meeting_id") or params.get("meeting_id"),
            "lead_id": params.get("lead_id"),
            "type": params.get("reminder_type", "sms"),
            "scheduled_at": scheduled_at.isoformat(),
            "status": "pending",
            "content": {
                "message": params.get("message", "Reminder for your upcoming meeting"),
                "template": params.get("template", "meeting_reminder_1h"),
                "title": chained_result.get("title") or params.get("title"),
                "join_link": chained_result.get("join_link")
            }
        }
        
        try:
            response = self.supabase.table("reminders").insert(reminder_data).execute()
            
            if response.data:
                return {
                    "success": True,
                    "reminder_id": response.data[0]["id"],
                    "scheduled_at": scheduled_at.isoformat(),
                    "message": f"Reminder scheduled for {scheduled_at.strftime('%Y-%m-%d %H:%M')}"
                }
            
            return {"success": False, "error": "Failed to create reminder"}
            
        except Exception as e:
            logger.error(f"Error scheduling reminder: {e}")
            return {"success": False, "error": str(e)}
    
    def _apply_offset(self, base_time: datetime, offset: str) -> datetime:
        """
        Apply time offset like '-1h', '+30m', '-10m' to a base time.
        
        Args:
            base_time: Base datetime
            offset: Offset string like '-1h', '+30m', '-10m'
            
        Returns:
            Adjusted datetime
        """
        if not offset:
            return base_time
        
        # Parse sign
        sign = -1 if offset.startswith("-") else 1
        value = offset.lstrip("+-")
        
        # Parse unit and amount
        if value.endswith("h"):
            delta = timedelta(hours=int(value[:-1]) * sign)
        elif value.endswith("m"):
            delta = timedelta(minutes=int(value[:-1]) * sign)
        elif value.endswith("d"):
            delta = timedelta(days=int(value[:-1]) * sign)
        else:
            # Default to minutes
            delta = timedelta(minutes=int(value) * sign)
        
        return base_time + delta
    
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
            
            self.supabase.table("action_plans").update(
                update_data
            ).eq("id", plan.id).execute()
            
        except Exception as e:
            logger.error(f"Failed to update action plan in DB: {e}")
    
    async def get_plan(self, plan_id: str, tenant_id: str) -> Optional[ActionPlan]:
        """Get an action plan by ID."""
        try:
            response = self.supabase.table("action_plans").select(
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
            query = self.supabase.table("action_plans").select(
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


def get_assistant_agent_service(supabase: Client) -> AssistantAgentService:
    """Get or create AssistantAgentService instance."""
    global _assistant_agent_service
    if _assistant_agent_service is None:
        _assistant_agent_service = AssistantAgentService(supabase)
    return _assistant_agent_service
