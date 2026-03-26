# Day 6: API Security + Rate Limiting

## Overview

Day 6 implements comprehensive API protection following OWASP API Security Top 10 2023 guidelines. This builds upon Days 1-5 (authentication, MFA, passkeys, RBAC, session security) to add API-level protections against abuse, DDoS, replay attacks, and webhook spoofing.

## Official References

- **OWASP API Security Top 10 (2023)**
  - https://owasp.org/www-project-api-security/
  - API1:2023 - Broken Object Level Authorization
  - API2:2023 - Broken Authentication
  - API4:2023 - Unrestricted Resource Consumption (Rate Limiting)
  - API6:2023 - Unrestricted Access to Sensitive Business Flows
  - API8:2023 - Security Misconfiguration
  - API10:2023 - Unsafe Consumption of APIs

- **OWASP Rate Limiting Cheat Sheet**
  - https://cheatsheetseries.owasp.org/cheatsheets/Rate_Limiting_Cheat_Sheet.html

- **Stripe Webhook Signatures** (industry standard)
  - https://stripe.com/docs/webhooks/signatures

- **NIST SP 800-90B - Recommendation for the Entropy Sources**
  - https://csrc.nist.gov/publications/detail/sp/800-90b/final

## What Day 6 Adds

### 1. Tiered Rate Limiting (L1-L4)

Four-tier rate limiting system protecting against different attack vectors:

| Tier | Scope | Default | Purpose |
|------|-------|---------|---------|
| L1 | IP | 100/min | DDoS protection, brute force |
| L2 | User | 1000/min | Abuse by authenticated users |
| L3 | Tenant | 10,000/min | Resource consumption control |
| L4 | Global | 100,000/min | System overload protection |

**Algorithm:** Sliding window with Redis sorted sets

**Features:**
- Automatic blocking with configurable duration per tier
- Rate limit headers in all responses
- Graceful degradation (fail open if Redis unavailable)
- Per-endpoint rate limiting support

### 2. Webhook HMAC-SHA256 Verification

Following Stripe's webhook verification pattern:

**Headers:**
- `X-Webhook-Signature`: HMAC-SHA256 hex signature
- `X-Webhook-Timestamp`: Unix timestamp (replay protection)
- `X-Webhook-Version`: Signature version

**Security Features:**
- Constant-time signature comparison (prevents timing attacks)
- 5-minute timestamp tolerance (replay protection)
- Per-tenant webhook secrets
- Automatic secret rotation support

### 3. API-Wide Idempotency

True idempotency with response caching:

**Header:** `Idempotency-Key: <8-128 character string>`

**Behavior:**
- First request: Process normally
- Duplicate request (same key, same request): Return cached response with `Idempotent-Replay: true`
- Duplicate key, different request: HTTP 409 Conflict
- Concurrent request with same key: HTTP 409 "already in progress"

**Storage:**
- Redis primary (fast lookup, 24-hour TTL)
- PostgreSQL backup (durability across cache restarts)

### 4. API Security Middleware

Automatic request validation and security headers:

**Validations:**
- Request body size limit (10MB)
- Content-Type whitelist (json, form-data, multipart, text)
- Suspicious User-Agent blocking

**Security Headers Added:**
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`

**Blocked User-Agents:** sqlmap, nikto, nmap, masscan, zgrab, gobuster, dirbuster

## Database Schema Changes

### New Tables

```sql
-- Rate limiting audit trail
rate_limit_events (
    id UUID PRIMARY KEY,
    tier TEXT NOT NULL CHECK (tier IN ('ip', 'user', 'tenant', 'global')),
    scope_key TEXT NOT NULL,  -- IP, user_id, or tenant_id
    endpoint TEXT,
    action_taken TEXT NOT NULL,
    limit_config JSONB,
    triggered_at TIMESTAMPTZ DEFAULT NOW()
);

-- Webhook configuration (per-tenant)
webhook_configs (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    webhook_name TEXT NOT NULL,
    secret_key TEXT NOT NULL,
    signature_algorithm TEXT DEFAULT 'hmac-sha256',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, webhook_name)
);

