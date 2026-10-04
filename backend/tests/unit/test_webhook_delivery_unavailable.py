"""An owned webhook configuration is not evidence that a send occurred."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import webhooks_admin


class WebhookLookup:
    def __init__(self, rows=(), *, error=None, raises=None):
        self.rows = list(rows)
        self.error = error
        self.raises = raises
        self.filters = {}

    def table(self, name):
        assert name == "webhook_endpoints"
        return self

    def select(self, fields):
        assert fields == "*"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        if self.raises:
            raise self.raises
        rows = [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in self.filters.items())
        ]
        return SimpleNamespace(error=self.error, data=rows)


@pytest.mark.asyncio
async def test_owned_webhook_reports_unsupported_without_a_delivery_receipt():
    db = WebhookLookup(
        [{"id": "endpoint-a", "tenant_id": "tenant-a", "url": "https://example.invalid/hook"}]
    )
    with pytest.raises(HTTPException) as exc:
        await webhooks_admin.test_webhook("endpoint-a", SimpleNamespace(tenant_id="tenant-a"), db)
    assert exc.value.status_code == 501
    assert exc.value.detail == {
        "code": "outbound_webhook_delivery_unavailable",
        "message": "Outbound webhook delivery is not available. No test notification was sent.",
    }
    assert db.filters == {"id": "endpoint-a", "tenant_id": "tenant-a"}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint_id", ["endpoint-b", "missing"])
async def test_foreign_or_missing_configuration_remains_not_found(endpoint_id):
    db = WebhookLookup([{"id": "endpoint-b", "tenant_id": "tenant-b"}])
    with pytest.raises(HTTPException) as exc:
        await webhooks_admin.test_webhook(endpoint_id, SimpleNamespace(tenant_id="tenant-a"), db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["adapter_error", "exception"])
async def test_unavailable_configuration_storage_is_not_not_found_or_send_success(failure):
    db = WebhookLookup(
        error="Synthetic missing configuration storage" if failure == "adapter_error" else None,
        raises=RuntimeError("Synthetic storage unavailable") if failure == "exception" else None,
    )
    with pytest.raises(HTTPException) as exc:
        await webhooks_admin.test_webhook("endpoint-a", SimpleNamespace(tenant_id="tenant-a"), db)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "webhook_configuration_unavailable"
    assert "Synthetic" not in str(exc.value.detail)
