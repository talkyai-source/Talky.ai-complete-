
---

# Debugging Log — 2026-04-06
## Connector OAuth Issues: UUID State Serialization + Google 403 `access_denied`

### Error 1 — Connector authorize flow fails with `Object of type UUID is not JSON serializable`

#### Symptom

When the frontend called `POST /api/v1/connectors/authorize`, the backend returned `500 Internal Server Error`.

Relevant logs:

```text
ERROR [app.infrastructure.connectors.oauth] Failed to create OAuth state: Object of type UUID is not JSON serializable
POST /api/v1/connectors/authorize HTTP/1.1" 500 Internal Server Error
```

The stack trace terminated inside:

```text
app/api/v1/endpoints/connectors.py -> authorize_connector()
app/infrastructure/connectors/oauth.py -> create_state()
```

#### Root Cause

`authorize_connector()` stored `connector_id` from the database in OAuth state `extra_data`.

In the live flow:

- `tenant_id` and `user_id` were already strings
- `connector_id` came back from the DB as a UUID object
- `OAuthStateManager.create_state()` passed the state payload directly into `json.dumps(...)`

That failed before the OAuth redirect URL could be returned.

#### Fix

Added a normalization layer in `backend/app/infrastructure/connectors/oauth.py`:

- `_json_safe()` converts `uuid.UUID` values to strings
- It also normalizes nested dict/list/tuple values so Redis and in-memory storage behave consistently
- `create_state()` now runs the full state payload through `_json_safe()` before `json.dumps(...)`

#### Regression Coverage

Added a unit test in `backend/tests/unit/test_oauth_state.py` that reproduces the exact failure mode:

- `extra_data={"connector_id": uuid.uuid4()}`
- verifies the stored JSON contains the string form of the UUID

#### Verification

Focused backend test run:

```bash
../backend/venv/bin/python -m pytest backend/tests/unit/test_oauth_state.py
```

Result:

- `9 passed`

---

### Error 2 — Google OAuth returns `403 access_denied`

#### Symptom

After the backend state serialization bug was fixed, the Google Calendar connector no longer crashed locally, but Google rejected the authorization request with:

```text
Error 403: access_denied
```

Request details included:

- `redirect_uri=http://localhost:8000/api/v1/connectors/callback`
- `scope=https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events`
- `access_type=offline`
- `prompt=consent`
- `code_challenge_method=S256`

#### What The Backend Actually Sends

The backend uses a unified OAuth callback route in:

- `backend/app/api/v1/endpoints/connectors.py`

The redirect URI is constructed as:

```python
base_url = os.getenv("API_BASE_URL", str(http_request.base_url).rstrip("/"))
redirect_uri = f"{base_url}/api/v1/connectors/callback"
```

The Google Calendar connector requests these scopes in:

- `backend/app/infrastructure/connectors/calendar/google_calendar.py`

```python
[
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]
```

#### Root Cause

This was not a backend runtime failure. Google was rejecting the request itself.

The likely causes are Google Cloud OAuth configuration:

- the OAuth client does not include the exact redirect URI
- the app is still in Testing and the logged-in Google account is not listed as a test user
- Google Calendar API is not enabled in the selected project
- the Workspace admin blocks the app or requested scopes

#### Repo Documentation Mismatch

One repo document still referenced an old Gmail-specific callback path:

```text
http://localhost:8000/api/v1/connectors/gmail/callback
```

But the live connector implementation now uses:

```text
http://localhost:8000/api/v1/connectors/callback
```

This stale documentation could cause the Google OAuth client to be configured with the wrong redirect URI even though the backend request is correct.

#### Resolution Checklist

In Google Cloud Console:

1. Open the OAuth client used by `GOOGLE_CLIENT_ID`
2. Confirm the application type is `Web application`
3. Set `Authorized JavaScript origins` to:
   - `http://localhost:3000`
4. Set `Authorized redirect URIs` to exactly:
   - `http://localhost:8000/api/v1/connectors/callback`
5. Enable `Google Calendar API`
6. Open `OAuth consent screen`
7. If the app is in `Testing`, add the Google account being used under `Test users`
8. If using Google Workspace, verify the admin has not blocked external or unverified apps

#### Notes

- `prompt=consent` is expected here because the app wants a refresh token
- `access_type=offline` is expected for the same reason
- `code_challenge_method=S256` is standard PKCE behavior and not the cause of this error

---

### Files Changed

| File | Change |
|---|---|
| `backend/app/infrastructure/connectors/oauth.py` | Added `_json_safe()` normalization and applied it before storing OAuth state |
| `backend/tests/unit/test_oauth_state.py` | Added regression test for UUID `connector_id` in OAuth state |
| `backend/docs/day_twenty_six_email.md` | Corrected stale OAuth callback documentation to use unified `/api/v1/connectors/callback` |

