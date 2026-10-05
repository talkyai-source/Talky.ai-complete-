"""Reviewed selection must accept actual adapter UUIDs without coercing objects."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID
import pytest

from app.core.postgres_adapter import QueryBuilder
from app.services.connector_resolver import ReviewedConnectorChanged, _reviewed_account_row


ACCOUNT = "ea02a99e-6697-49ec-8b02-de22770c040a"


def database(raw_identity):
    # Use the actual adapter decode boundary: uuid is intentionally not JSON.
    decoded = QueryBuilder(None, "connector_accounts")._decode_row(
        {"id": raw_identity, "created_at": datetime(2026, 10, 6, tzinfo=timezone.utc)},
        {"id": "uuid", "created_at": "timestamptz"},
    )
    assert decoded["id"] is raw_identity
    db = Mock()

    def table(name):
        query = Mock()
        for method in ("select", "eq", "order", "limit"):
            getattr(query, method).return_value = query
        query.execute.return_value = SimpleNamespace(
            error=None, data=[{"id": "connector-a"}] if name == "connectors" else [decoded]
        )
        return query

    db.table.side_effect = table
    return db, decoded


@pytest.mark.parametrize("raw_identity", [UUID(ACCOUNT), AsyncpgUUID(ACCOUNT), ACCOUNT, "authorization-a"])
def test_native_uuid_and_existing_text_match_the_same_reviewed_row(raw_identity):
    db, decoded = database(raw_identity)
    selected = _reviewed_account_row(db, "tenant-a", "connector-a", "gmail", str(raw_identity))
    assert selected["id"] == str(raw_identity)
    assert isinstance(selected["id"], str)
    assert decoded["id"] is raw_identity  # Do not rewrite the adapter's response.


class LooksLikeIdentity:
    def __str__(self):
        raise AssertionError("Arbitrary object identity must not be stringified")


@pytest.mark.parametrize("raw_identity", [None, "", "   ", 123, True, b"account", [], {}, LooksLikeIdentity()])
def test_missing_blank_or_arbitrary_identity_is_denied(raw_identity):
    db, _ = database(raw_identity)
    with pytest.raises(ReviewedConnectorChanged, match="current authorization cannot be established"):
        _reviewed_account_row(db, "tenant-a", "connector-a", "gmail")


@pytest.mark.parametrize("raw_identity", [UUID(ACCOUNT), AsyncpgUUID(ACCOUNT), ACCOUNT])
def test_native_identity_cannot_satisfy_another_reviewed_row(raw_identity):
    db, _ = database(raw_identity)
    with pytest.raises(ReviewedConnectorChanged, match="changed after review"):
        _reviewed_account_row(db, "tenant-a", "connector-a", "gmail", "replacement-row")
