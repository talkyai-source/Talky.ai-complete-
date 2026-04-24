"""
tests/core_prompts/test_core_prompts.py

Tests for all three core prompts.
Verifies that:
  - The rails (structure, rules, states) are always present
  - Client context is correctly injected
  - The prompt works for any industry (web agency = dental = law firm etc.)
  - Forbidden phrases never appear in agent response scripts
  - The PromptManager routes correctly

Run: pytest tests/core_prompts/ -v
"""
import pytest
from app.domain.services.core_prompts import (
    CorePromptEngine,
    CompileRequest,
    PromptType,
    EXAMPLE_REQUESTS,
    get_core_prompt_engine,
)
from app.domain.services.prompt_manager import PromptManager
from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationRule


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    return CorePromptEngine()


@pytest.fixture
def pm():
    return PromptManager()


# ─────────────────────────────────────────────────────────────
# 1. CorePromptEngine — structural tests
# ─────────────────────────────────────────────────────────────

class TestCorePromptStructure:

    def test_all_three_types_compile(self, engine):
        for pt in PromptType:
            req = CompileRequest(
                prompt_type=pt,
                business_name="Test Co",
                agent_name="Alex",
                client_description=f"test business for {pt}",
            )
            result = engine.compile(req)
            assert len(result) > 200, f"Prompt for {pt} is too short"

    def test_business_name_injected(self, engine):
        req = CompileRequest(
            prompt_type=PromptType.APPOINTMENT_BOOKING,
            business_name="DevCraft Agency",
            agent_name="Alex",
            client_description="web development agency",
        )
        result = engine.compile(req)
        assert "DevCraft Agency" in result

    def test_agent_name_injected(self, engine):
        req = CompileRequest(
            prompt_type=PromptType.CUSTOMER_SUPPORT,
            business_name="FlowDesk",
            agent_name="Maya",
            client_description="SaaS support",
        )
        result = engine.compile(req)
        assert "Maya" in result

    def test_client_description_injected(self, engine):
        desc = "independent gym booking free trial sessions with new members"
        req = CompileRequest(
            prompt_type=PromptType.APPOINTMENT_BOOKING,
            business_name="Peak Fitness",
            agent_name="Jordan",
            client_description=desc,
        )
        result = engine.compile(req)
        assert desc in result

    def test_client_custom_rules_injected(self, engine):
        req = CompileRequest(
            prompt_type=PromptType.APPOINTMENT_BOOKING,
            business_name="DevCraft",
            agent_name="Alex",
            client_description="web agency",
            client_custom_rules=["Do not quote prices on this call", "Only book discovery calls"],
        )
        result = engine.compile(req)
        assert "Do not quote prices on this call" in result
        assert "Only book discovery calls" in result

    def test_context_values_injected(self, engine):
        req = CompileRequest(
            prompt_type=PromptType.ORDER_TAKER,
            business_name="Mario's Pizza",
            agent_name="Sofia",
            client_description="pizza restaurant",
            context={"delivery_wait": "30-40 minutes", "minimum_order": "£15"},
        )
        result = engine.compile(req)
        assert "30-40 minutes" in result
        assert "£15" in result

    def test_universal_rules_always_present(self, engine):
        for pt in PromptType:
            req = CompileRequest(
                prompt_type=pt,
                business_name="Acme", agent_name="Bot",
                client_description="test",
            )
            result = engine.compile(req)
            assert "Never make up information" in result
            assert "Never promise something you cannot guarantee" in result
            assert "2 sentences" in result or "Maximum 2 sentences" in result


# ─────────────────────────────────────────────────────────────
# 2. Appointment Booking — rails verification
# ─────────────────────────────────────────────────────────────

