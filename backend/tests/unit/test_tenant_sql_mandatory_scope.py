"""Scoped complex filters must not be confused with a boolean scope bypass."""
import pytest

from tests.unit.test_tenant_sql_ratchet import scanner, scan, reader  # noqa: F401


@pytest.mark.parametrize('sql', [
    "INSERT INTO calls(id,tenant_id) VALUES($2,$1::uuid) ON CONFLICT(id) DO NOTHING",
    "INSERT INTO calls(tenant_id,id) VALUES($1,$2) ON CONFLICT(tenant_id,id) DO NOTHING",
    "SELECT id FROM calls WHERE tenant_id=$1 AND is_active IS NOT FALSE",
    "SELECT id FROM calls WHERE tenant_id=$1 AND (status='pending' OR (status='failed' AND attempt_count<5))",
    "UPDATE calls SET status=CASE WHEN $3 THEN 'done' ELSE status END WHERE tenant_id=$1 AND id=$2",
    "UPDATE calls SET accepted_at=CASE WHEN $3 THEN now() ELSE accepted_at END WHERE tenant_id=$1 AND id=$2",
    "UPDATE calls SET status='done' WHERE tenant_id=$1 AND EXISTS (SELECT 1 FROM leads l WHERE l.id=calls.lead_id AND l.tenant_id=$1)",
    "SELECT c.id FROM calls c JOIN leads l ON l.id=c.lead_id AND l.tenant_id=$1 WHERE c.tenant_id=$1 AND (c.lead_id IS NULL OR EXISTS (SELECT 1 FROM leads p WHERE p.id=c.lead_id AND p.tenant_id=$1))",
])
def test_mandatory_scope_survives_unrelated_filters_and_owned_subqueries(scanner, sql):
    findings = scan(scanner, reader(sql))
    assert len(findings) == 1 and findings[0]['scoped'], sql


@pytest.mark.parametrize('sql', [
    "INSERT INTO calls(id,tenant_id) VALUES($2,$1) ON CONFLICT(id) DO UPDATE SET tenant_id=$3",
    "INSERT INTO calls(id,tenant_id) VALUES($2,$1),($3,$4) ON CONFLICT(id) DO NOTHING",
    "INSERT INTO calls(id,tenant_id) VALUES($2,other_tenant()) ON CONFLICT(id) DO NOTHING",
    "SELECT id FROM calls WHERE tenant_id=$1 OR status='pending'",
    "SELECT id FROM calls WHERE NOT(tenant_id=$1)",
    "SELECT id FROM calls WHERE (tenant_id=$1) IS DISTINCT FROM TRUE",
    "SELECT id FROM calls WHERE invert_boolean(tenant_id=$1)",
    "SELECT id FROM calls WHERE state BETWEEN TRUE AND tenant_id=$1 AND status='pending'",
    "UPDATE calls SET tenant_id=$1 WHERE id=$2",
    "UPDATE calls SET status=CASE WHEN tenant_id=$1 THEN 'done' ELSE status END WHERE id=$2",
    "SELECT id FROM calls WHERE EXISTS (SELECT 1 FROM leads l WHERE l.tenant_id=$1)",
    "SELECT id FROM calls WHERE tenant_id=$1 AND EXISTS (SELECT 1 FROM leads l WHERE l.id=calls.lead_id)",
    "SELECT c.id FROM calls c JOIN leads l ON l.id=c.lead_id WHERE c.tenant_id=$1 AND (c.lead_id IS NULL OR c.status='done')",
    "SELECT c.id FROM calls c LEFT JOIN leads l ON l.tenant_id=$1 WHERE c.tenant_id=$1",
    "SELECT id FROM calls WHERE tenant_id=$1 UNION SELECT id FROM calls",
    "SELECT id FROM calls WHERE tenant_id=$1; SELECT id FROM calls",
    "SELECT id FROM calls WHERE (tenant_id=$1 AND status='pending') = FALSE",
])
def test_scope_cannot_be_optional_inverted_or_borrowed_from_a_subquery(scanner, sql):
    findings = scan(scanner, reader(sql))
    assert len(findings) == 1 and not findings[0]['scoped'], sql
