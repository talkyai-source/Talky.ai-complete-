# Day 5: Session Security + Device Control

## Overview

Day 5 implements defense-in-depth session security controls following OWASP Session Management and NIST SP 800-63B guidelines. This builds on the server-side session foundation from Day 1.

## Official References

- **OWASP Session Management Cheat Sheet (2024)**
  - https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

- **NIST SP 800-63B: Digital Identity Guidelines - Authentication and Lifecycle Management**
  - https://pages.nist.gov/800-63-3/sp800-63b.html

- **OWASP Device Fingerprinting Cheat Sheet**
  - https://cheatsheetseries.owasp.org/cheatsheets/Device_Fingerprinting_Cheat_Sheet.html

## What Day 5 Adds

### 1. Device Fingerprinting

Generates a unique fingerprint for each device/browser combination using:
- User-Agent string
- Accept headers (content negotiation preferences)
- Client Hints (Sec-Ch-Ua*, modern browsers)
- Accept-Language and Accept-Encoding

**Purpose:** Detect when a session is used from a different device than originally created, indicating potential session hijacking.

**Privacy Note:** All fingerprinting is server-side using standard HTTP headers. No additional client-side tracking is used.

### 2. Session Binding

Sessions can be bound to:
- **IP Address:** Detects when session originates from different IP
- **Device Fingerprint:** Detects browser/device changes

**Configuration:**
```python
SESSION_BIND_TO_IP = True              # Enable IP binding
SESSION_BIND_TO_FINGERPRINT = True     # Enable fingerprint binding
SESSION_STRICT_BINDING = False         # Mark suspicious vs revoke immediately
```

**IP Binding Tolerance:**
- Mobile users may change IPs frequently
- System uses /24 subnet matching (allows same Class C subnet)
- Fingerprint is primary signal for mobile devices

### 3. Suspicious Activity Detection

Sessions are marked suspicious when:
- IP address changes significantly (different /24 subnet)
- Device fingerprint doesn't match
- Both signals are compared and violations logged

**Behavior:**
- `strict_binding=False` (default): Mark suspicious but allow request
- `strict_binding=True`: Revoke session immediately

Users can verify suspicious sessions via the API to clear flags.

### 4. Concurrent Session Limits

Enforces maximum active sessions per user:
```python
MAX_SESSIONS_PER_USER = 10  # Configurable, 0 = unlimited
```

When limit exceeded:
- Oldest sessions (by creation time) are automatically revoked
- New session is created
- Audit trail records `concurrent_limit_exceeded` reason

### 5. Selective Session Revocation

Users can revoke specific sessions by ID:
```http
DELETE /api/v1/sessions/{session_id}
```

Use case: "Log out from my phone" while keeping desktop session active.

### 6. Session Management UI API

New endpoints for session management:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sessions/active` | GET | List all sessions with device info |
| `/sessions/{id}` | DELETE | Revoke specific session |
| `/sessions/verify` | POST | Verify suspicious session |
| `/sessions/security-status` | GET | Get current session security status |

## Database Schema Changes

### New Columns on `security_sessions`

```sql
-- Device fingerprinting
device_fingerprint    TEXT
device_name          TEXT
device_type          TEXT  -- mobile|tablet|desktop|unknown
browser              TEXT
os                   TEXT
country_code         TEXT

-- Session binding
bound_ip             TEXT
ip_binding_enforced  BOOLEAN DEFAULT FALSE
fingerprint_binding_enforced BOOLEAN DEFAULT FALSE

-- Security flags
is_suspicious        BOOLEAN DEFAULT FALSE
suspicious_reason    TEXT
suspicious_detected_at TIMESTAMPTZ
requires_verification BOOLEAN DEFAULT FALSE
verified_at          TIMESTAMPTZ

-- Session tracking
session_number       INTEGER
rotated_from_session_id UUID
is_rotated           BOOLEAN DEFAULT FALSE
```

### New Indexes

```sql
-- Device fingerprint lookups
CREATE INDEX idx_ss_fingerprint ON security_sessions(device_fingerprint);

-- Suspicious session queries
CREATE INDEX idx_ss_suspicious ON security_sessions(user_id, is_suspicious, suspicious_detected_at DESC)
    WHERE is_suspicious = TRUE;

-- Active session binding lookups
CREATE INDEX idx_ss_active_binding ON security_sessions(session_token_hash, bound_ip, device_fingerprint)
    WHERE revoked = FALSE;

-- Session count per user
CREATE INDEX idx_ss_user_session_number ON security_sessions(user_id, session_number DESC)
    WHERE revoked = FALSE;
```

## API Reference

### List Active Sessions

```http
GET /api/v1/sessions/active
Authorization: Bearer {jwt_token}
Cookie: talky_sid={session_cookie}
```

Response:
```json
{
  "sessions": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "device_name": "Chrome on Windows",
      "device_type": "desktop",
      "browser": "chrome",
      "os": "windows",
      "ip_address": "192.168.1.100",
      "is_current": true,
      "is_suspicious": false,
      "requires_verification": false,
      "created_at": "2026-03-17T10:00:00Z",
      "last_active_at": "2026-03-17T14:30:00Z",
      "expires_at": "2026-03-18T10:00:00Z"
    }
  ],
  "total_count": 1,
  "current_session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Revoke Specific Session