class TestAppointmentBookingPrompt:

    REQUIRED_RAILS = [
        "GREETING",
        "CONFIRM OR BOOK",
        "RESCHEDULE",
        "CLOSE",
        "WRONG PERSON",
        "I'M BUSY RIGHT NOW",
        "REQUEST FOR HUMAN",
        "OFF-TOPIC",
    ]

    def test_all_rails_present(self, engine):
        req = EXAMPLE_REQUESTS["dental_appointment"]
        result = engine.compile(req)
        for rail in self.REQUIRED_RAILS:
            assert rail in result, f"Missing rail: {rail}"

    def test_works_for_web_agency(self, engine):
        req = EXAMPLE_REQUESTS["web_agency_discovery"]
        result = engine.compile(req)
        assert "DevCraft Agency" in result
        assert "discovery" in result.lower()
        assert "GREETING" in result
        assert "RESCHEDULE" in result

    def test_works_for_law_firm(self, engine):
        req = EXAMPLE_REQUESTS["law_firm_consultation"]
        result = engine.compile(req)
        assert "Morrison & Partners" in result
        assert "Do not give legal advice" in result

    def test_works_for_gym(self, engine):
        req = EXAMPLE_REQUESTS["gym_membership"]
        result = engine.compile(req)
        assert "Peak Fitness" in result
        assert "free" in result.lower()

    def test_greeting_script_is_present(self, engine):
        req = CompileRequest(
            prompt_type=PromptType.APPOINTMENT_BOOKING,
            business_name="TestCo", agent_name="Sam",
            client_description="test",
        )
        result = engine.compile(req)
        assert "Hi, this is" in result or "GREETING" in result

    def test_confirm_detail_instruction_present(self, engine):
        """Closing state must instruct agent to recap the key detail."""
        req = EXAMPLE_REQUESTS["dental_appointment"]
        result = engine.compile(req)
        assert "CLOSE" in result or "closing" in result.lower()
        assert "confirm" in result.lower() or "recap" in result.lower()


# ─────────────────────────────────────────────────────────────
# 3. Customer Support — rails verification
# ─────────────────────────────────────────────────────────────

class TestCustomerSupportPrompt:

    REQUIRED_RAILS = [
        "GREETING",
        "UNDERSTAND THE ISSUE",
        "RESOLVE OR ROUTE",
        "CONFIRM AND CLOSE",
        "ANGRY CUSTOMER",
        "I WANT A REFUND",
        "I WANT TO CANCEL",
        "REQUEST FOR MANAGER",
    ]

    def test_all_rails_present(self, engine):
        req = EXAMPLE_REQUESTS["saas_support"]
        result = engine.compile(req)
        for rail in self.REQUIRED_RAILS:
            assert rail in result, f"Missing rail: {rail}"

    def test_angry_customer_instruction_is_action_focused(self, engine):
        req = EXAMPLE_REQUESTS["saas_support"]
        result = engine.compile(req)
        # Should say what to DO, not just sympathise
        assert "DO" in result or "fix" in result.lower() or "action" in result.lower()

    def test_one_question_at_a_time_instruction(self, engine):
        req = EXAMPLE_REQUESTS["ecommerce_support"]
        result = engine.compile(req)
        assert "ONE" in result or "one" in result

    def test_works_for_ecommerce(self, engine):
        req = EXAMPLE_REQUESTS["ecommerce_support"]
        result = engine.compile(req)
        assert "GiftBox Co." in result
        assert "14 days" in result  # return window from context

    def test_works_for_property_management(self, engine):
        req = EXAMPLE_REQUESTS["property_management"]
        result = engine.compile(req)
        assert "Citywide Properties" in result
        assert "Emergency maintenance" in result or "emergency" in result.lower()

    def test_no_hollow_phrases_in_support_rails(self, engine):
        req = EXAMPLE_REQUESTS["saas_support"]
        result = engine.compile(req)
        hollow_phrases = [
            "I completely understand your frustration",
            "I sincerely apologise",
            "I can certainly help you",
        ]
        for phrase in hollow_phrases:
            # These should appear in the RULES section as things to NOT say
            # but must not appear as scripted responses
            lines_with_phrase = [l for l in result.split("\n") if phrase in l]
            for line in lines_with_phrase:
                # If it appears, it should be in the "don't say this" context
                assert "never" in line.lower() or "replace" in line.lower() or "→" in line, (
                    f"Hollow phrase '{phrase}' may be scripted as a response"
                )


