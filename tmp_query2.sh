#!/bin/bash
export PGPASSWORD=ts6Q11cThjBZp3QXvidmaa43hJrUgrlO
DB="psql -h localhost -U talkyai -d talkyai -t -A"

CAMPAIGN_ID="50847cc9-34c4-4b70-962e-197cf953dabd"

echo "=== TIMESTAMPS ANALYSIS ==="
$DB -c "SELECT 'created_at' as field, created_at FROM campaigns WHERE id='$CAMPAIGN_ID'
UNION ALL SELECT 'started_at', started_at FROM campaigns WHERE id='$CAMPAIGN_ID'
UNION ALL SELECT 'completed_at', completed_at FROM campaigns WHERE id='$CAMPAIGN_ID';"

echo ""
echo "=== FAILED JOBS DETAIL ==="
$DB -c "SELECT job_id, status, lead_id, attempt_number, last_error, fail_category, created_at, updated_at FROM dialer_jobs WHERE campaign_id='$CAMPAIGN_ID' AND status='failed' ORDER BY updated_at;"

echo ""
echo "=== CANCELLED JOBS DETAIL ==="
$DB -c "SELECT job_id, status, lead_id, attempt_number, created_at, updated_at FROM dialer_jobs WHERE campaign_id='$CAMPAIGN_ID' AND status='cancelled' ORDER BY updated_at;"

echo ""
echo "=== COMPLETED JOBS DETAIL ==="
$DB -c "SELECT job_id, status, lead_id, attempt_number, created_at, updated_at FROM dialer_jobs WHERE campaign_id='$CAMPAIGN_ID' AND status='completed' ORDER BY updated_at;"

echo ""
echo "=== ALL JOBS FULL DETAIL ==="
$DB -c "SELECT job_id, status, lead_id, attempt_number, priority, last_error, fail_category, scheduled_for, created_at, updated_at FROM dialer_jobs WHERE campaign_id='$CAMPAIGN_ID' ORDER BY created_at;"

echo ""
echo "=== LEADS DETAIL ==="
$DB -c "SELECT id, phone_number, status, last_called_at, created_at FROM leads WHERE campaign_id='$CAMPAIGN_ID' ORDER BY created_at;"

echo ""
echo "=== CALLS DETAIL ==="
$DB -c "SELECT cl.id, cl.status, cl.direction, cl.duration_seconds, cl.started_at, cl.ended_at, cl.error_message FROM calls cl JOIN leads l ON cl.lead_id = l.id WHERE l.campaign_id='$CAMPAIGN_ID' ORDER BY cl.started_at;"

echo ""
echo "=== CALL EVENTS ==="
$DB -c "SELECT ce.event_type, ce.created_at, ce.details FROM call_events ce JOIN calls cl ON ce.call_id = cl.id JOIN leads l ON cl.lead_id = l.id WHERE l.campaign_id='$CAMPAIGN_ID' ORDER BY ce.created_at LIMIT 30;"

echo ""
echo "=== REDIS CHECK (with auth) ==="
redis-cli -a talky_redis_password llen "dialer:tenant:790ca2db-6696-4fe9-9a2c-cd690c414a1e:queue" 2>/dev/null
echo "Priority queue:"
redis-cli -a talky_redis_password llen "dialer:priority:queue" 2>/dev/null
echo "Inflight:"
redis-cli -a talky_redis_password llen "dialer:inflight" 2>/dev/null

echo ""
echo "=== TENANT CALLING CONFIG ==="
$DB -c "SELECT * FROM calling_config WHERE tenant_id='790ca2db-6696-4fe9-9a2c-cd690c414a1e';"

echo ""
echo "=== CAMPAIGN CALLING CONFIG ==="
$DB -c "SELECT calling_config FROM campaigns WHERE id='$CAMPAIGN_ID';"

echo ""
echo "=== MINUTES / BILLING ==="
$DB -c "SELECT tenant_id, minutes_used, minutes_limit, period_start, period_end FROM tenant_minutes WHERE tenant_id='790ca2db-6696-4fe9-9a2c-cd690c414a1e' ORDER BY period_start DESC LIMIT 3;"
