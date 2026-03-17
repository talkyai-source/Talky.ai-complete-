from __future__ import annotations

from app.api.v1.endpoints.telephony_concurrency import router


def test_day9_concurrency_routes_registered() -> None:
    paths = {route.path for route in router.routes}
    assert "/telephony/sip/runtime/concurrency/status" in paths
    assert "/telephony/sip/runtime/concurrency/leases/acquire" in paths
    assert "/telephony/sip/runtime/concurrency/leases/{lease_id}/release" in paths
    assert "/telephony/sip/runtime/concurrency/leases/{lease_id}/heartbeat" in paths
    assert "/telephony/sip/runtime/concurrency/leases/expire" in paths
