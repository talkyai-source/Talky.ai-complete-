#!/bin/bash
export PGPASSWORD=ts6Q11cThjBZp3QXvidmaa43hJrUgrlO
DB="psql -h localhost -U talkyai -d talkyai -t -A"

CAMPAIGN_ID="50847cc9-34c4-4b70-962e-197cf953dabd"

echo "=== CAMPAIGN STATUS ==="
$DB -c "SELECT id, tenant_id, name, status, total_leads, started_at, completed_at, created_at FROM campaigns WHERE id = '$CAMPAIGN_ID';"

echo ""
echo "=== TENANT INFO ==="
$DB -c "SELECT t.id, t.email, t.plan_name, t.status FROM tenants t JOIN campaigns c ON c.tenant_id = t.id WHERE c.id = '$CAMPAIGN_ID';"

echo ""
echo "=== DIALER JOB STATUS ==="
$DB -c "SELECT status, count(*) FROM dialer_jobs WHERE campaign_id = '$CAMPAIGN_ID' GROUP BY status ORDER BY count DESC;"

echo ""
echo "=== LEAD STATUS ==="
$DB -c "SELECT l.status, count(*) FROM leads l WHERE l.campaign_id = '$CAMPAIGN_ID' GROUP BY l.status ORDER BY count DESC;"

echo ""
echo "=== RECENT CALLS FOR CAMPAIGN ==="
$DB -c "SELECT cl.status, count(*) FROM calls cl JOIN leads l ON cl.lead_id = l.id WHERE l.campaign_id = '$CAMPAIGN_ID' GROUP BY cl.status ORDER BY count DESC;"

echo ""
echo "=== REDIS QUEUE STATUS ==="
TENANT_ID=$($DB -c "SELECT tenant_id FROM campaigns WHERE id = '$CAMPAIGN_ID';")
echo "Tenant: $TENANT_ID"
redis-cli llen "dialer:tenant:${TENANT_ID}:queue" 2>/dev/null
echo "Priority queue:"
redis-cli llen "dialer:priority:queue" 2>/dev/null
echo "Inflight:"
redis-cli llen "dialer:inflight" 2>/dev/null
echo "Scheduled retry set:"
redis-cli zcard "dialer:scheduled" 2>/dev/null

echo ""
echo "=== DIALER WORKER LOGS (last 20 relevant) ==="
journalctl -u talky-dialer-worker --since "24 hours ago" --no-pager 2>/dev/null | grep -i "50847cc9" | tail -20
echo "(empty = dialer never touched this campaign)"
