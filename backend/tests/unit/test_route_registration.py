"""The decorator must be attached to the handler you think it is.

WHY THIS EXISTS
---------------
On 2026-08-23 two helper functions were inserted between
``@router.websocket("/ws/campaign-test/{campaign_id}")`` and the handler it was
meant to decorate. The route bound to the helper instead. The real handler
stopped being registered at all, and every "Test agent" click got a 403 that the
browser reported as "Connection timeout. Is the backend running?".

It reached production because nothing checked it:

  * ``python -m py_compile`` passed — the file was valid Python;
  * the full 4,952-test gate passed — ``test_campaign_test_ws`` imports
    ``campaign_test_websocket`` and calls it DIRECTLY, so it never touches
    routing;
  * the earlier structural break (helpers at column 0 *inside* the handler) had
    already been caught and fixed, which made the file look reviewed.

A test that calls a handler directly proves the handler works. It says nothing
about whether anything can reach it. This file closes that gap for the routes
where a mis-binding is silent rather than loud.
"""

from __future__ import annotations

import pytest

from app.api.v1.endpoints.call_feedback import router as feedback_router
from app.api.v1.endpoints.campaign_test_ws import router as campaign_test_router
from app.api.v1.endpoints.conversation_reviews import (
    admin_router as reviews_admin_router,
    router as reviews_router,
)


def bindings(router) -> list[tuple[str, str]]:
    """(path, endpoint name) for every route.

    A list, not a dict keyed by path: the same path is registered once per
    method, so ``/calls/{call_id}/feedback`` appears twice (POST and GET) and a
    dict would silently keep only the last one — which is how the first draft of
    this file reported two perfectly-registered handlers as missing.
    """
    return [(r.path, r.endpoint.__name__) for r in router.routes]


def handler_names(router) -> set[str]:
    return {name for _, name in bindings(router)}


def test_the_campaign_test_websocket_is_bound_to_its_handler():
    """THE REGRESSION. A helper wearing this decorator is a silent outage."""
    bound = dict(bindings(campaign_test_router))
    path = "/ws/campaign-test/{campaign_id}"
    assert path in bound, f"route missing entirely; registered: {sorted(bound)}"
    assert bound[path] == "campaign_test_websocket", (
        f"{path} is bound to {bound[path]!r}, not the handler. A decorator "
        "separated from its function by an inserted helper registers the "
        "helper, and every upgrade is refused with 403."
    )


def test_no_route_anywhere_is_bound_to_a_private_helper():
    """A leading underscore means "not the public entry point". If one is
    serving traffic, something was inserted in the wrong place."""
    for name, router in (
        ("campaign_test", campaign_test_router),
        ("call_feedback", feedback_router),
        ("reviews", reviews_router),
        ("reviews_admin", reviews_admin_router),
    ):
        for path, fn in bindings(router):
            assert not fn.startswith("_"), (
                f"{name}: {path} is served by private helper {fn!r}"
            )


@pytest.mark.parametrize(
    "router, expected",
    [
        (feedback_router, {"submit_call_feedback", "get_call_feedback",
                           "retry_call_feedback_transcription",
                           "get_call_feedback_audio"}),
        (reviews_router, {"submit_review", "get_my_review", "list_call_reviews",
                          "get_review_options", "get_reward_balance"}),
        (reviews_admin_router, {"list_reviews", "summarise_reviews"}),
    ],
)
def test_expected_handlers_are_actually_registered(router, expected):
    """Catches a route silently disappearing, which is what happened here —
    the handler was still importable and still tested, just unreachable."""
    registered = handler_names(router)
    missing = expected - registered
    assert not missing, f"handlers not registered: {sorted(missing)}"