```http
DELETE /api/v1/sessions/{session_id}
Authorization: Bearer {jwt_token}
```

Response:
```json
{
  "detail": "Session revoked successfully.",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "revoked": true
}
```

### Verify Suspicious Session

```http
POST /api/v1/sessions/verify
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "confirm_ownership": true
}
```

Response:
```json
{
  "detail": "Session verified successfully. Suspicious flags cleared.",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "verified": true
}
```

### Get Security Status

```http
GET /api/v1/sessions/security-status
Authorization: Bearer {jwt_token}
Cookie: talky_sid={session_cookie}
```

Response:
```json
{
  "is_bound": true,
  "ip_binding": true,
  "fingerprint_binding": true,
  "has_fingerprint": true,
  "is_suspicious": false,
  "requires_verification": false,
  "recommendations": []
}
```

## Architecture

### Session Security Flow

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│   Client    │────▶│  SessionSecurity     │────▶│   Validate      │
│   Request   │     │  Middleware          │     │   Session       │
└─────────────┘     └──────────────────────┘     └─────────────────┘
                             │                            │
                             ▼                            ▼
                    ┌────────────────┐           ┌─────────────────┐
                    │  Generate      │           │  Check Binding  │
                    │  Fingerprint   │           │  IP + Device    │
                    └────────────────┘           └─────────────────┘
                                                          │
                             ┌────────────────────────────┼────┐
                             ▼                            ▼    ▼
                    ┌────────────────┐           ┌──────────┐ ┌──────────┐
                    │   Normal       │           │Suspicious│ │ Revoke   │
                    │   Request      │           │  Flag    │ │ Session  │
                    └────────────────┘           └──────────┘ └──────────┘
```

### Integration Points

1. **Middleware Chain:**
   - CORS Middleware
   - TenantMiddleware (extracts user context)
   - **SessionSecurityMiddleware** (NEW - validates binding)
   - Rate Limiting

2. **Auth Flow:**
   - Login/Register → create_session() with fingerprinting
   - Subsequent requests → validate_session() with binding checks
   - Logout → revoke_session_by_token()

3. **Session Management:**
   - User calls GET /sessions/active → get_active_sessions_detailed()
   - User revokes session → revoke_session_by_id()
   - Suspicious activity → verify_suspicious_session()

## Security Considerations

### False Positives

| Scenario | Handling |
|----------|----------|
| Browser upgrade | Fingerprint changes → Mark suspicious, allow with verification |
| VPN on/off | IP changes → Mark suspicious, allow with verification |
| Mobile network handoff | IP changes within subnet → Allowed |
| Different browser profile | Fingerprint changes → Mark suspicious |

### Privacy

- Device fingerprint is a one-way hash (SHA-256)
- Original values cannot be recovered
- No client-side tracking (canvas, WebGL, etc.)
- User-Agent parsing happens server-side only

### Performance

- Fingerprint generation: ~0.1ms (hash of headers)
- Middleware overhead: ~1-2ms per request
- Database indexes optimized for binding lookups

## Migration

Apply Day 5 migration:
```bash
psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
     -f database/migrations/day5_session_security.sql
```

**Backward Compatibility:**
- Existing sessions work without fingerprint (marked as `has_fingerprint: false`)
- New sessions automatically include fingerprinting
- Gradual adoption on next login

## Configuration

Environment variables:
```bash
# Session binding (all default to True)
SESSION_BIND_TO_IP=true
SESSION_BIND_TO_FINGERPRINT=true
SESSION_STRICT_BINDING=false

# Concurrent session limit
SESSION_MAX_PER_USER=10

# IP subnet tolerance (/24 allows same Class C subnet)
SESSION_IP_BINDING_TOLERANCE=/24
```

## Monitoring

Security events logged:
```python
logger.warning(
    "Suspicious session activity detected",
    extra={
        "security_event": "suspicious_session",
        "session_id": "...",
        "user_id": "...",
        "suspicious_reason": "ip_mismatch",
        "ip_address": "...",
    }
)
```

Integration points for SIEM:
- Suspicious session events
- Concurrent limit enforcement
- Mass session revocation

## Future Enhancements

Day 5 builds foundation for:
- **Day 6:** Audit logging with security event stream
- **Day 7:** Anomaly detection ML model
- **Day 8:** Continuous authentication with behavioral biometrics

## Checklist

- [ ] Migration applied successfully
- [ ] New sessions have device_fingerprint populated
- [ ] GET /sessions/active returns device info
- [ ] DELETE /sessions/{id} revokes specific session
- [ ] IP change marks session suspicious (not revoked)
- [ ] Verification clears suspicious flags
- [ ] Concurrent limit enforced (test with 11 sessions)
- [ ] Security events logged
- [ ] Documentation complete