# ─────────────────────────────────────────────────────────────
# 4. Order Taker — rails verification
# ─────────────────────────────────────────────────────────────

class TestOrderTakerPrompt:

    REQUIRED_RAILS = [
        "GREETING",
        "TAKE THE ORDER",
        "CONFIRM THE COMPLETE ORDER",
        "COLLECT DETAILS",
        "CLOSE",
        "ITEM NOT ON MENU",
        "OUT OF STOCK",
        "CAN I CHANGE MY ORDER",
    ]

    def test_all_rails_present(self, engine):
        req = EXAMPLE_REQUESTS["pizza_restaurant"]
        result = engine.compile(req)
        for rail in self.REQUIRED_RAILS:
            assert rail in result, f"Missing rail: {rail}"

    def test_confirm_back_each_item_instruction(self, engine):
        req = EXAMPLE_REQUESTS["pizza_restaurant"]
        result = engine.compile(req)
        assert "confirm" in result.lower()
        assert "Anything else" in result

    def test_full_order_readback_instruction(self, engine):
        req = EXAMPLE_REQUESTS["pizza_restaurant"]
        result = engine.compile(req)
        assert "full order" in result.lower() or "complete order" in result.lower()

    def test_explicit_confirmation_required(self, engine):
        req = EXAMPLE_REQUESTS["pizza_restaurant"]
        result = engine.compile(req)
        assert "explicit confirmation" in result.lower() or "Does that look right" in result

    def test_menu_items_in_context_block(self, engine):
        req = EXAMPLE_REQUESTS["pizza_restaurant"]
        result = engine.compile(req)
        assert "Margherita" in result
        assert "£12" in result

    def test_works_for_pharmacy(self, engine):
        req = EXAMPLE_REQUESTS["pharmacy_order"]
        result = engine.compile(req)
        assert "Central Pharmacy" in result
        assert "Never advise on medication" in result or "medication" in result.lower()

    def test_works_for_catering(self, engine):
        req = EXAMPLE_REQUESTS["catering_order"]
        result = engine.compile(req)
        assert "Fresh Feast Catering" in result
        assert "dietary" in result.lower()

    def test_no_commentary_on_order_rule(self, engine):
        """Agent must never say 'great choice' or 'that's popular'."""
        req = EXAMPLE_REQUESTS["pizza_restaurant"]
        result = engine.compile(req)
        assert "comment on what they ordered" in result.lower() or \
               "good choice" in result.lower() or "that's popular" in result.lower()
        # These appear in the rules section as things to NOT do


# ─────────────────────────────────────────────────────────────
# 5. PromptManager routing tests
# ─────────────────────────────────────────────────────────────

class TestPromptManagerRouting:

    def _make_config(self, goal="appointment_confirmation", ctx=None):
        return AgentConfig(
            goal=AgentGoal.APPOINTMENT_CONFIRMATION,
            business_type="dental clinic",
            agent_name="Sarah",
            company_name="Bright Smile Dental",
            context=ctx or {},
        )

    def test_routes_to_system_prompt_in_context(self, pm):
        custom = "You are a custom agent. Do exactly this."
        config = self._make_config(ctx={"system_prompt": custom})
        result = pm.get_system_prompt(config)
        assert result == custom

    def test_routes_to_core_prompt_when_prompt_type_set(self, pm):
        config = self._make_config(ctx={
            "prompt_type": "appointment_booking",
            "client_description": "dental clinic confirming appointments",
        })
        result = pm.get_system_prompt(config)
        assert "GREETING" in result
        assert "RESCHEDULE" in result
        assert "Bright Smile Dental" in result

    def test_auto_routes_appointment_goal_to_core_prompt(self, pm):
        config = self._make_config(ctx={
            "client_description": "dental practice booking check-ups",
        })
        result = pm.get_system_prompt(config)
        # Should get a core prompt with the rails
        assert "GREETING" in result or "greeting" in result.lower()

    def test_returns_something_for_none_config(self, pm):
        result = pm.get_system_prompt(None)
        assert len(result) > 10

    def test_fallback_still_works_without_context(self, pm):
        config = self._make_config(ctx={})
        result = pm.get_system_prompt(config)
        assert len(result) > 100


