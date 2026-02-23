"""
Post-Call Analyzer Service
Runs AFTER call ends to detect actionable intents from saved transcript.
Zero latency impact on call flow.

Day 29: Voice AI Intent Detection & Actions

Architecture:
- Hooks into existing _save_call_data() background task
- Analyzes transcript text that's already being saved
- Checks API availability and user permission before executing
- Stores recommendations when actions can't be executed
"""
import logging
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from app.core.postgres_adapter import Client

from app.domain.models.voice_intent import (
    VoiceActionableIntent,
    ActionReadiness,
    DetectedIntent,
    CallRecommendation
)

logger = logging.getLogger(__name__)


class PostCallAnalyzer:
    """
    Analyzes transcripts after call completion to detect actionable intents.
    
    This runs in the existing _save_call_data() background task,
    after transcript is already saved - zero latency impact on call flow.
    
    Decision Flow:
    1. Analyze transcript for actionable intents (pattern + confidence)
    2. Check if tenant has required connectors (calendar/email)
    3. Check if tenant has enabled auto_actions permission
    4. If ready: Execute via AssistantAgentService
    5. If not ready: Store recommendation for next interaction
    """
    
    # Intent detection patterns with confidence scores
    # Higher confidence = more explicit intent expression
    INTENT_PATTERNS = {
        VoiceActionableIntent.BOOKING_REQUEST: [
            # Explicit booking language
            (r'\b(schedule|book|set up|arrange)\b.*\b(meeting|call|appointment|demo)\b', 0.9),
            # Time-based confirmation
            (r'\b(tomorrow|next week|today|monday|tuesday|wednesday|thursday|friday)\b.*\b(at|around)\s+\d', 0.85),
            # General confirmation in booking context
            (r'\b(sounds good|yes.*that time|confirm|works for me|let\'?s do it)\b', 0.75),
            # Demo/meeting interest
            (r'\b(let\'?s|can we|could we)\b.*\b(meet|talk|call|demo)\b', 0.7),
        ],
        VoiceActionableIntent.FOLLOW_UP_REQUEST: [
            # Explicit follow-up request
            (r'\b(send|email)\b.*\b(information|details|follow.?up|summary)\b', 0.9),
            (r'\b(can you|could you)\b.*\b(email|send)\b', 0.85),
            # Information request
            (r'\b(more info|brochure|pricing|documentation|details)\b', 0.7),
        ],
        VoiceActionableIntent.REMINDER_REQUEST: [
            # Explicit reminder request
            (r'\b(remind|reminder|don\'?t let me forget)\b', 0.9),
            (r'\b(make sure I|alert me|notify me)\b', 0.8),
        ],
        VoiceActionableIntent.CALLBACK_LATER: [
            # Explicit callback request
            (r'\b(call me back|callback|call later|call again)\b', 0.9),
            # Implicit - busy/not good time
            (r'\b(busy now|not a good time|try.*(later|tomorrow|next week))\b', 0.75),
            (r'\b(in a meeting|can\'?t talk now)\b', 0.7),
        ],
    }
    
    # Required connectors for each intent type
    REQUIRED_CONNECTORS = {
        VoiceActionableIntent.BOOKING_REQUEST: "calendar",
        VoiceActionableIntent.FOLLOW_UP_REQUEST: "email",
        VoiceActionableIntent.REMINDER_REQUEST: None,  # No external API needed
        VoiceActionableIntent.CALLBACK_LATER: None,    # Internal scheduling
    }
    
    # Minimum confidence threshold to consider intent valid
    MIN_CONFIDENCE_THRESHOLD = 0.7
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
        self._compiled_patterns = self._compile_patterns()
    
    def _compile_patterns(self) -> Dict[VoiceActionableIntent, List[Tuple[re.Pattern, float]]]:
        """Pre-compile regex patterns for performance."""
        compiled = {}
        for intent, patterns in self.INTENT_PATTERNS.items():
            compiled[intent] = [
                (re.compile(pattern, re.IGNORECASE), confidence)
                for pattern, confidence in patterns
            ]
        return compiled
    
    async def analyze_call(
        self,
        call_id: str,
        tenant_id: str,
        transcript_text: str,
        lead_id: Optional[str] = None
    ) -> Optional[DetectedIntent]:
        """
        Analyze transcript for actionable intents.
        
        Called from _save_call_data() after transcript is saved.
        This runs in background - no latency impact on call.
        
        Args:
            call_id: Call ID
            tenant_id: Tenant ID  
            transcript_text: Full transcript text
            lead_id: Lead ID if available
            
        Returns:
            DetectedIntent if actionable intent found, None otherwise
        """
        if not transcript_text or not transcript_text.strip():
            logger.debug(f"No transcript to analyze for call {call_id}")
            return None
        
        # Step 1: Detect intent from transcript
        intent, confidence, extracted_data = self._detect_intent(transcript_text)
        
        if intent == VoiceActionableIntent.NONE:
            logger.debug(f"No actionable intent detected in call {call_id}")
            return None
        
        if confidence < self.MIN_CONFIDENCE_THRESHOLD:
            logger.debug(
                f"Intent {intent} confidence {confidence} below threshold "
                f"{self.MIN_CONFIDENCE_THRESHOLD} for call {call_id}"
            )
            return None
        
        logger.info(
            "actionable_intent_detected",
            extra={
                "call_id": call_id,
                "intent": intent.value,
                "confidence": confidence,
                "extracted_data": extracted_data
            }
        )
        
        # Step 2: Check API availability
        required_connector = self.REQUIRED_CONNECTORS.get(intent)
        api_available = await self._check_api_available(tenant_id, required_connector)
        
        # Step 3: Check user permission for auto-actions
        permission_granted = await self._check_auto_action_permission(tenant_id)
        
        # Step 4: Determine readiness and build result
        if required_connector and not api_available:
            readiness = ActionReadiness.MISSING_API
            recommendation = self._build_recommendation_message(
                intent, 
                api_missing=required_connector
            )
        elif not permission_granted:
            readiness = ActionReadiness.NEEDS_PERMISSION
            recommendation = self._build_recommendation_message(
                intent,
                needs_permission=True
            )
        else:
            readiness = ActionReadiness.READY
            recommendation = None
        
        # Build action plan
        action_plan = self._build_action_plan(intent, extracted_data, lead_id)
        
        detected = DetectedIntent(
            intent=intent,
            confidence=confidence,
            extracted_data=extracted_data,
            readiness=readiness,
            action_plan=action_plan if readiness == ActionReadiness.READY else None,
            recommendation_message=recommendation
        )
        
        # Step 5: Save to call record
        await self._save_intent_to_call(call_id, detected)
        
        # Step 6: Execute if ready, otherwise log recommendation
        if readiness == ActionReadiness.READY:
            await self._execute_action(
                call_id=call_id,
                tenant_id=tenant_id,
                lead_id=lead_id,
                detected=detected
            )
            logger.info(
                "post_call_action_executed",
                extra={"call_id": call_id, "intent": intent.value}
            )
        else:
            logger.info(
                "post_call_action_pending",
                extra={
                    "call_id": call_id,
                    "intent": intent.value,
                    "readiness": readiness.value,
                    "recommendation": recommendation
                }
            )
        
        return detected
    
    def _detect_intent(
        self, 
        transcript: str
    ) -> Tuple[VoiceActionableIntent, float, Dict[str, Any]]:
        """
        Pattern-based intent detection on transcript.
        
        Uses hybrid approach: pattern matching with confidence scores.
        
        Args:
            transcript: Full transcript text
            
        Returns:
            Tuple of (intent, confidence, extracted_data)
        """
        best_intent = VoiceActionableIntent.NONE
        best_confidence = 0.0
        extracted_data = {}
        
        for intent, compiled_patterns in self._compiled_patterns.items():
            for pattern, base_confidence in compiled_patterns:
                match = pattern.search(transcript)
                if match:
                    # Boost confidence if multiple patterns match
                    confidence = base_confidence
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_intent = intent
                        
                        # Extract relevant data based on intent
                        if intent == VoiceActionableIntent.BOOKING_REQUEST:
                            extracted_data = self._extract_booking_data(transcript)
                        elif intent == VoiceActionableIntent.CALLBACK_LATER:
                            extracted_data = self._extract_callback_data(transcript)
        
        return best_intent, best_confidence, extracted_data
    
    def _extract_booking_data(self, transcript: str) -> Dict[str, Any]:
        """Extract meeting-related data from transcript."""
        data = {}
        
        # Extract time references
        time_patterns = [
            r'(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))',  # "2pm", "10:30 AM"
            r'(tomorrow|today|next\s+\w+day|monday|tuesday|wednesday|thursday|friday)',
            r'(morning|afternoon|evening)',
            r'(in\s+\d+\s+(?:hour|minute|day)s?)',
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, transcript, re.IGNORECASE)
            if match:
                data['time_reference'] = match.group(0)
                break
        
        return data
    
    def _extract_callback_data(self, transcript: str) -> Dict[str, Any]:
        """Extract callback-related data from transcript."""
        data = {}
        
        # Extract when they want to be called back
        time_patterns = [
            r'(tomorrow|later today|next week|in an hour)',
            r'(morning|afternoon|evening)',
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, transcript, re.IGNORECASE)
            if match:
                data['callback_time'] = match.group(0)
                break
        
        return data
    
    async def _check_api_available(
        self, 
        tenant_id: str, 
        connector_type: Optional[str]
    ) -> bool:
        """
        Check if required connector is active for tenant.
        
        Args:
            tenant_id: Tenant ID
            connector_type: Type of connector needed (calendar, email, etc.)
            
        Returns:
            True if connector is available and active
        """
        if not connector_type:
            return True  # No external API required
        
        try:
            result = self.db_client.table("connectors").select(
                "id, status"
            ).eq(
                "tenant_id", tenant_id
            ).eq(
                "type", connector_type
            ).eq(
                "status", "active"
            ).execute()
            
            return len(result.data) > 0
            
        except Exception as e:
            logger.warning(f"Failed to check connector availability: {e}")
            return False
    
    async def _check_auto_action_permission(self, tenant_id: str) -> bool:
        """
        Check if tenant has granted auto-action permission.
        
        This permission allows the system to automatically execute
        actions detected from call transcripts.
        
        Args:
            tenant_id: Tenant ID
            
        Returns:
            True if auto_actions_enabled is True for tenant
        """
        try:
            result = self.db_client.table("tenant_settings").select(
                "auto_actions_enabled"
            ).eq("tenant_id", tenant_id).execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0].get("auto_actions_enabled", False)
            
            return False
            
        except Exception as e:
            logger.warning(f"Failed to check auto-action permission: {e}")
            return False
    
    def _build_recommendation_message(
        self,
        intent: VoiceActionableIntent,
        api_missing: Optional[str] = None,
        needs_permission: bool = False
    ) -> str:
        """
        Build user-facing recommendation message.
        
        Shown in next interaction to help user enable the feature.
        
        Args:
            intent: Detected intent
            api_missing: Name of missing connector
            needs_permission: Whether permission is needed
            
        Returns:
            User-facing message
        """
        if api_missing:
            connector_messages = {
                "calendar": (
                    "The caller wanted to book a meeting. "
                    "Connect your calendar in Settings > Integrations to enable automatic booking."
                ),
                "email": (
                    "The caller requested follow-up information. "
                    "Connect your email in Settings > Integrations to send automatic follow-ups."
                ),
            }
            return connector_messages.get(
                api_missing,
                f"Connect {api_missing} to enable this action."
            )
        
        if needs_permission:
            intent_actions = {
                VoiceActionableIntent.BOOKING_REQUEST: "book meetings automatically",
                VoiceActionableIntent.FOLLOW_UP_REQUEST: "send follow-up emails automatically",
                VoiceActionableIntent.REMINDER_REQUEST: "schedule reminders automatically",
                VoiceActionableIntent.CALLBACK_LATER: "schedule callbacks automatically",
            }
            action = intent_actions.get(intent, "take actions automatically")
            return (
                f"The caller expressed interest that could {action}. "
                f"Enable Auto-Actions in Settings to handle this automatically."
            )
        
        return ""
    
    def _build_action_plan(
        self,
        intent: VoiceActionableIntent,
        extracted_data: Dict[str, Any],
        lead_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Build action plan for the detected intent.
        
        Uses the same action types as AssistantAgentService.
        
        Args:
            intent: Detected intent
            extracted_data: Data extracted from transcript
            lead_id: Lead ID
            
        Returns:
            List of action steps
        """
        if intent == VoiceActionableIntent.BOOKING_REQUEST:
            return [
                {
                    "type": "book_meeting",
                    "parameters": {
                        "lead_id": lead_id,
                        "title": "Follow-up Call",
                        "duration_minutes": 30,
                        "add_video_conference": True,
                        **extracted_data
                    }
                },
                {
                    "type": "send_email",
                    "parameters": {"template_name": "meeting_confirmation"},
                    "use_result_from": 0
                },
                {
                    "type": "schedule_reminder",
                    "parameters": {"offset": "-1h", "channel": "sms"},
                    "use_result_from": 0
                },
            ]
        
        elif intent == VoiceActionableIntent.FOLLOW_UP_REQUEST:
            return [
                {
                    "type": "send_email",
                    "parameters": {
                        "template_name": "follow_up",
                        "lead_id": lead_id
                    }
                },
            ]
        
        elif intent == VoiceActionableIntent.REMINDER_REQUEST:
            return [
                {
                    "type": "schedule_reminder",
                    "parameters": {
                        "lead_id": lead_id,
                        "message": "Reminder from your recent call",
                        **extracted_data
                    }
                },
            ]
        
        elif intent == VoiceActionableIntent.CALLBACK_LATER:
            return [
                {
                    "type": "schedule_reminder",
                    "parameters": {
                        "lead_id": lead_id,
                        "message": "Callback requested by lead",
                        **extracted_data
                    }
                },
            ]
        
        return []
    
    async def _save_intent_to_call(
        self, 
        call_id: str, 
        detected: DetectedIntent
    ) -> None:
        """
        Save detected intent to call record.
        
        Args:
            call_id: Call ID
            detected: Detected intent result
        """
        try:
            update_data = {
                "detected_intents": [detected.model_dump()],
                "updated_at": datetime.utcnow().isoformat()
            }
            
            # Add pending recommendation if applicable
            if detected.recommendation_message:
                update_data["pending_recommendations"] = detected.recommendation_message
            
            self.db_client.table("calls").update(
                update_data
            ).eq("id", call_id).execute()
            
            logger.debug(f"Saved intent to call {call_id}")
            
        except Exception as e:
            logger.error(f"Failed to save intent to call {call_id}: {e}")
    
    async def _execute_action(
        self,
        call_id: str,
        tenant_id: str,
        lead_id: Optional[str],
        detected: DetectedIntent
    ) -> None:
        """
        Execute action plan via AssistantAgentService.
        
        Only called when readiness == READY (APIs available + permission).
        
        Args:
            call_id: Call ID
            tenant_id: Tenant ID
            lead_id: Lead ID
            detected: Detected intent with action plan
        """
        if not detected.action_plan:
            logger.warning(f"No action plan to execute for call {call_id}")
            return
        
        try:
            from app.services.assistant_agent_service import get_assistant_agent_service
            
            agent_service = get_assistant_agent_service(self.db_client)
            
            # Create action plan
            plan = await agent_service.create_plan(
                tenant_id=tenant_id,
                intent=f"Post-call action: {detected.intent}",
                context={
                    "call_id": call_id,
                    "lead_id": lead_id,
                    "source": "post_call_analysis",
                    "extracted_data": detected.extracted_data
                },
                actions=detected.action_plan
            )
            
            # Execute the plan
            result = await agent_service.execute_plan(plan)
            
            # Update call record with results
            self.db_client.table("calls").update({
                "action_plan_id": plan.id,
                "action_results": {
                    "plan_id": plan.id,
                    "status": result.status,
                    "successful_steps": result.successful_steps,
                    "failed_steps": result.failed_steps,
                    "executed_at": datetime.utcnow().isoformat()
                }
            }).eq("id", call_id).execute()
            
            logger.info(
                "post_call_action_completed",
                extra={
                    "call_id": call_id,
                    "plan_id": plan.id,
                    "status": result.status,
                    "successful_steps": result.successful_steps,
                    "failed_steps": result.failed_steps
                }
            )
            
        except Exception as e:
            logger.error(
                f"Failed to execute post-call action for {call_id}: {e}",
                exc_info=True
            )


# Singleton instance
_post_call_analyzer: Optional[PostCallAnalyzer] = None


def get_post_call_analyzer(db_client: Client) -> PostCallAnalyzer:
    """
    Get or create PostCallAnalyzer singleton.
    
    Args:
        db_client: PostgreSQL client
        
    Returns:
        PostCallAnalyzer instance
    """
    global _post_call_analyzer
    if _post_call_analyzer is None:
        _post_call_analyzer = PostCallAnalyzer(db_client)
    return _post_call_analyzer
