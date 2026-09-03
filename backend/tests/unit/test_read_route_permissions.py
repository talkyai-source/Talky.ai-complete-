"""Read surfaces must enforce revocable database-backed RBAC permissions."""

from __future__ import annotations

import pytest

from app.api.v1.endpoints import analytics, calls, dashboard


def _dependencies(router, method: str, path: str) -> set[object]:
    route = next(
        item for item in router.routes if item.path == path and method in item.methods
    )
    return {dependency.call for dependency in route.dependant.dependencies}


@pytest.mark.parametrize(
    "path",
    [
        "/analytics/best-time",
        "/analytics/retry-effectiveness",
        "/analytics/calls",
        "/analytics/calls/by-campaign",
    ],
)
def test_analytics_reads_require_analytics_permission(path):
    assert analytics._require_analytics_read in _dependencies(
        analytics.router, "GET", path
    )


def test_dashboard_summary_requires_analytics_permission():
    assert dashboard._require_analytics_read in _dependencies(
        dashboard.router, "GET", "/dashboard/summary"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/calls/live",
        "/calls/rejected",
        "/calls/issues",
        "/calls/",
        "/calls/{call_id}",
        "/calls/{call_id}/transcript",
        "/calls/{call_id}/summary",
        "/calls/{call_id}/events",
        "/calls/{call_id}/legs",
    ],
)
def test_call_reads_require_calls_read_permission(path):
    assert calls._require_calls_read in _dependencies(calls.router, "GET", path)
