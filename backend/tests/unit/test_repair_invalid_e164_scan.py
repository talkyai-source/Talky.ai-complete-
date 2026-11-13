"""D2-7 (stored data): the one-time scan also finds E.164-shaped invalid numbers.

The repair tool selected only numbers NOT shaped like E.164 (an SQL regex), so
a stored "+12345678901234" or "+999123456789" (no such country) was never
listed, although the dialer now skips it as invalid_phone_number. With
--all-numbers the tool reads every live lead, lists those with the same rule
the dialer uses, and never rewrites them.
"""
from __future__ import annotations

from copy import deepcopy

from tests.unit.test_repair_non_e164_leads import TENANT, Connection, repair, row


class AllNumbersConnection(Connection):
    """Same store; the --all-numbers queries carry no E.164 regex filter."""

    def _candidates(self, sql, tenant):
        flat = " ".join(sql.lower().split())
        assert "tenant_id = $1" in flat and "status <> 'deleted'" in flat
        assert "phone_number !~" not in flat, "--all-numbers must not pre-filter by shape"
        return [r for r in self.rows if r["tenant_id"] == str(tenant)
                and r["status"] is not None and r["status"] != "deleted"]


ROWS = [
    row(101, "+442079460958"),        # valid E.164: nothing to do
    row(102, "+12345678901234"),      # E.164-shaped, too long for +1: invalid
    row(103, "+999123456789"),        # E.164-shaped, no such country: invalid
    row(104, "020 7946 0959"),        # national GB: repairable as before
    row(105, "+447700900124", status="deleted"),
]


async def test_all_numbers_lists_e164_shaped_invalid_numbers_and_writes_nothing():
    conn = AllNumbersConnection(ROWS)
    before = deepcopy(conn.rows)
    result = await repair().repair_tenant(conn, TENANT, batch_size=2, sample_limit=10, all_numbers=True)
    assert result["matched"] == 4
    assert result["valid"] == 1
    assert result["invalid"] == 2
    assert result["proposed"] == 1
    assert conn.rows == before and conn.updates == [] and conn.audit_rows == []
    flagged = {s["lead_id"] for s in result["samples"] if s["action"] == "invalid"}
    assert flagged == {ROWS[1]["id"], ROWS[2]["id"]}
    assert all("+12345678901234" not in str(s) for s in result["samples"]), "numbers stay masked"


async def test_apply_never_rewrites_an_e164_shaped_invalid_number():
    from tests.unit.test_repair_non_e164_leads import Audit

    conn = AllNumbersConnection(ROWS)
    result = await repair().repair_tenant(conn, TENANT, apply=True, audit=Audit(), all_numbers=True)
    assert result["invalid"] == 2 and result["updated"] == 1
    assert [u[1] for u in conn.updates] == [ROWS[3]["id"]], "only the repairable national number changes"


def test_the_default_population_is_unchanged():
    args = repair().build_parser().parse_args([])
    assert args.all_numbers is False
    assert repair().build_parser().parse_args(["--all-numbers"]).all_numbers is True
