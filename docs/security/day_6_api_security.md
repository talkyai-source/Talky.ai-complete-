# Day 6: API Security + Rate Limiting

**Implementation Date:** 2026-03-17
**Status:** Complete

## Overview

Day 6 implements comprehensive API protection following OWASP API Security Top 10 2023 guidelines. This builds upon Days 1-5 (authentication, MFA, passkeys, RBAC, session security) to add API-level protections.

## OWASP API Security Coverage

| OWASP Item | Description | Implementation |
|------------|-------------|----------------|
| API4:2023 | Unrestricted Resource Consumption | Tiered rate limiting (L1-L4) |
| API8:2023 | Security Misconfiguration | Webhook HMAC verification |
| API6:2023 | Unrestricted Business Flows | Idempotency key support |
| API10:2023 | Unsafe API Consumption | Request validation, sanitization |

## Components

### 1. Unified Rate Limiting (`app/core/security/api_security.py`)

Four-tier rate limiting system:

| Tier | Scope | Default | Purpose |
|------|-------|---------|---------|
| L1 | IP | 100/min | DDoS protection, brute force |
| L2 | User | 1000/min | Abuse by authenticated users |
| L3 | Tenant | 10,000/min | Resource consumption control |
| L4 | Global | 100,000/min | System overload protection |

**Usage:**
```python
from app.core.security.api_security import rate_limit_dependency

@router.post("/endpoint")
async def endpoint(_=Depends(rate_limit_dependency)):
    ...
```

**Headers in responses:**
- `X-RateLimit-IP-Limit`
- `X-RateLimit-IP-Remaining`
- `X-RateLimit-User-Limit`
- `X-RateLimit-User-Remaining`
- `X-RateLimit-Tenant-Limit`
- `X-RateLimit-Tenant-Remaining`
- `Retry-After` (when blocked)

### 2. Webhook Signature Verification (`app/core/security/webhook_verification.py`)

HMAC-SHA256 signature verification following Stripe's pattern.

**Headers:**
- `X-Webhook-Signature`: HMAC-SHA256 hex signature
- `X-Webhook-Timestamp`: Unix timestamp (replay protection)
- `X-Webhook-Version`: Signature version

**Usage for incoming webhooks:**
```python
from app.core.security.webhook_verification import verify_webhook_request

@router.post("/webhook")
async def webhook(request: Request):
    body = await verify_webhook_request(request, secret="your-secret")
    data = json.loads(body)
    ...
```

**Usage for outgoing webhooks:**
```python
from app.core.security.webhook_verification import create_webhook_signature_headers

headers = create_webhook_signature_headers(payload_bytes, secret)
requests.post(url, data=payload, headers=headers)
```

### 3. Idempotency Support (`app/core/security/idempotency.py`)

True idempotency with response caching.

**Header:** `Idempotency-Key: <8-128 character string>`

**Usage:**
```python
from app.core.security.idempotency import (
    idempotency_dependency,
    store_idempotent_response,
    release_idempotency_lock,
)

@router.post("/charge")
async def charge(
    request: Request,
    idempotency_key: Optional[str] = Depends(idempotency_dependency)
):
    try:
        # Process request
        result = await process_charge()

        # Store for idempotency
        await store_idempotent_response(
            request, 200, json.dumps(result)
        )
        return result
    except Exception:
        await release_idempotency_lock(request)
        raise
```

**Behavior:**
- First request: Process normally
- Duplicate request (same key, same request): Return cached response with `Idempotent-Replay: true` header
- Duplicate key, different request: HTTP 409 Conflict
- Concurrent request with same key: HTTP 409 "already in progress"

### 4. API Security Middleware (`app/core/api_security_middleware.py`)

Automatic request validation and security headers.

**Validations:**
- Request body size (max 10MB)
- Content-Type whitelist
- Suspicious User-Agent blocking

**Security headers added:**
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`

**Blocked User-Agents:**
- sqlmap, nikto, nmap, masscan, zgrab, gobuster, dirbuster

## Database Schema

### Tables Created

```sql
-- Rate limiting audit trail
rate_limit_events (id, tier, scope_key, endpoint, action_taken, limit_config, triggered_at)

