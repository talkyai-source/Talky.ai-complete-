"""Smoke test: telephony package imports without cycles or errors."""


def test_package_imports():
    from app.domain.services import telephony  # noqa: F401
    from app.domain.services.telephony import modes  # noqa: F401
