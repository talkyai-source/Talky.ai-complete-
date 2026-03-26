# Day 7: Voice Security + Abuse Protection

## Overview

Day 7 implements comprehensive voice/telephony security following telecom industry best practices. It adds a unified Call Guard system that validates all calls before initiation, abuse detection for fraud patterns, and toll fraud protection.

## References

- **CTIA Anti-Fraud Best Practices** (Cellular Telecommunications Industry Association)
- **OWASP Top 10 for Telephony** - https://owasp.org/www-project-voice-security/
- **Twilio Security Guidelines** - https://www.twilio.com/docs/usage/security
- **FCA (UK) Telecom Fraud Guidance** (FG 19/6)

## Components

### 1. Call Guard Service (`app/domain/services/call_guard.py`)

Unified pre-call validation service that checks ALL requirements before allowing call initiation.

**Guard Checks (in priority order):**

| Check | Purpose | Failure Action |
|-------|---------|----------------|
| TENANT_ACTIVE | Prevent calls for suspended/deleted tenants | BLOCK |
| PARTNER_ACTIVE | Prevent calls for suspended partners | BLOCK |
| SUBSCRIPTION_VALID | Enforce billing/payment status | BLOCK |
| FEATURE_ENABLED | Check feature flags from plan | BLOCK |
| NUMBER_VALID | Validate E.164 format | BLOCK |
| GEOGRAPHIC_ALLOWED | Block high-risk countries | BLOCK |
| DNC_CHECK | Do-not-call list validation | BLOCK |
| RATE_LIMIT | Prevent abuse (calls/minute) | THROTTLE |
| CONCURRENCY_LIMIT | Enforce max concurrent calls | QUEUE |
| SPEND_LIMIT | Monthly spend cap enforcement | BLOCK |
| BUSINESS_HOURS | Respect calling time windows | QUEUE |
| VELOCITY_CHECK | Detect call pumping patterns | BLOCK |

**Usage:**
```python
from app.domain.services.call_guard import CallGuard, GuardDecision

guard = CallGuard(db_pool, redis_client)
result = await guard.evaluate(
    tenant_id="tenant-uuid",
    phone_number="+1234567890",
    campaign_id="campaign-uuid",
)

if result.decision == GuardDecision.ALLOW:
    await initiate_call(...)
elif result.decision == GuardDecision.BLOCK:
    # Handle blocked call
    pass
```

### 2. Abuse Detection Service (`app/domain/services/abuse_detection.py`)

Real-time pattern detection for telephony fraud and abuse.

**Detection Types:**

| Type | Description | Severity |
|------|-------------|----------|
| VELOCITY_SPIKE | Sudden volume increase (>3x average) | HIGH |
| SHORT_DURATION_PATTERN | Call pumping (<10 sec calls) | MEDIUM |
| REPEAT_NUMBER | Harassment (3+ calls to same number) | MEDIUM |
| SEQUENTIAL_DIALING | War dialing (sequential numbers) | HIGH |
| PREMIUM_RATE | Premium number abuse | HIGH |
| INTERNATIONAL_SPIKE | Sudden international increase | HIGH |
| WANGIRI | Missed call fraud pattern | HIGH |
| TOLL_FRAUD | IRSF/Known fraud patterns | CRITICAL |

**Usage:**
```python
from app.domain.services.abuse_detection import AbuseDetectionService

detector = AbuseDetectionService(db_pool, redis_client)

# Pre-call check (<50ms)
events = await detector.analyze_call_initiated(tenant_id, phone_number)

# Post-call analysis
await detector.analyze_call_completed(call_id, tenant_id, duration, phone_number)

# Periodic batch analysis
events = await detector.analyze_velocity_patterns()
```

### 3. Database Schema

**Tables:**

- `tenant_call_limits` - Per-tenant call limits and restrictions
- `partner_limits` - Multi-tenant partner aggregate limits
- `abuse_detection_rules` - Configurable fraud detection rules
- `abuse_events` - Audit trail of detected abuse
- `call_guard_decisions` - Audit log of all guard decisions
- `dnc_entries` - Do-not-call list entries
- `call_velocity_snapshots` - Metrics for pattern detection

**Migration:** `database/migrations/day7_voice_security.sql`

### 4. API Endpoints

#### Call Limits Admin (`/api/v1/admin/`)

```
GET    /tenants/{tenant_id}/call-limits
PUT    /tenants/{tenant_id}/call-limits
GET    /partners/{partner_id}/limits
PUT    /partners/{partner_id}/limits
POST   /dnc                      # Add DNC entry
GET    /dnc                      # List DNC entries
DELETE /dnc/{entry_id}           # Remove DNC entry
GET    /call-limits/status       # System status
```

#### Abuse Monitoring (`/api/v1/admin/abuse/`)

```
GET    /events                   # List abuse events
GET    /events/{event_id}        # Get event details
POST   /events/{event_id}/resolve # Resolve event
GET    /statistics               # Abuse statistics
GET    /alerts                   # High-priority alerts
GET    /rules                    # List detection rules
POST   /rules                    # Create rule
PUT    /rules/{rule_id}          # Update rule
DELETE /rules/{rule_id}          # Delete rule
```