-- Webhook configuration
webhook_configs (id, tenant_id, webhook_name, secret_key, signature_algorithm, is_active, created_at)

-- Idempotency backup (Redis is primary)
idempotency_keys (key_hash, tenant_id, user_id, request_method, request_path, response_status, created_at, expires_at)

-- Security event logging
api_security_events (id, tenant_id, user_id, event_type, source_ip, user_agent, request_path, details, created_at)
```

See migration: `database/migrations/day6_api_security.sql`

## Configuration

Environment variables:

```bash
# Rate limiting (all have defaults)
RATE_LIMIT_IP_REQUESTS=100
RATE_LIMIT_USER_REQUESTS=1000
RATE_LIMIT_TENANT_REQUESTS=10000
RATE_LIMIT_GLOBAL_REQUESTS=100000

# Idempotency
IDEMPOTENCY_WINDOW_HOURS=24
IDEMPOTENCY_KEY_MAX_LENGTH=128

# Webhook verification
WEBHOOK_SIGNATURE_HEADER=X-Webhook-Signature
WEBHOOK_TIMESTAMP_HEADER=X-Webhook-Timestamp
WEBHOOK_MAX_AGE_SECONDS=300

# Request validation
MAX_REQUEST_BODY_SIZE=10485760  # 10MB
```

## Integration with Main App

Add to `app/main.py`:

```python
# Day 6: API Security Middleware (before rate limiting)
from app.core.api_security_middleware import APISecurityMiddleware
app.add_middleware(APISecurityMiddleware)

# Note: Idempotency middleware is applied per-endpoint via dependency
# Note: Rate limiting is applied per-endpoint via dependency
```

## Security Considerations

1. **Fail Open**: If Redis is unavailable, rate limiting and idempotency fail open (allow requests) to prevent downtime

2. **Constant-Time Comparison**: Webhook signatures use `secrets.compare_digest()` to prevent timing attacks

3. **Secret Management**: Webhook secrets are:
   - Generated with `secrets.token_urlsafe(32)`
   - Stored per-tenant in database
   - Cached in memory with TTL
   - Returned once during configuration (not retrievable after)

4. **Replay Protection**: Webhook timestamps are validated:
   - Maximum age: 5 minutes
   - Future timestamps rejected
   - Prevents replay attacks

5. **XSS Prevention**: Basic payload sanitization removes:
   - `<script>` tags
   - `javascript:` protocol
   - Event handlers (`onclick=`, etc.)

## Testing

### Rate Limiting Test
```bash
# Make 101 rapid requests (limit is 100/min for IP)
for i in {1..101}; do
    curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/health
done
# Expected: Last request returns 429 Too Many Requests
```

### Webhook Signature Test
```bash
# Generate signature
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
  -H "Content-Type: application/json" \
  -d '{"call_id": "123"}'
```

### Idempotency Test
```bash
# First request - processes
 curl -X POST http://localhost:8000/api/v1/charge \
  -H "Idempotency-Key: unique-key-123" \
  -d '{"amount": 100}'

# Second request with same key - returns cached response
 curl -X POST http://localhost:8000/api/v1/charge \
  -H "Idempotency-Key: unique-key-123" \
  -d '{"amount": 100}'
# Expected: Same response as first request with Idempotent-Replay: true header
```

## Migration Path

To migrate existing webhook endpoints:

1. Add webhook configuration to database:
```sql
INSERT INTO webhook_configs (tenant_id, webhook_name, secret_key)
VALUES ('tenant-uuid', 'webhook_name', 'generated-secret');
```

2. Update webhook sender to include signatures

3. Replace endpoint implementation to use secured version

## References

- [OWASP API Security Top 10 (2023)](https://owasp.org/www-project-api-security/)
- [OWASP Rate Limiting Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Rate_Limiting_Cheat_Sheet.html)
- [Stripe Webhook Signatures](https://stripe.com/docs/webhooks/signatures)
- [NIST SP 800-90B](https://csrc.nist.gov/publications/detail/sp/800-90b/final)