-- Idempotency backup (Redis is primary)
idempotency_keys (
    key_hash TEXT PRIMARY KEY,
    tenant_id UUID,
    user_id UUID,
    request_method TEXT NOT NULL,
    request_path TEXT NOT NULL,
    request_body_hash TEXT,
    response_status INTEGER,
    response_body_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- API security event logging
api_security_events (
    id UUID PRIMARY KEY,
    tenant_id UUID,
    user_id UUID,
    event_type TEXT NOT NULL,  -- 'webhook_verify_failed', etc.
    source_ip INET,
    user_agent TEXT,
    request_path TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### New Indexes

```sql
-- Rate limit event queries
CREATE INDEX idx_rate_limit_events_scope
    ON rate_limit_events(tier, scope_key, triggered_at DESC);

-- Webhook lookup
CREATE INDEX idx_webhook_configs_tenant
    ON webhook_configs(tenant_id, webhook_name) WHERE is_active = TRUE;

-- Idempotency cleanup
CREATE INDEX idx_idempotency_keys_expires
    ON idempotency_keys(expires_at);

-- Security event queries
CREATE INDEX idx_api_security_events_tenant_time
    ON api_security_events(tenant_id, event_type, created_at DESC);
```

## API Reference

### Rate Limited Endpoint

```python
from app.core.security import rate_limit_dependency

@router.post("/charge")
async def charge(_=Depends(rate_limit_dependency)):
    # This endpoint is rate limited on all tiers
    ...
```

**Response Headers:**
```http
X-RateLimit-IP-Limit: 100
X-RateLimit-IP-Remaining: 95
X-RateLimit-User-Limit: 1000
X-RateLimit-User-Remaining: 999
X-RateLimit-Tenant-Limit: 10000
X-RateLimit-Tenant-Remaining: 9998
```

**When Blocked (429):**
```http
Retry-After: 300
X-RateLimit-IP-Limit: 100
```

### Webhook Signature Verification

**Incoming Webhook (Receiving):**
```python
from app.core.security import verify_webhook_request

@router.post("/webhook/inbound")
async def inbound_webhook(request: Request):
    body = await verify_webhook_request(request, secret="whsec_...")
    data = json.loads(body)
    ...
```

**Outgoing Webhook (Sending):**
```python
from app.core.security import create_webhook_signature_headers

headers = create_webhook_signature_headers(payload_bytes, secret)
requests.post(url, data=payload, headers=headers)
```

**Headers Sent:**
```http
X-Webhook-Signature: a1b2c3d4...
X-Webhook-Timestamp: 1742235600
X-Webhook-Version: v1
```

### Idempotent Request

```python
from app.core.security import idempotency_dependency, store_idempotent_response

@router.post("/payment")
async def create_payment(
    request: Request,
    idempotency_key: Optional[str] = Depends(idempotency_dependency)
):
    try:
        # Process payment
        result = await process_payment()

        # Store for idempotency
        await store_idempotent_response(
            request, 200, json.dumps(result)
        )
        return result
    except Exception:
        from app.core.security import release_idempotency_lock
        await release_idempotency_lock(request)
        raise
```

**Client Request:**
```http
POST /api/v1/payment
Idempotency-Key: unique-key-123
Content-Type: application/json

{"amount": 100, "currency": "USD"}
```

**Duplicate Response:**
```http
200 OK
Idempotent-Replay: true

{"status": "processed", "id": "pi_123"}
```

### Secured Webhook Endpoint

```http
POST /api/v1/webhooks/secure/call/goal-achieved
X-Webhook-Signature: <hmac-signature>
X-Webhook-Timestamp: <unix-timestamp>
X-Tenant-ID: <tenant-uuid>
Content-Type: application/json

{"call_id": "12345"}
```

## Architecture

### Rate Limiting Flow

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   Request   │────▶│ Rate Limit          │────▶│ Check L1 (IP)   │
│             │     │ Middleware          │     │ 100/min         │
└─────────────┘     └─────────────────────┘     └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ Check L2 (User) │
                    │ 1000/min        │
                    └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ Check L3        │
                    │ (Tenant)        │
                    │ 10k/min         │
                    └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ Add Headers     │
                    │ or Block (429)  │
                    └─────────────────┘
```

### Webhook Verification Flow

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│ Webhook     │────▶│ Extract Headers     │────▶│ Validate        │
│ POST        │     │ - Signature         │     │ Timestamp       │
│             │     │ - Timestamp         │     │ (max 5min old)  │
└─────────────┘     └─────────────────────┘     └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ Compute HMAC    │
                    │ with Secret     │
                    └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ Constant-Time   │
                    │ Compare         │
                    └─────────────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
            ┌──────────┐          ┌──────────┐
            │ Success  │          │ 401 Fail │
            │ Process  │          │ Log Event│
            └──────────┘          └──────────┘
```

### Middleware Chain

```
CORS Middleware
    ↓
TenantMiddleware (extracts user/tenant context)
    ↓
SessionSecurityMiddleware (Day 5)
    ↓
APISecurityMiddleware (Day 6) ← NEW
    ↓
Rate Limit Dependency (per-endpoint)
    ↓
Idempotency Dependency (per-endpoint)
    ↓
Route Handler
```

## Security Considerations

### Fail Open Design

If Redis is unavailable:
- Rate limiting allows requests (returns `ALLOW` with `reason: redis_unavailable`)
- Idempotency allows requests (processes normally)
- **Rationale:** Availability is prioritized; monitoring should alert on Redis failure

### Timing Attack Prevention

All HMAC comparisons use `secrets.compare_digest()`:
```python
is_valid = secrets.compare_digest(
    signature_header.lower(),
    expected_sig.lower()
)
```

This ensures constant-time comparison regardless of where signatures differ.

### Webhook Secret Management

- Generated with `secrets.token_urlsafe(32)` (256-bit entropy)
- Stored per-tenant in database
- Cached in memory with 5-minute TTL
- Returned once during configuration (not retrievable after)
- Can be rotated by updating `webhook_configs` table

### XSS Prevention

Basic payload sanitization available:
```python
from app.core.api_security_middleware import sanitize_json_value

clean_data = sanitize_json_value(user_input)
# Removes: <script> tags, javascript: protocol, event handlers
```

## Configuration

Environment variables:

```bash
# Rate limiting (all have defaults)
RATE_LIMIT_IP_REQUESTS=100          # L1: Per IP
RATE_LIMIT_USER_REQUESTS=1000       # L2: Per user
RATE_LIMIT_TENANT_REQUESTS=10000    # L3: Per tenant
RATE_LIMIT_GLOBAL_REQUESTS=100000   # L4: System-wide

# Block durations (seconds)
RATE_LIMIT_IP_BLOCK=300             # 5 min
RATE_LIMIT_USER_BLOCK=600           # 10 min
RATE_LIMIT_TENANT_BLOCK=900         # 15 min
RATE_LIMIT_GLOBAL_BLOCK=60          # 1 min

# Idempotency
IDEMPOTENCY_WINDOW_HOURS=24
IDEMPOTENCY_KEY_MAX_LENGTH=128

# Webhook verification
WEBHOOK_SIGNATURE_HEADER=X-Webhook-Signature
WEBHOOK_TIMESTAMP_HEADER=X-Webhook-Timestamp
WEBHOOK_MAX_AGE_SECONDS=300         # 5 minutes

# Request validation
MAX_REQUEST_BODY_SIZE=10485760      # 10MB
```

## Migration

Apply Day 6 migration:
```bash
psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
     -f database/migrations/day6_api_security.sql
```

**Backward Compatibility:**
- Existing endpoints work without rate limiting (opt-in via dependency)
- Existing webhooks continue to work (new secured endpoints at `/webhooks/secure/*`)
- Idempotency is opt-in via header

## Monitoring

Security events logged:
```python
# Rate limit exceeded
logger.warning(
    f"Rate limit exceeded ({tier.value} tier)",
    extra={"security_event": "rate_limit_exceeded", "tier": tier.value}
)

# Webhook verification failed
logger.warning(
    "Webhook verification failed",
    extra={"security_event": "webhook_verify_failed", "tenant_id": ...}
)

# Suspicious User-Agent blocked
logger.warning(f"Suspicious User-Agent blocked: {user_agent}")
```

SIEM integration points:
- Rate limit violations by IP/user/tenant
- Webhook verification failures
- Idempotency conflicts (possible replay attacks)
- Suspicious user agent blocks

## Testing

### Rate Limiting Test
```bash
# Make 101 rapid requests (limit is 100/min for IP)
for i in {1..101}; do
    curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/health
done
# Expected: Last request returns 429
```

### Webhook Signature Test
```bash
# Generate test signature
python3 << 'EOF'
import hmac, hashlib, time
timestamp = str(int(time.time()))
payload = b'{"call_id": "123"}'
secret = "whsec_test_secret"
signed = f"{timestamp}.".encode() + payload
signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
print(f"Signature: {signature}")
print(f"Timestamp: {timestamp}")
EOF

# Send request
curl -X POST http://localhost:8000/api/v1/webhooks/secure/call/goal-achieved \
  -H "X-Webhook-Signature: <signature>" \
  -H "X-Webhook-Timestamp: <timestamp>" \
  -H "X-Tenant-ID: <tenant-id>" \
  -d '{"call_id": "123"}'
```

### Idempotency Test
```bash
# First request - processes
curl -X POST http://localhost:8000/api/v1/charge \
  -H "Idempotency-Key: unique-key-123" \
  -d '{"amount": 100}'

# Second request - returns cached
curl -X POST http://localhost:8000/api/v1/charge \
  -H "Idempotency-Key: unique-key-123" \
  -d '{"amount": 100}'
# Expected: Same response with Idempotent-Replay: true header
```

## Future Enhancements

Day 6 builds foundation for:
- **Day 7:** Anomaly detection with rate limit patterns
- **Day 8:** Continuous authentication with request behavior analysis
- **Day 9:** Advanced DDoS protection with upstream filtering

## Checklist

- [ ] Migration applied successfully
- [ ] Rate limit headers present in responses
- [ ] 429 returned after exceeding rate limit
- [ ] Webhook signature verification accepts valid signatures
- [ ] Webhook signature verification rejects invalid signatures
- [ ] Idempotency returns cached response for duplicate keys
- [ ] Idempotency returns 409 for key reuse with different request
- [ ] Security headers present in all responses
- [ ] Large requests (>10MB) rejected with 413
- [ ] Suspicious User-Agents blocked with 403
- [ ] Python syntax passes on all files
- [ ] Documentation complete
