"""
Salesforce CRM Connector
OAuth 2.0 web-server flow (with PKCE) + REST API integration.

What this connector does for Talky.ai
-------------------------------------
* OAuth against ``login.salesforce.com`` (or a sandbox / My Domain host via
  ``SALESFORCE_LOGIN_URL``) using the Connected App in
  ``SALESFORCE_CLIENT_ID`` / ``SALESFORCE_CLIENT_SECRET``.
* Remembers the org's ``instance_url`` and identity URL — Salesforce hands
  them back with the tokens and every REST call needs them — through the
  generic ``config_from_tokens`` / ``apply_config`` hooks on BaseConnector.
* Finds the person we called (Contact first, then unconverted Lead) by phone
  (SOSL, digits-only so formatting never matters) or e-mail (SOQL).
* Creates a Lead for an unknown callee, logs every call as a completed
  ``Task`` (subtype Call, with duration, direction and disposition) and can
  amend that Task once the AI summary is ready.
* Pulls Leads/Contacts back out (``query``) so a campaign can be seeded from
  a Salesforce list.

Salesforce specifics worth knowing
----------------------------------
* The token response has NO ``expires_in``; access-token life is the org's
  session timeout.  We stamp a conservative ``expires_at`` (default 15 min,
  ``SALESFORCE_ACCESS_TOKEN_TTL_SECONDS``) so the resolver refreshes
  proactively, and the sync service retries once with a forced refresh on a
  401 (``INVALID_SESSION_ID``).
* ``TaskSubtype`` is a create-only field that some orgs restrict; on
  ``INVALID_FIELD_FOR_INSERT_UPDATE`` the Task is retried without it.
* A Lead needs ``LastName`` and ``Company``; both fall back to "Unknown".
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx

from app.infrastructure.connectors.base import (
    ConnectorFactory,
    ConnectorProviderError,
    OAuthTokens,
)
from app.infrastructure.connectors.crm.base import CRMProvider

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_URL = "https://login.salesforce.com"
DEFAULT_API_VERSION = "v60.0"
# Salesforce does not return expires_in; refresh well inside the shortest
# configurable org session timeout (15 minutes).
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 900

_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _async_client(**kwargs) -> httpx.AsyncClient:
    """Single seam for the HTTP client so tests can inject a MockTransport."""
    return httpx.AsyncClient(**kwargs)


_CONTACT_FIELDS = "Id, FirstName, LastName, Email, Phone, MobilePhone"
_LEAD_FIELDS = "Id, FirstName, LastName, Email, Phone, MobilePhone, Company, IsConverted"
# Salesforce record-id prefixes for the two objects a call can be logged against.
_WHO_ID_PREFIXES = ("003", "00Q")
_DESCRIPTION_MAX = 32000


def _soql_literal(value: str) -> str:
    """Escape a value for use inside a single-quoted SOQL string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _category_for_status(status_code: int, error_code: Optional[str]) -> str:
    if status_code == 401:
        return "authentication"
    if status_code == 403:
        if error_code and "LIMIT" in error_code.upper():
            return "rate_limit"
        return "permission"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "provider_outage"
    return "invalid_request"


def _parse_error_body(response: httpx.Response) -> tuple[str, Optional[str]]:
    """Salesforce errors are ``[{"message", "errorCode"}]``; OAuth errors are
    ``{"error", "error_description"}``.  Return ``(message, error_code)``."""
    try:
        body = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return (text[:300] or f"HTTP {response.status_code}"), None
    if isinstance(body, list) and body:
        first = body[0] if isinstance(body[0], dict) else {}
        return (
            str(first.get("message") or f"HTTP {response.status_code}"),
            str(first.get("errorCode")) if first.get("errorCode") else None,
        )
    if isinstance(body, dict):
        message = body.get("error_description") or body.get("message") or body.get("error")
        code = body.get("error") or body.get("errorCode")
        return (str(message or f"HTTP {response.status_code}"), str(code) if code else None)
    return f"HTTP {response.status_code}", None


