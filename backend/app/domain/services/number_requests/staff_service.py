"""Staff side of number requests: list, read, open one document, decide.

Every statement on a request table carries ``tenant_id = $N``. The staff list
therefore goes account by account (the account directory comes from ``tenants``,
which holds no request data), the same way the nightly cleanup worker does.

Nothing here logs or returns a file's content, a storage key or a wrapped key
to a caller that should not have it: ``get_document`` is the only function that
returns them, and only to the document route.
"""
from __future__ import annotations

from typing import Any

# Account directory (tenants is not row-protected and holds no request data).
_T0 = (
    "SELECT id, business_name FROM tenants "
    "WHERE ($1::uuid IS NULL OR id > $1::uuid) ORDER BY id LIMIT $2"
)
_T1 = (
    "SELECT id, tenant_id, requested_by_user_id, use_type, status, submitted_at, reviewed_at, "
    "files_deleted_at FROM telephony_number_requests "
    "WHERE tenant_id = $1 AND status = $2 ORDER BY submitted_at DESC, id DESC LIMIT $3"
)
_T2 = (
    "SELECT id, tenant_id, requested_by_user_id, use_type, status, review_note, "
    "reviewed_by_user_id, reviewed_at, submitted_at, files_deleted_at "
    "FROM telephony_number_requests WHERE tenant_id = $1 AND id = $2"
)
_T3 = (
    "SELECT id, kind, file_name, content_type, size_bytes, sha256, scan_status, created_at, "
    "files_deleted_at FROM telephony_number_request_documents "
    "WHERE tenant_id = $1 AND request_id = $2"
)
_T4 = (
    "SELECT id, kind, storage_key, content_type, size_bytes, sha256, wrapped_key, scan_status, "
    "files_deleted_at FROM telephony_number_request_documents "
    "WHERE tenant_id = $1 AND request_id = $2 AND id = $3"
)
_T5 = (
    "UPDATE telephony_number_requests SET status = $3, review_note = $4, reviewed_by_user_id = $5, "
    "reviewed_at = now(), updated_at = now() "
    "WHERE tenant_id = $1 AND id = $2 AND status = 'submitted' "
    "RETURNING id, status, reviewed_at, requested_by_user_id, submitted_at"
)
_T7 = "SELECT email FROM user_profiles WHERE tenant_id = $1 AND id = $2"
_T8 = "SELECT business_name FROM tenants WHERE id = $1"

DIRECTORY_PAGE = 500
_KIND_ORDER = ("business_registration", "business_photo_id", "utility_bill", "personal_photo_id")


def _sort_key(row: dict[str, Any]):
    return (row["submitted_at"], str(row["id"]))


async def list_requests(conn, *, status: str, limit: int, offset: int) -> tuple[list[dict], bool]:
    """Newest first across all accounts. Returns (page, has_more)."""
    want = offset + limit + 1
    best: list[dict] = []
    after: str | None = None
    while True:
        accounts = await conn.fetch(_T0, after, DIRECTORY_PAGE)
        for account in accounts:
            rows = await conn.fetch(_T1, str(account["id"]), status, want)
            for row in rows:
                item = dict(row)
                item["business_name"] = account["business_name"]
                best.append(item)
            best.sort(key=_sort_key, reverse=True)
            del best[want:]
        if len(accounts) < DIRECTORY_PAGE:
            break
        after = str(accounts[-1]["id"])
    page = best[offset:offset + limit]
    return page, len(best) > offset + limit


async def fetch_request_row(conn, tenant_id: str, request_id: str) -> dict | None:
    row = await conn.fetchrow(_T2, str(tenant_id), str(request_id))
    return dict(row) if row is not None else None


async def get_request(conn, tenant_id: str, request_id: str) -> dict | None:
    """The request, its documents' details, the requester's email and the account name."""
    request = await fetch_request_row(conn, tenant_id, request_id)
    if request is None:
        return None
    docs = [dict(d) for d in await conn.fetch(_T3, str(tenant_id), str(request_id))]
    docs.sort(key=lambda d: _KIND_ORDER.index(d["kind"]) if d["kind"] in _KIND_ORDER else 99)
    request["documents"] = docs
    request["requester_email"] = await requester_email(conn, tenant_id, request["requested_by_user_id"])
    request["business_name"] = await conn.fetchval(_T8, str(tenant_id))
    return request


async def get_document(conn, tenant_id: str, request_id: str, document_id: str) -> dict | None:
    row = await conn.fetchrow(_T4, str(tenant_id), str(request_id), str(document_id))
    return dict(row) if row is not None else None


async def decide(conn, *, tenant_id: str, request_id: str, decision: str, note: str | None,
                 reviewer_id: str) -> dict | None:
    """One conditional UPDATE on a submitted request. None means it was not submitted (or absent)."""
    row = await conn.fetchrow(_T5, str(tenant_id), str(request_id), decision, note, str(reviewer_id))
    return dict(row) if row is not None else None


async def requester_email(conn, tenant_id: str, user_id: str) -> str | None:
    return await conn.fetchval(_T7, str(tenant_id), str(user_id))