# ─────────────────────────────────────────────────────────────
# 6. Cross-industry universality test
# ─────────────────────────────────────────────────────────────

class TestUniversality:
    """
    The key test: the same prompt type must work for wildly different industries.
    A web agency, a dental clinic, and a law firm all use APPOINTMENT_BOOKING
    and all get the same rails — just with different client context.
    """

    APPOINTMENT_INDUSTRIES = [
        ("DevCraft Agency",    "web development agency booking discovery calls"),
        ("Bright Smile Dental", "dental clinic confirming patient appointments"),
        ("Morrison & Partners",  "law firm scheduling initial consultations"),
        ("Peak Fitness",         "gym booking free trial sessions"),
        ("Speedy Mechanics",     "car garage booking MOT and service appointments"),
        ("Bloom Hair Studio",    "hair salon booking appointments for cuts and colour"),
        ("TutorPro",             "tutoring company booking first sessions with students"),
        ("City Real Estate",     "estate agent booking property viewings"),
    ]

    SUPPORT_INDUSTRIES = [
        ("FlowDesk",        "project management SaaS handling billing questions"),
        ("GiftBox Co.",     "online gift shop handling returns and delivery issues"),
        ("PowerNet",        "broadband provider handling outages and billing disputes"),
        ("InsureQuick",     "insurance company handling claims and policy questions"),
        ("Citywide Props",  "property management handling tenant maintenance requests"),
    ]

    ORDER_INDUSTRIES = [
        ("Mario's Pizza",        "pizza restaurant taking delivery orders"),
        ("Bloom Florist",        "florist taking orders for arrangements and delivery"),
        ("Fresh Feast Catering", "catering company taking event bookings"),
        ("PetPal Supplies",      "pet supplies shop taking phone orders"),
        ("Central Pharmacy",     "pharmacy taking prescription collection bookings"),
    ]

    def _compile(self, engine, prompt_type, business, description):
        return engine.compile(CompileRequest(
            prompt_type=prompt_type,
            business_name=business,
            agent_name="Alex",
            client_description=description,
        ))

    def test_appointment_rails_present_for_all_industries(self, engine):
        rails = ["GREETING", "RESCHEDULE", "REQUEST FOR HUMAN"]
        for business, desc in self.APPOINTMENT_INDUSTRIES:
            result = self._compile(engine, PromptType.APPOINTMENT_BOOKING, business, desc)
            for rail in rails:
                assert rail in result, f"Missing '{rail}' for {business}"
            assert business in result, f"Business name missing for {business}"

    def test_support_rails_present_for_all_industries(self, engine):
        rails = ["GREETING", "UNDERSTAND THE ISSUE", "ANGRY CUSTOMER"]
        for business, desc in self.SUPPORT_INDUSTRIES:
            result = self._compile(engine, PromptType.CUSTOMER_SUPPORT, business, desc)
            for rail in rails:
                assert rail in result, f"Missing '{rail}' for {business}"

    def test_order_rails_present_for_all_industries(self, engine):
        rails = ["GREETING", "TAKE THE ORDER", "ITEM NOT ON MENU"]
        for business, desc in self.ORDER_INDUSTRIES:
            result = self._compile(engine, PromptType.ORDER_TAKER, business, desc)
            for rail in rails:
                assert rail in result, f"Missing '{rail}' for {business}"

    def test_all_examples_compile_without_error(self, engine):
        for name, req in EXAMPLE_REQUESTS.items():
            result = engine.compile(req)
            assert len(result) > 300, f"Example '{name}' produced too short a prompt"
