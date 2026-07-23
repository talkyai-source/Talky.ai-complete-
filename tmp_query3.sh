#!/bin/bash
export PGPASSWORD=ts6Q11cThjBZp3QXvidmaa43hJrUgrlO
DB="psql -h localhost -U talkyai -d talkyai -t -A"

CAMPAIGN_ID="50847cc9-34c4-4b70-962e-197cf953dabd"

echo "=== DIALER_JOBS TABLE SCHEMA ==="
$DB -c "\d dialer_jobs" 2>/dev/null | head -30

echo ""
echo "=== ALL DIALER JOBS ==="
$DB -c "SELECT * FROM dialer_jobs WHERE campaign_id='$CAMPAIGN_ID' ORDER BY created_at;"

echo ""
echo "=== ALL CALLS ==="
$DB -c "SELECT * FROM calls WHERE lead_id IN (SELECT id FROM leads WHERE campaign_id='$CAMPAIGN_ID') ORDER BY created_at;"

echo ""
echo "=== CALL EVENTS ==="
$DB -c "SELECT * FROM call_events WHERE call_id IN (SELECT id FROM calls WHERE lead_id IN (SELECT id FROM leads WHERE campaign_id='$CAMPAIGN_ID')) ORDER BY created_at LIMIT 30;"

echo ""
echo "=== REDIS PASSWORD FROM ENV ==="
grep REDIS /opt/talky/backend/.env 2>/dev/null | head -5

echo ""
echo "=== REDIS AUTH ATTEMPT ==="
redis-cli -a "$(grep REDIS_PASSWORD /opt/talky/backend/.env 2>/dev/null | cut -d= -f2)" llen "dialer:inflight" 2>/dev/null
redis-cli -a "$(grep REDIS_PASSWORD /opt/talky/backend/.env 2>/dev/null | cut -d= -f2)" llen "dialer:tenant:790ca2db-6696-4fe9-9a2c-cd690c414a1e:queue" 2>/dev/null

echo ""
echo "=== TENANTS TABLE SCHEMA ==="
$DB -c "\d tenants" 2>/dev/null | head -30

echo ""
echo "=== TENANT INFO ==="
$DB -c "SELECT * FROM tenants WHERE id='790ca2db-6696-4fe9-9a2c-cd690c414a1e';" 2>/dev/null

echo ""
echo "=== DIALER WORKER LAST 50 LINES (non-heartbeat) ==="
journalctl -u talky-dialer-worker --since "48 hours ago" --no-pager 2>/dev/null | grep -viE "heartbeat" | tail -50
