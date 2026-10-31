"""cancel_active_jobs_for_lead must be tenant-scoped (prod DB role bypasses RLS)."""
from app.domain.services.dialer.job_lifecycle import cancel_active_jobs_for_lead
from app.domain.services.dialer import job_states as st


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.in_filters = []

    def update(self, vals):
        self.vals = vals
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, vals):
        self.in_filters.append((col, vals))
        return self

    def execute(self):
        hit = [
            r for r in self.rows
            if all(r.get(c) == v for c, v in self.filters)
            and all(r.get(c) in vs for c, vs in self.in_filters)
        ]
        for r in hit:
            r.update(self.vals)
        return _Res([{"id": r["id"]} for r in hit])


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "dialer_jobs"
        return _Query(self.rows)


def _rows():
    s = st.ACTIVE_STATUSES[0]
    return [
        {"id": 1, "lead_id": "L1", "tenant_id": "A", "status": s},
        {"id": 2, "lead_id": "L1", "tenant_id": "B", "status": s},
    ]


def test_other_tenants_job_with_same_lead_id_is_not_cancelled():
    rows = _rows()
    n = cancel_active_jobs_for_lead(_DB(rows), "L1", tenant_id="A")
    assert n == 1
    assert rows[0]["status"] == "cancelled"
    assert rows[1]["status"] != "cancelled"


def test_wrong_tenant_cancels_nothing():
    rows = _rows()
    assert cancel_active_jobs_for_lead(_DB(rows), "L1", tenant_id="C") == 0
    assert all(r["status"] != "cancelled" for r in rows)
