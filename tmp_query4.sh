#!/bin/bash
export PGPASSWORD=ts6Q11cThjBZp3QXvidmaa43hJrUgrlO
DB="psql -h localhost -U talkyai -d talkyai -t -A"

CAMPAIGN_ID="50847cc9-34c4-4b70-962e-197cf953dabd"

echo "=== REDIS PASSWORD ==="
REDIS_PASS=$(grep REDIS_URL /opt/talky/backend/.env 2>/dev/null | sed 's/.*:\/\/:\([^@]*\)@.*/\1/')
echo "Redis password: $REDIS_PASS"

echo ""
echo "=== REDIS QUEUE STATUS ==="
redis-cli -a "$REDIS_PASS" llen "dialer:tenant:790ca2db-6696-4fe9-9a2c-cd690c414a1e:queue" 2>/dev/null
echo "Priority queue:"
redis-cli -a "$REDIS_PASS" llen "dialer:priority:queue" 2>/dev/null
echo "Inflight:"
redis-cli -a "$REDIS_PASS" llen "dialer:inflight" 2>/dev/null
echo "Scheduled retry set:"
redis-cli -a "$REDIS_PASS" zcard "dialer:scheduled" 2>/dev/null

echo ""
echo "=== ALL ACTIVE TENANTS (running campaigns) ==="
$DB -c "SELECT DISTINCT tenant_id FROM campaigns WHERE status IN ('running', 'active');"

echo ""
echo "=== ALL RUNNING CAMPAIGNS ==="
$DB -c "SELECT id, tenant_id, name, status, total_leads, started_at, completed_at FROM campaigns WHERE status IN ('running', 'active');"

echo ""
echo "=== DIALER WORKER LAST 30 LINES (today only) ==="
journalctl -u talky-dialer-worker --since "2026-07-21 09:00:00" --no-pager 2>/dev/null | grep -viE "heartbeat" | tail -30

echo ""
echo "=== CAMPAIGN COMPLETED_AT BEFORE STARTED_AT ANALYSIS ==="
$DB -c "SELECT id, name, status, started_at, completed_at, 
  CASE WHEN completed_at < started_at THEN 'ANOMALY: completed before started' ELSE 'ok' END as ts_check
  FROM campaigns WHERE completed_at IS NOT NULL AND completed_at < started_at;"

echo ""
echo "=== CONTACT LIST TOGGLE STATUS ==="
$DB -c "SELECT cl.id, cl.name, cl.is_active, cl.tenant_id FROM contact_lists cl 
  WHERE cl.tenant_id = '790ca2db-6696-4fe9-9a2c-cd690c414a1e';"

echo ""
echo "=== LEADS - ALL STATUSES ==="
$DB -c "SELECT l.status, count(*) as cnt FROM leads l WHERE l.campaign_id='$CAMPAIGN_ID' GROUP BY l.status;"

echo ""
echo "=== CHECK IF LEAD 6bbaba43 IS DELETED FROM CONTACT LISTS ==="
$DB -c "SELECT l.id, l.phone_number, l.status, l.contact_list_id FROM leads l WHERE l.id = '6bbaba43-43c4-4eeb-bf67-a16d8d0e3b5d';"

echo ""
echo "=== CHECK IF LEAD a320feb0 - LAST JOB STATUS ==="
$DB -c "SELECT dj.status, dj.last_outcome, dj.failure_category, dj.failure_reason, dj.completed_at 
  FROM dialer_jobs dj WHERE dj.lead_id = 'a320feb0-0329-44f1-9157-434d8972f44d' AND dj.campaign_id='$CAMPAIGN_ID';"
