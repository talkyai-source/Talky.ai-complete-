"""
tests/dental/conftest.py — pytest configuration for dental workflow tests
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires a running backend (API_BASE_URL)")
    config.addinivalue_line("markers", "latency: requires real API keys and RUN_LATENCY=true")


def pytest_collection_modifyitems(config, items):
    """Skip integration and latency tests unless explicitly enabled."""
    import os
    skip_integration = pytest.mark.skip(reason="Set API_BASE_URL and TEST_TOKEN to run integration tests")
    skip_latency     = pytest.mark.skip(reason="Set RUN_LATENCY=true and all API keys to run latency tests")

    for item in items:
        if "integration" in item.keywords and not os.getenv("TEST_TOKEN"):
            item.add_marker(skip_integration)
        if "latency" in item.keywords and os.getenv("RUN_LATENCY", "").lower() != "true":
            item.add_marker(skip_latency)
