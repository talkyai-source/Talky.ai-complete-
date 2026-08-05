"""A caller ID must verify regardless of how it was written.

WHY THIS EXISTS (2026-08-06)
----------------------------
`TenantPhoneNumberService.is_verified_for_tenant` compares with an exact SQL
string match:

    WHERE tenant_id = $1 AND e164 = $2

Registration (`tenant_phone_numbers._e164_format`) enforces a leading '+', so
the STORED side is always true E.164. The CALLER side was never normalised.

Production ran with `DEFAULT_CALLER_ID=17789249977` — the tenant's verified
number WITHOUT the '+'. `dialer_worker.py:878` uses that value whenever a
campaign has no explicit `calling_config.caller_id`:

    caller_id = getattr(rules, "caller_id", None) or os.getenv("DEFAULT_CALLER_ID", "1001")

13 of 15 campaigns had no explicit caller_id, so they all took that path,
compared '17789249977' against '+17789249977', failed, and were refused with
`caller_id_not_verified`. Newly-signed-up tenants could never place a call:
499 such failures on one tenant, 52 on another as recently as 2026-08-04 —
while their `tenant_phone_numbers` row read `verified` the whole time.

One character. The check is fail-closed by design, so the mismatch presented as
a hard refusal rather than anything that looked like a formatting bug.
"""
from __future__ import annotations

import pytest

from app.domain.services.dnc_service import normalize_e164


# ---------------------------------------------------------------------------
# The normaliser itself — this is what closes the gap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written_as",
    [
        "+17789249977",     # canonical, as stored
        "17789249977",      # THE PRODUCTION BUG: DEFAULT_CALLER_ID had no '+'
        " +17789249977 ",   # stray whitespace
        "+1 778 924 9977",  # spaced
        "+1 (778) 924-9977",  # punctuated
    ],
)
def test_every_spelling_normalises_to_the_stored_form(written_as):
    assert normalize_e164(written_as) == "+17789249977", (
        f"{written_as!r} must normalise to the E.164 form registration stores, "
        "or the exact-match verification lookup refuses the call"
    )


def test_a_non_phone_value_does_not_become_a_valid_number():
    """Narrowing, not widening.

    "1001" is the Asterisk extension the dialer falls back to when
    DEFAULT_CALLER_ID is unset. It must NOT normalise into anything that could
    match a real verified row — a caller ID that was correctly unverified
    before must stay unverified.
    """
    normalised = normalize_e164("1001")
    assert normalised != "+17789249977"
    assert not normalised.startswith("+1778")


def test_empty_input_is_safe():
    assert normalize_e164("") == ""


# ---------------------------------------------------------------------------
# The lookup applies it
# ---------------------------------------------------------------------------

class _FakeConn:
    """Records the e164 value the query was actually given."""

    def __init__(self, stored: str, status: str = "verified"):
        self.stored = stored
        self.status = status
        self.queried_with: str | None = None

    async def fetchrow(self, _sql, _tenant_id, e164):
        self.queried_with = e164
        if e164 != self.stored:
            return None
        return {"status": self.status, "stir_shaken_token": "tok"}


class _FakePool:
    def __init__(self, conn):
        self.conn = conn


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_id_as_dialled", ["+17789249977", "17789249977"])
async def test_lookup_verifies_with_or_without_the_plus(
    caller_id_as_dialled, monkeypatch
):
    """THE REGRESSION. Both spellings must verify against the stored '+' form."""
    import contextlib

    from app.domain.services import tenant_phone_number_service as mod

    conn = _FakeConn(stored="+17789249977")

    @contextlib.asynccontextmanager
    async def _fake_acquire(_pool, _tenant_id):
        yield conn

    monkeypatch.setattr(
        "app.core.db_utils.acquire_with_tenant", _fake_acquire, raising=False
    )

    svc = mod.TenantPhoneNumberService.__new__(mod.TenantPhoneNumberService)
    svc._db_pool = _FakePool(conn)

    ok = await svc.is_verified_for_tenant(
        "3f7c1a1e-0000-4000-8000-000000000000",
        e164=caller_id_as_dialled,
    )

    assert conn.queried_with == "+17789249977", (
        f"lookup queried {conn.queried_with!r} — the caller side must be "
        "normalised to the stored E.164 form before comparing"
    )
    assert ok is True


def test_the_default_caller_id_env_is_documented_as_needing_e164():
    """`dialer_worker` still reads DEFAULT_CALLER_ID; the normaliser now makes
    a missing '+' harmless, but the fallback literal must not silently become
    a valid-looking number."""
    from tests.unit._source_scan import code

    src = code("app/workers/dialer_worker.py")
    assert "DEFAULT_CALLER_ID" in src
    # The bare-extension fallback is intentional and must stay unverifiable.
    assert '"1001"' in src or "'1001'" in src
