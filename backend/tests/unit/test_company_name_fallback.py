"""S1 regression - the company-name fallback must not be a real brand.

`telephony_session_config.TELEPHONY_COMPANY_NAME` used to be the literal
"All States Estimation" (a real, specific company), and
`telephony/config.py::_resolve_greeting_context` hardcoded the same string a
second time. On this multi-tenant platform any tenant whose campaign /
AI config left `company_name` unset had its agent introduce itself on a live
PSTN call under ANOTHER tenant's brand - a cross-tenant leak and a
misrepresentation of who is calling.

Contract asserted here:
  1. Neither composition path can ever emit the real brand from a fallback.
  2. A tenant that DID set a company name is completely unaffected.
  3. The fallback is logged loudly, once per call, with tenant + campaign id
     and no phone numbers.
"""
import logging
import re
from unittest.mock import MagicMock, patch

REAL_BRAND = "All States Estimation"


def _mock_global_config():
    cfg = MagicMock()
    cfg.tts_provider = "cartesia"
    cfg.tts_voice_id = "test-voice"
    cfg.tts_model = "sonic-3"
    cfg.llm_model = "test-model"
    cfg.llm_temperature = 0.5
    cfg.llm_max_tokens = 200
    cfg.llm_provider = "groq"
    cfg.stt_engine = "flux"
    cfg.pipeline_mode = "cascaded"
    cfg.realtime_model = ""
    cfg.realtime_voice = ""
    cfg.realtime_settings = {}
    return cfg


def _build(campaign, **kw):
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )
    with patch(
        "app.domain.services.telephony_session_config.get_global_config",
        return_value=_mock_global_config(),
    ):
        return build_telephony_session_config(
            gateway_type="telephony", campaign=campaign, **kw
        )


# ---------------------------------------------------------------------------
# 1. No real brand may leak out of either fallback
# ---------------------------------------------------------------------------
class TestFallbackIsNotARealBrand:
    def test_constant_is_not_the_real_company(self):
        from app.domain.services.telephony_session_config import (
            TELEPHONY_COMPANY_NAME,
        )
        assert REAL_BRAND.lower() not in TELEPHONY_COMPANY_NAME.lower()

    def test_neither_module_hardcodes_the_real_company(self):
        """Guard: the literal must not come back in either fenced file."""
        import inspect
        from app.domain.services import telephony_session_config as tsc
        from app.domain.services.telephony import config as tconf
        for mod in (tsc, tconf):
            src = inspect.getsource(mod)
            assert REAL_BRAND not in src, (
                f"{mod.__name__} still hardcodes {REAL_BRAND!r}"
            )

    def test_campaign_without_company_name_does_not_speak_the_real_brand(self):
        cfg = _build({
            "id": "camp-1",
            "tenant_id": "tenant-A",
            "script_config": {"knowledge_driven": True},
        })
        assert REAL_BRAND not in cfg.system_prompt
        assert not any(
            REAL_BRAND.lower() in (t or "").lower()
            for t in (cfg.stt_keyterms or [])
        )
        assert cfg.agent_config.company_name
        assert REAL_BRAND not in cfg.agent_config.company_name

    def test_campaign_less_call_does_not_speak_the_real_brand(self):
        cfg = _build(None)
        assert REAL_BRAND not in cfg.system_prompt

    def test_fallback_still_composes_a_readable_identity_line(self):
        """The neutral value must read acceptably where it is spoken/asserted -
        an empty or absurd value would produce a broken sentence."""
        from app.domain.services.telephony_session_config import (
            TELEPHONY_COMPANY_NAME,
            build_telephony_inbound_greeting,
        )
        assert TELEPHONY_COMPANY_NAME.strip() == TELEPHONY_COMPANY_NAME
        assert len(TELEPHONY_COMPANY_NAME) >= 3
        greeting = build_telephony_inbound_greeting("Sarah", TELEPHONY_COMPANY_NAME)
        assert TELEPHONY_COMPANY_NAME in greeting
        assert "  " not in greeting


# ---------------------------------------------------------------------------
# 2. A tenant that HAS a company name is untouched (both call paths)
# ---------------------------------------------------------------------------
class TestConfiguredCompanyNameUnaffected:
    def test_prompt_path_uses_the_tenants_own_name(self, caplog):
        from app.domain.services.telephony_session_config import (
            TELEPHONY_COMPANY_NAME,
        )
        with caplog.at_level(logging.WARNING):
            cfg = _build({
                "id": "camp-2",
                "tenant_id": "tenant-B",
                "script_config": {
                    "knowledge_driven": True,
                    "company_name": "Blue Ridge Roofing",
                },
            })
        assert "Blue Ridge Roofing" in cfg.system_prompt
        assert cfg.agent_config.company_name == "Blue Ridge Roofing"
        assert TELEPHONY_COMPANY_NAME not in cfg.system_prompt
        assert not [
            r for r in caplog.records
            if "company_name_fallback" in r.getMessage()
        ]

    def test_greeting_path_uses_the_tenants_own_name(self, caplog):
        from app.domain.services.telephony.config import _resolve_greeting_context
        session = MagicMock()
        session.agent_config.agent_name = "Sarah"
        session.agent_config.company_name = "Blue Ridge Roofing"
        with caplog.at_level(logging.WARNING):
            agent_name, company = _resolve_greeting_context(session)
        assert (agent_name, company) == ("Sarah", "Blue Ridge Roofing")
        assert not [
            r for r in caplog.records
            if "company_name_fallback" in r.getMessage()
        ]


# ---------------------------------------------------------------------------
# 3. The fallback is visible - one warning per call, with the ids
# ---------------------------------------------------------------------------
class TestFallbackIsLoggedOncePerCall:
    def test_prompt_path_warns_once_with_tenant_and_campaign(self, caplog):
        with caplog.at_level(logging.WARNING):
            _build({
                "id": "camp-3",
                "tenant_id": "tenant-C",
                "script_config": {"knowledge_driven": True},
            })
        hits = [
            r for r in caplog.records if "company_name_fallback" in r.getMessage()
        ]
        assert len(hits) == 1, [r.getMessage() for r in hits]
        msg = hits[0].getMessage()
        assert "tenant-C" in msg and "camp-3" in msg
        assert not re.search(r"\+?\d{7,}", msg), f"log leaks a number: {msg}"

    def test_greeting_path_warns_once_per_session(self, caplog):
        from app.domain.services.telephony.config import _resolve_greeting_context

        class _Sess:
            tenant_id = "tenant-D"
            campaign_id = "camp-4"
            agent_config = None

        session = _Sess()
        with caplog.at_level(logging.WARNING):
            _resolve_greeting_context(session)
            _resolve_greeting_context(session)
            _resolve_greeting_context(session)
        hits = [
            r for r in caplog.records if "company_name_fallback" in r.getMessage()
        ]
        assert len(hits) == 1, [r.getMessage() for r in hits]
        msg = hits[0].getMessage()
        assert "tenant-D" in msg and "camp-4" in msg
        assert not re.search(r"\+?\d{7,}", msg)

    def test_greeting_path_returns_the_neutral_fallback(self):
        from app.domain.services.telephony.config import _resolve_greeting_context
        from app.domain.services.telephony_session_config import (
            TELEPHONY_COMPANY_NAME,
        )

        class _Sess:
            tenant_id = None
            campaign_id = None
            agent_config = None

        agent_name, company = _resolve_greeting_context(_Sess())
        assert agent_name == "your assistant"
        assert company == TELEPHONY_COMPANY_NAME
        assert REAL_BRAND not in company