class SalesforceConnector(CRMProvider):
    """Salesforce CRM integration (OAuth 2.0 web-server flow with PKCE)."""

    @property
    def provider_name(self) -> str:
        return "salesforce"

    @property
    def oauth_scopes(self) -> List[str]:
        # ``id`` lets us call the identity URL; ``refresh_token``/``offline_access``
        # are both accepted spellings for a refresh token grant.
        return ["api", "id", "refresh_token", "offline_access"]

    def __init__(self, tenant_id: str, connector_id: str):
        super().__init__(tenant_id=tenant_id, connector_id=connector_id)
        self._instance_url: Optional[str] = None
        self._identity_url: Optional[str] = None
        self.config: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Static configuration
    # ------------------------------------------------------------------

    @staticmethod
    def login_url() -> str:
        return (os.getenv("SALESFORCE_LOGIN_URL") or DEFAULT_LOGIN_URL).rstrip("/")

    @staticmethod
    def api_version() -> str:
        raw = (os.getenv("SALESFORCE_API_VERSION") or DEFAULT_API_VERSION).strip()
        return raw if raw.startswith("v") else f"v{raw}"

    @staticmethod
    def access_token_ttl_seconds() -> int:
        try:
            return max(120, int(os.getenv("SALESFORCE_ACCESS_TOKEN_TTL_SECONDS", DEFAULT_ACCESS_TOKEN_TTL_SECONDS)))
        except ValueError:
            return DEFAULT_ACCESS_TOKEN_TTL_SECONDS

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.getenv("SALESFORCE_CLIENT_ID") and os.getenv("SALESFORCE_CLIENT_SECRET"))

    def _get_client_credentials(self) -> tuple[str, str]:
        client_id = os.getenv("SALESFORCE_CLIENT_ID")
        client_secret = os.getenv("SALESFORCE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ValueError(
                "Salesforce OAuth credentials not configured. "
                "Set SALESFORCE_CLIENT_ID and SALESFORCE_CLIENT_SECRET environment variables."
            )
        return client_id, client_secret

    # ------------------------------------------------------------------
    # Persisted state (instance_url is mandatory for every API call)
    # ------------------------------------------------------------------

    @property
    def instance_url(self) -> Optional[str]:
        return self._instance_url

    def config_from_tokens(self, tokens: OAuthTokens) -> Optional[Dict[str, Any]]:
        out: Dict[str, Any] = {}
        if self._instance_url:
            out["instance_url"] = self._instance_url
        if self._identity_url:
            out["identity_url"] = self._identity_url
        return out or None

    def apply_config(self, config: Optional[Dict[str, Any]]) -> None:
        if not isinstance(config, dict):
            return
        self.config = dict(config)
        instance_url = config.get("instance_url")
        if isinstance(instance_url, str) and instance_url.strip():
            self._instance_url = instance_url.strip().rstrip("/")
        identity_url = config.get("identity_url")
        if isinstance(identity_url, str) and identity_url.strip():
            self._identity_url = identity_url.strip()

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None,
    ) -> str:
        client_id, _ = self._get_client_credentials()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.oauth_scopes),
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{self.login_url()}/services/oauth2/authorize?{urlencode(params)}"

    def _tokens_from_response(self, token_data: Dict[str, Any], fallback_refresh: Optional[str]) -> OAuthTokens:
        instance_url = token_data.get("instance_url")
        if isinstance(instance_url, str) and instance_url.strip():
            self._instance_url = instance_url.strip().rstrip("/")
        identity_url = token_data.get("id")
        if isinstance(identity_url, str) and identity_url.startswith("http"):
            self._identity_url = identity_url
        ttl = self.access_token_ttl_seconds()
        return OAuthTokens(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token") or fallback_refresh,
            token_type=token_data.get("token_type", "Bearer"),
            expires_in=ttl,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
            scope=token_data.get("scope"),
        )

    async def _token_request(self, data: Dict[str, str], operation: str) -> Dict[str, Any]:
        async with _async_client(timeout=_HTTP_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{self.login_url()}/services/oauth2/token",
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as exc:
                raise ConnectorProviderError(
                    provider="salesforce",
                    operation=operation,
                    category="network",
                    message=f"Salesforce token endpoint unreachable: {type(exc).__name__}",
                ) from exc
        if response.status_code != 200:
            message, code = _parse_error_body(response)
            logger.error("Salesforce %s failed status=%s code=%s", operation, response.status_code, code)
            # ``invalid_grant`` (revoked/expired refresh token) must read as an
            # authentication failure so the resolver tells the user to reconnect.
            category = "authentication" if response.status_code in (400, 401) and code in (
                "invalid_grant", "invalid_client", "inactive_user", "inactive_org",
            ) else _category_for_status(response.status_code, code)
            raise ConnectorProviderError(
                provider="salesforce",
                operation=operation,
                category=category,
                message=f"{operation} failed: {message}",
                status_code=response.status_code,
            )
        token_data = response.json()
        if not token_data.get("access_token"):
            raise ConnectorProviderError(
                provider="salesforce",
                operation=operation,
                category="authentication",
                message="Salesforce returned no access token.",
                status_code=response.status_code,
            )
        return token_data

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> OAuthTokens:
        client_id, client_secret = self._get_client_credentials()
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        token_data = await self._token_request(data, "exchange_code")
        return self._tokens_from_response(token_data, fallback_refresh=None)

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        client_id, client_secret = self._get_client_credentials()
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        token_data = await self._token_request(data, "refresh_tokens")
        return self._tokens_from_response(token_data, fallback_refresh=refresh_token)

    async def revoke_token(self, token: str) -> bool:
        """Best-effort revoke (Salesforce accepts access or refresh tokens)."""
        try:
            async with _async_client(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f"{self.login_url()}/services/oauth2/revoke",
                    data={"token": token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    async def fetch_account_identity(self) -> Optional[Dict[str, Any]]:
        if not self._identity_url:
            raise ConnectorProviderError(
                provider="salesforce",
                operation="identity",
                category="invalid_request",
                message="Salesforce did not return an identity URL.",
            )
        async with _async_client(timeout=_HTTP_TIMEOUT) as client:
            try:
                response = await client.get(self._identity_url, headers=self._get_auth_headers())
            except httpx.HTTPError as exc:
                raise ConnectorProviderError(
                    provider="salesforce",
                    operation="identity",
                    category="network",
                    message=f"Salesforce identity endpoint unreachable: {type(exc).__name__}",
                ) from exc
        if response.status_code != 200:
            message, code = _parse_error_body(response)
            raise ConnectorProviderError(
                provider="salesforce",
                operation="identity",
                category=_category_for_status(response.status_code, code),
                message=f"identity check failed: {message}",
                status_code=response.status_code,
            )
        body = response.json()
        org_id = str(body.get("organization_id") or "")
        return {
            "email": body.get("email") or body.get("username"),
            "external_account_id": org_id or None,
            "config": {
                "org_id": org_id or None,
                "user_id": body.get("user_id"),
                "username": body.get("username"),
                "display_name": body.get("display_name"),
            },
        }

    # ------------------------------------------------------------------
    # REST plumbing
    # ------------------------------------------------------------------

    def _get_auth_headers(self) -> Dict[str, str]:
        if not self._access_token:
            raise ValueError("Access token not set. Call set_access_token() first.")
        return {"Authorization": f"Bearer {self._access_token}"}

    def _api_base(self) -> str:
        if not self._instance_url:
            raise ConnectorProviderError(
                provider="salesforce",
                operation="api",
                category="invalid_request",
                message="Salesforce instance URL is missing; reconnect the connector.",
            )
        return f"{self._instance_url}/services/data/{self.api_version()}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        ok_statuses: tuple[int, ...] = (200, 201, 204),
    ) -> Any:
        url = f"{self._api_base()}{path}"
        headers = {**self._get_auth_headers(), "Content-Type": "application/json"}
        async with _async_client(timeout=_HTTP_TIMEOUT) as client:
            try:
                response = await client.request(method, url, json=json_body, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise ConnectorProviderError(
                    provider="salesforce",
                    operation=operation,
                    category="network",
                    message=f"Salesforce API unreachable: {type(exc).__name__}",
                ) from exc
        if response.status_code not in ok_statuses:
            message, code = _parse_error_body(response)
            logger.warning(
                "Salesforce %s failed status=%s code=%s", operation, response.status_code, code
            )
            err = ConnectorProviderError(
                provider="salesforce",
                operation=operation,
                category=_category_for_status(response.status_code, code),
                message=f"{operation} failed: {message}",
                status_code=response.status_code,
                retry_after=response.headers.get("Retry-After"),
            )
            err.error_code = code  # type: ignore[attr-defined]
            raise err
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def query(self, soql: str) -> List[Dict[str, Any]]:
        """Run a SOQL query and return its records (first page)."""
        data = await self._request("GET", "/query", operation="query", params={"q": soql})
        return list((data or {}).get("records") or [])

    async def search(self, sosl: str) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/search", operation="search", params={"q": sosl})
        return list((data or {}).get("searchRecords") or [])

    async def create_record(self, sobject: str, fields: Dict[str, Any]) -> str:
        data = await self._request(
            "POST", f"/sobjects/{quote(sobject)}", operation=f"create_{sobject.lower()}", json_body=fields,
            ok_statuses=(200, 201),
        )
        record_id = (data or {}).get("id")
        if not record_id:
            raise ConnectorProviderError(
                provider="salesforce",
                operation=f"create_{sobject.lower()}",
                category="invalid_request",
                message=f"Salesforce returned no id for the new {sobject}.",
            )
        return str(record_id)

    async def update_record(self, sobject: str, record_id: str, fields: Dict[str, Any]) -> None:
        await self._request(
            "PATCH", f"/sobjects/{quote(sobject)}/{quote(record_id)}",
            operation=f"update_{sobject.lower()}", json_body=fields, ok_statuses=(200, 204),
        )

    # ------------------------------------------------------------------
    # CRMProvider contract
    # ------------------------------------------------------------------

    @staticmethod
    def _shape_record(record: Dict[str, Any]) -> Dict[str, Any]:
        attrs = record.get("attributes") or {}
        return {
            "id": record.get("Id"),
            "object": attrs.get("type"),
            "first_name": record.get("FirstName"),
            "last_name": record.get("LastName"),
            "email": record.get("Email"),
            "phone": record.get("Phone") or record.get("MobilePhone"),
            "company": record.get("Company"),
        }

    async def search_contact(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Contact first (a real customer record), then an unconverted Lead."""
        if email and email.strip():
            literal = _soql_literal(email.strip())
            for soql in (
                f"SELECT {_CONTACT_FIELDS} FROM Contact WHERE Email = '{literal}' LIMIT 1",
                f"SELECT {_LEAD_FIELDS} FROM Lead WHERE Email = '{literal}' AND IsConverted = false LIMIT 1",
            ):
                records = await self.query(soql)
                if records:
                    return self._shape_record(records[0])

        digits = _phone_digits(phone or "")
        if len(digits) >= 6:
            sosl = (
                f"FIND {{{digits}}} IN PHONE FIELDS RETURNING "
                f"Contact({_CONTACT_FIELDS}), "
                f"Lead({_LEAD_FIELDS} WHERE IsConverted = false)"
            )
            records = await self.search(sosl)
            contacts = [r for r in records if (r.get("attributes") or {}).get("type") == "Contact"]
            leads = [r for r in records if (r.get("attributes") or {}).get("type") == "Lead"]
            if contacts:
                return self._shape_record(contacts[0])
            if leads:
                return self._shape_record(leads[0])
        return None

    async def create_contact(
        self,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Unknown callees become Leads (Salesforce's prospect object)."""
        props = dict(properties or {})
        fields: Dict[str, Any] = {
            "LastName": (last_name or "").strip() or (first_name or "").strip() or "Unknown",
            "Company": str(props.pop("company", "") or "").strip() or "Unknown",
            "LeadSource": str(props.pop("lead_source", "") or "Other"),
        }
        if first_name and (last_name or "").strip():
            fields["FirstName"] = first_name.strip()
        if email and email.strip():
            fields["Email"] = email.strip()
        if phone:
            fields["Phone"] = phone
        description = props.pop("description", None)
        if description:
            fields["Description"] = str(description)[:_DESCRIPTION_MAX]
        # Any remaining keys are trusted, already-Salesforce-named fields
        # (e.g. a tenant-configured custom field).
        fields.update({k: v for k, v in props.items() if v is not None})
        record_id = await self.create_record("Lead", fields)
        return {"id": record_id, "object": "Lead", **{k: v for k, v in fields.items()}}

    @staticmethod
    def _who_id(contact_id: Optional[str]) -> Optional[str]:
        cid = (contact_id or "").strip()
        return cid if cid and cid[:3] in _WHO_ID_PREFIXES else None

    async def log_call(
        self,
        contact_id: str,
        call_body: str,
        duration_seconds: int,
        outcome: str = "COMPLETED",
        call_direction: str = "OUTBOUND",
        timestamp: Optional[datetime] = None,
    ) -> str:
        ts = timestamp or datetime.now(timezone.utc)
        direction = "Inbound" if str(call_direction).upper().startswith("IN") else "Outbound"
        disposition = str(outcome or "COMPLETED")[:255]
        fields: Dict[str, Any] = {
            "Subject": f"Call - {disposition}"[:255],
            "Status": "Completed",
            "Priority": "Normal",
            "TaskSubtype": "Call",
            "ActivityDate": (ts.date() if isinstance(ts, datetime) else date.today()).isoformat(),
            "Description": (call_body or "")[:_DESCRIPTION_MAX],
            "CallType": direction,
            "CallDisposition": disposition,
            "CallDurationInSeconds": max(0, int(duration_seconds or 0)),
        }
        who_id = self._who_id(contact_id)
        if who_id:
            fields["WhoId"] = who_id
        try:
            return await self.create_record("Task", fields)
        except ConnectorProviderError as exc:
            code = getattr(exc, "error_code", None)
            if code == "INVALID_FIELD_FOR_INSERT_UPDATE" and "TaskSubtype" in fields:
                fields.pop("TaskSubtype")
                return await self.create_record("Task", fields)
            raise

    async def update_call_log(
        self,
        call_log_id: str,
        *,
        call_body: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> bool:
        fields: Dict[str, Any] = {}
        if call_body is not None:
            fields["Description"] = call_body[:_DESCRIPTION_MAX]
        if outcome:
            fields["CallDisposition"] = str(outcome)[:255]
            fields["Subject"] = f"Call - {str(outcome)[:240]}"
        if not fields:
            return False
        await self.update_record("Task", call_log_id, fields)
        return True

    async def create_note(
        self,
        contact_id: str,
        note_body: str,
        timestamp: Optional[datetime] = None,
    ) -> str:
        """Notes land as completed Tasks: ``ContentNote`` needs a two-step
        link and legacy ``Note`` is disabled in orgs with Enhanced Notes."""
        ts = timestamp or datetime.now(timezone.utc)
        fields: Dict[str, Any] = {
            "Subject": "Talky.ai call note",
            "Status": "Completed",
            "Priority": "Normal",
            "ActivityDate": ts.date().isoformat(),
            "Description": (note_body or "")[:_DESCRIPTION_MAX],
        }
        who_id = self._who_id(contact_id)
        if who_id:
            fields["WhoId"] = who_id
        return await self.create_record("Task", fields)

    # ------------------------------------------------------------------
    # Pull side: seed a campaign from Salesforce
    # ------------------------------------------------------------------

    async def list_people(
        self,
        sobject: str = "Lead",
        *,
        where: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return Leads or Contacts that have a phone number, shaped like
        :meth:`search_contact` results plus ``title``."""
        sobject = "Contact" if str(sobject).lower() == "contact" else "Lead"
        limit = max(1, min(int(limit or 500), 2000))
        if sobject == "Lead":
            fields = f"{_LEAD_FIELDS}, Title"
            base_where = "(Phone != null OR MobilePhone != null) AND IsConverted = false"
        else:
            fields = f"{_CONTACT_FIELDS}, Title, Account.Name"
            base_where = "(Phone != null OR MobilePhone != null)"
        clause = base_where
        extra = (where or "").strip()
        if extra:
            clause = f"{base_where} AND ({extra})"
        soql = f"SELECT {fields} FROM {sobject} WHERE {clause} ORDER BY LastModifiedDate DESC LIMIT {limit}"
        records = await self.query(soql)
        shaped = []
        for record in records:
            row = self._shape_record(record)
            row["title"] = record.get("Title")
            if sobject == "Contact":
                account = record.get("Account") or {}
                row["company"] = account.get("Name") if isinstance(account, dict) else None
            row["mobile_phone"] = record.get("MobilePhone")
            row["phone"] = record.get("Phone") or record.get("MobilePhone")
            shaped.append(row)
        return shaped

    async def probe(self) -> Dict[str, Any]:
        """Prove the token and instance work: identity + one trivial query."""
        identity = await self.fetch_account_identity() or {}
        await self.query("SELECT Id FROM Task LIMIT 1")
        return {
            "ok": True,
            "org_id": (identity.get("config") or {}).get("org_id"),
            "username": (identity.get("config") or {}).get("username"),
            "instance_url": self._instance_url,
            "api_version": self.api_version(),
        }


# Register with factory
ConnectorFactory.register("salesforce", SalesforceConnector)
