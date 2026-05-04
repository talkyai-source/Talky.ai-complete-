# OIDC / OAuth 2.0 integration guide

How to plug Talky.ai into an external identity provider (Auth0, Okta,
Keycloak, Azure AD, Google Workspace, etc.) instead of — or alongside —
the built-in JWT auth.

The backend already issues its own JWTs (see
[`app/core/jwt_security.py`](../backend/app/core/jwt_security.py)). OIDC
adds the option of letting an external IdP authenticate users; we then
mint our own session JWT downstream so the rest of the app is unchanged.

## Decision: front-channel vs back-channel

Two integration shapes — pick one.

### A. Front-channel (Authorization Code + PKCE)
The frontend redirects users to the IdP, the IdP redirects back with a
code, the **backend** exchanges the code for tokens. Best when you want
SSO + central session management.

```
Browser ──► IdP login ──► /auth/oidc/callback ──► Backend
                                                    │
                                                    ▼
                                  validate id_token, upsert user,
                                  issue Talky.ai JWT, set cookie
```

### B. Back-channel (token exchange)
The frontend already has an IdP id_token (e.g. from `next-auth`). It
calls `POST /auth/oidc/exchange` with that token; the backend validates
it against the IdP's JWKS and mints a Talky.ai JWT.

Lower coupling, but you trust the frontend to handle the IdP flow.

## Implementation sketch

Add `authlib==1.3.2` to `requirements.in`, then a new endpoint module
under `app/api/v1/endpoints/oidc.py`:

```python
from authlib.integrations.starlette_client import OAuth
from authlib.jose import jwt as jose_jwt
from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.core.jwt_security import issue_session_token
from app.services.users import upsert_from_oidc_claims

router = APIRouter(prefix="/auth/oidc", tags=["auth"])
oauth = OAuth()

settings = get_settings()
oauth.register(
    name="idp",
    server_metadata_url=settings.oidc_discovery_url,   # e.g. https://idp/.well-known/openid-configuration
    client_id=settings.oidc_client_id,
    client_secret=settings.oidc_client_secret,
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("oidc_callback")
    return await oauth.idp.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="oidc_callback")
async def callback(request: Request):
    token = await oauth.idp.authorize_access_token(request)
    claims = token.get("userinfo") or token["id_token"]
    user = await upsert_from_oidc_claims(claims)
    session_jwt = issue_session_token(user_id=user.id, tenant_id=user.tenant_id)
    return {"access_token": session_jwt, "token_type": "bearer"}
```

Then wire into the main router:

```python
# app/api/v1/router.py
from app.api.v1.endpoints import oidc
api_router.include_router(oidc.router)
```

## Configuration

Add to `backend/.env.example`:

```ini
# --- OIDC (optional) ---
OIDC_ENABLED=false
OIDC_DISCOVERY_URL=https://your-idp.example.com/.well-known/openid-configuration
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
OIDC_REDIRECT_URI=https://api.example.com/api/v1/auth/oidc/callback
```

And to `app/core/config.py` Settings class:

```python
oidc_enabled: bool = False
oidc_discovery_url: str | None = None
oidc_client_id: str | None = None
oidc_client_secret: str | None = None
oidc_redirect_uri: str | None = None
```

`prod_gate.py` should refuse to boot if `OIDC_ENABLED=true` and any of the
others are missing.

## Security notes

- **Always use PKCE** for public/SPA clients (Authlib does this by default
  for the `code` flow).
- **Validate `iss`, `aud`, `exp`, `nbf`** on every id_token (Authlib does
  this when given a `server_metadata_url`).
- **Rotate refresh tokens** on use; revoke on logout.
- **Tenant mapping**: decide how IdP claims map to Talky tenants — usually
  via a custom claim like `tenant_id` or by domain of the verified email.
  Document the mapping; never trust an unverified email.
- **Just-in-time provisioning** is convenient but every JIT-created user is
  a potential auth bypass if the IdP is compromised. Restrict by allowed
  email domains.

## Testing

Unit-test the claim → user upsert without hitting the IdP. Use
[`pytest-httpx`](https://pypi.org/project/pytest-httpx/) to stub the JWKS
endpoint for token validation tests.