#### Telephony Bridge (Modified)

```
POST   /api/v1/sip/telephony/call  # Now CallGuard protected
```

**Response Codes:**
- `200` - Call initiated
- `202` - Call queued (concurrency limit)
- `429` - Call blocked or throttled (rate limit/abuse)

## Configuration

### Tenant Call Limits

```python
{
    "calls_per_minute": 60,
    "calls_per_hour": 1000,
    "calls_per_day": 10000,
    "max_concurrent_calls": 10,
    "max_queue_size": 50,
    "monthly_spend_cap": 1000.00,
    "allowed_country_codes": ["US", "CA", "GB"],
    "blocked_country_codes": ["PK", "NG", "BD"],
    "blocked_prefixes": ["+1900", "+4487"],
    "respect_business_hours": True,
    "business_hours_start": "09:00",
    "business_hours_end": "17:00",
}
```

### Abuse Detection Rules

Default global rules are auto-populated by migration:

| Rule | Type | Action |
|------|------|--------|
| Velocity Spike Detection | velocity_spike | THROTTLE |
| Short Duration Pattern | short_duration | BLOCK |
| Repeat Number | repeat_number | BLOCK |
| Sequential Dialing | sequential_dialing | BLOCK |
| Premium Rate Protection | premium_rate | BLOCK |
| International Spike | international_spike | THROTTLE |
| Toll Fraud Protection | toll_fraud | BLOCK |

## Security Considerations

### Fail-Closed Design

CallGuard defaults to BLOCK if:
- Database connection fails
- Configuration cannot be loaded
- Any check raises an exception

Set `fail_open=True` only in emergencies (not recommended).

### Latency Budget

All guard checks must complete within 100ms:
- Database queries: Cached with 60s TTL
- Redis lookups: <5ms
- Abuse detection: <50ms for synchronous checks

### Audit Trail

Every guard decision is logged to `call_guard_decisions`:
- All checks performed with latency
- Failed checks with reasons
- Total latency
- Decision outcome

### Geographic Intelligence

Default high-risk countries (toll fraud):
- PK (Pakistan)
- BD (Bangladesh)
- NG (Nigeria)
- VN (Vietnam)
- ID (Indonesia)

Premium rate prefixes blocked:
- +1900, +1976 (US)
- +4487, +4498 (UK)
- +339, +338 (France)

## Integration

### Adding CallGuard to New Endpoints

```python
from app.domain.services.call_guard import CallGuard, GuardDecision

async def make_call_endpoint(...):
    guard = CallGuard(db_pool, redis_client)
    result = await guard.evaluate(...)

    if result.decision == GuardDecision.BLOCK:
        raise HTTPException(status_code=429, detail=...)

    # Proceed with call
```

### Periodic Abuse Analysis

Run via cron/job every minute:

```python
async def run_abuse_analysis():
    detector = AbuseDetectionService(db_pool, redis_client)

    # Velocity patterns
    events = await detector.analyze_velocity_patterns()

    # Partner aggregates
    for partner_id in active_partners:
        events = await detector.analyze_partner_aggregate(partner_id)

    # Take actions
    for event in events:
        if event.severity == Severity.CRITICAL:
            await suspend_tenant(event.tenant_id)
```

## Monitoring

### Key Metrics

- `call_guard_decisions_total` - Counter by decision type
- `call_guard_latency_seconds` - Histogram of evaluation latency
- `abuse_events_total` - Counter by severity and type
- `blocked_calls_total` - Counter by block reason

### Alerts

Configure alerts for:
- Spike in blocked calls (>100% increase)
- Unresolved critical abuse events
- CallGuard latency >100ms p99
- Velocity spike events

## Testing

### Unit Tests

```python
# Test call guard
async def test_call_guard_blocks_suspended_tenant():
    guard = CallGuard(db_pool)
    result = await guard.evaluate(
        tenant_id=suspended_tenant_id,
        phone_number="+1234567890",
    )
    assert result.decision == GuardDecision.BLOCK
    assert GuardCheck.TENANT_ACTIVE in result.failed_checks

# Test abuse detection
async def test_detects_sequential_dialing():
    detector = AbuseDetectionService(db_pool)
    events = await detector.analyze_call_initiated(
        tenant_id=tenant_id,
        phone_number="+1234567893",  # Sequential with previous
    )
    assert any(e.abuse_type == AbuseType.SEQUENTIAL_DIALING for e in events)
```

### Integration Tests

```python
async def test_call_endpoint_with_guard():
    response = await client.post(
        "/api/v1/sip/telephony/call",
        params={"destination": "+19001234567"},  # Premium rate
        headers={"X-Tenant-ID": tenant_id},
    )
    assert response.status_code == 429
    assert response.json()["error"] == "call_blocked"
```

## Migration

Apply Day 7 migration:

```bash
psql -U postgres -d talky -f database/migrations/day7_voice_security.sql
```

This creates:
1. All tables with indexes
2. Default global abuse detection rules
3. Triggers for updated_at
4. Comments for documentation

## Future Enhancements

- ML-based anomaly detection
- Real-time carrier blacklist integration
- STIR/SHAKEN attestation support
- Geographic IP/phone mismatch detection
- Automatic tenant suspension workflows
