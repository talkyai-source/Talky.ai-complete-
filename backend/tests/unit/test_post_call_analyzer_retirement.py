from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
RETIREMENT_NOTICE = "Retired on 2026-09-02"


def test_unwired_post_call_analyzer_is_not_shipped_as_a_runtime_feature() -> None:
    """Do not advertise executable post-call actions without a real call-end hook."""

    assert not (
        BACKEND_ROOT / "app" / "domain" / "services" / "post_call_analyzer.py"
    ).exists()
    assert not (
        BACKEND_ROOT / "app" / "domain" / "models" / "voice_intent.py"
    ).exists()

    for historical_record in (
        "docs/day_twenty_nine_voice_intent_actions.md",
        "docs/day_thirty_crm_drive_integration.md",
    ):
        assert RETIREMENT_NOTICE in (BACKEND_ROOT / historical_record).read_text(
            encoding="utf-8"
        )

    overview = (BACKEND_ROOT / "docs" / "project_overview.md").read_text(
        encoding="utf-8"
    )
    assert "`test_post_call_analyzer.py`" not in overview
