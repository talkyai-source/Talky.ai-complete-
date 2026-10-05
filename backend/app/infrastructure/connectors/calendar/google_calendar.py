"""
Google Calendar Connector
OAuth 2.0 integration with Google Calendar API.

Day 24: Unified Connector System
"""
import os
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode
import httpx

from app.infrastructure.connectors.base import ConnectorFactory, ConnectorProviderError, OAuthTokens
from app.infrastructure.connectors.calendar.base import CalendarProvider, CalendarEvent, utc_datetime, available_intervals

logger = logging.getLogger(__name__)


class GoogleCalendarConnector(CalendarProvider):
    """
    Google Calendar integration using OAuth 2.0.
    
    Setup Required:
    - Enable Google Calendar API in Google Cloud Console
    - Create OAuth 2.0 credentials
    - Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars
    
    OAuth Scopes:
    - https://www.googleapis.com/auth/calendar
    - https://www.googleapis.com/auth/calendar.events
    """
    
    OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
    API_BASE_URL = "https://www.googleapis.com/calendar/v3"
    
    @property
    def provider_name(self) -> str:
        return "google_calendar"
    
    @property
    def oauth_scopes(self) -> List[str]:
        return [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events"
        ]
    
    def _get_client_credentials(self) -> tuple[str, str]:
        """Get Google OAuth client credentials from environment."""
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise ValueError(
                "Google OAuth credentials not configured. "
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."
            )
        
        return client_id, client_secret
    
    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None
    ) -> str:
        """Generate Google OAuth authorization URL."""
        client_id, _ = self._get_client_credentials()
        
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.oauth_scopes),
            "state": state,
            "access_type": "offline",  # Get refresh token
            "prompt": "consent"  # Force consent to get refresh token
        }
        
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        
        return f"{self.OAUTH_AUTH_URL}?{urlencode(params)}"
    
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        """Exchange authorization code for tokens."""
        client_id, client_secret = self._get_client_credentials()
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }
        
        if code_verifier:
            data["code_verifier"] = code_verifier
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                logger.error(f"Token exchange failed: {response.text}")
                raise ValueError(f"Token exchange failed: {response.text}")
            
            token_data = response.json()
            
            expires_at = None
            if "expires_in" in token_data:
                expires_at = datetime.utcnow() + timedelta(seconds=token_data["expires_in"])
            
            return OAuthTokens(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in"),
                expires_at=expires_at,
                scope=token_data.get("scope")
            )
    
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """Refresh access token using refresh token."""
        client_id, client_secret = self._get_client_credentials()
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                logger.error(f"Token refresh failed: {response.text}")
                raise ValueError(f"Token refresh failed: {response.text}")
            
            token_data = response.json()
            
            expires_at = None
            if "expires_in" in token_data:
                expires_at = datetime.utcnow() + timedelta(seconds=token_data["expires_in"])
            
            return OAuthTokens(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", refresh_token),  # May not be returned
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in"),
                expires_at=expires_at,
                scope=token_data.get("scope")
            )
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers for API requests."""
        if not self._access_token:
            raise ValueError("Access token not set. Call set_access_token() first.")
        return {"Authorization": f"Bearer {self._access_token}"}
    
    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        location: Optional[str] = None,
        add_video_conference: bool = False,
        timezone: str = "UTC"
    ) -> CalendarEvent:
        """Create a Google Calendar event."""
        event_body = {
            "summary": title,
            "start": {
                "dateTime": start_time.isoformat(),
                "timeZone": timezone
            },
            "end": {
                "dateTime": end_time.isoformat(),
                "timeZone": timezone
            }
        }
        
        if description:
            event_body["description"] = description
        
        if location:
            event_body["location"] = location
        
        if attendees:
            event_body["attendees"] = [{"email": email} for email in attendees]
        
        if add_video_conference:
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": f"talky-{datetime.utcnow().timestamp()}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            }

        # Calendar-native reminders ("meeting timer") so the event itself alerts
        # the organizer/attendees ahead of time — a popup 10 min before and an
        # email 30 min before — instead of relying on the account's defaults.
        event_body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 10},
                {"method": "email", "minutes": 30},
            ],
        }

        params = {}
        if add_video_conference:
            params["conferenceDataVersion"] = "1"
        
        url = f"{self.API_BASE_URL}/calendars/primary/events"
        if params:
            url += f"?{urlencode(params)}"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=event_body,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"Create event failed: {response.text}")
                raise ValueError(f"Create event failed: {response.text}")
            
            data = response.json()
            
            video_link = None
            if "conferenceData" in data and "entryPoints" in data["conferenceData"]:
                for entry in data["conferenceData"]["entryPoints"]:
                    if entry.get("entryPointType") == "video":
                        video_link = entry.get("uri")
                        break
            
            return CalendarEvent(
                id=data["id"],
                title=data.get("summary", ""),
                description=data.get("description"),
                start_time=datetime.fromisoformat(data["start"]["dateTime"].replace("Z", "+00:00")),
                end_time=datetime.fromisoformat(data["end"]["dateTime"].replace("Z", "+00:00")),
                timezone=data["start"].get("timeZone", timezone),
                location=data.get("location"),
                attendees=[a["email"] for a in data.get("attendees", [])],
                video_link=video_link,
                metadata={"htmlLink": data.get("htmlLink")}
            )
    
    async def get_event_reference(self, event_id: str) -> str:
        """Observe one saved default-calendar reference; never infer action success."""
        if not isinstance(event_id, str) or re.fullmatch(r"(?!\.{1,2}$)[!-~]{1,512}", event_id) is None:
            raise ValueError("Invalid saved event reference")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/calendars/primary/events/{quote(event_id, safe='')}",
                headers=self._get_auth_headers(), params={"fields": "id"},
            )
        if response.status_code != 200:
            raise ConnectorProviderError(
                provider=self.provider_name, operation="get_event_reference",
                category="not_found" if response.status_code in (404, 410) else "provider_error",
                status_code=response.status_code, message="Calendar observation unavailable",
            )
        data = response.json()
        # Deleted Google tombstones can contain only an ID. Do not require
        # dates or copy any subject, attendee, body, or provider diagnostic.
        if not isinstance(data, dict) or data.get("id") != event_id:
            raise ValueError("Calendar observation reference mismatch")
        return event_id

    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        location: Optional[str] = None
    ) -> CalendarEvent:
        """Update a Google Calendar event."""
        # First get existing event
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/calendars/primary/events/{event_id}",
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Event not found: {event_id}")
            
            event_body = response.json()
        
        # Update fields
        if title is not None:
            event_body["summary"] = title
        if description is not None:
            event_body["description"] = description
        if location is not None:
            event_body["location"] = location
        if start_time is not None:
            event_body["start"]["dateTime"] = start_time.isoformat()
        if end_time is not None:
            event_body["end"]["dateTime"] = end_time.isoformat()
        if attendees is not None:
            event_body["attendees"] = [{"email": email} for email in attendees]
        
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.API_BASE_URL}/calendars/primary/events/{event_id}",
                json=event_body,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                raise ValueError(f"Update event failed: {response.text}")
            
            data = response.json()
            
            return CalendarEvent(
                id=data["id"],
                title=data.get("summary", ""),
                description=data.get("description"),
                start_time=datetime.fromisoformat(data["start"]["dateTime"].replace("Z", "+00:00")),
                end_time=datetime.fromisoformat(data["end"]["dateTime"].replace("Z", "+00:00")),
                timezone=data["start"].get("timeZone", "UTC"),
                location=data.get("location"),
                attendees=[a["email"] for a in data.get("attendees", [])]
            )
    
    async def delete_event(self, event_id: str) -> bool:
        """Delete a Google Calendar event."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{self.API_BASE_URL}/calendars/primary/events/{event_id}",
                headers=self._get_auth_headers()
            )
            
            return response.status_code == 204
    
    async def list_events(
        self,
        start_time: datetime,
        end_time: datetime,
        max_results: int = 50
    ) -> List[CalendarEvent]:
        """List events in a time range."""
        params = {
            "timeMin": utc_datetime(start_time).isoformat(),
            "timeMax": utc_datetime(end_time).isoformat(),
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/calendars/primary/events?{urlencode(params)}",
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                from app.infrastructure.connectors.google_errors import (
                    google_api_error_from_response,
                )
                raise google_api_error_from_response("google_calendar", response, "list_events")
            
            data = response.json()
            events = []
            
            for item in data.get("items", []):
                if "dateTime" not in item.get("start", {}):
                    continue  # Skip all-day events
                
                events.append(CalendarEvent(
                    id=item["id"],
                    title=item.get("summary", ""),
                    description=item.get("description"),
                    start_time=datetime.fromisoformat(
                        item["start"]["dateTime"].replace("Z", "+00:00")
                    ),
                    end_time=datetime.fromisoformat(
                        item["end"]["dateTime"].replace("Z", "+00:00")
                    ),
                    timezone=item["start"].get("timeZone", "UTC"),
                    location=item.get("location"),
                    attendees=[a["email"] for a in item.get("attendees", [])]
                ))
            
            return events
    
    async def get_availability(
        self,
        start_time: datetime,
        end_time: datetime,
        duration_minutes: int = 30
    ) -> List[Dict[str, datetime]]:
        """Get available time slots by checking busy times."""
        # FreeBusy includes all-day events and is not truncated by an event-list page.
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.API_BASE_URL}/freeBusy", headers=self._get_auth_headers(),
                json={"timeMin": utc_datetime(start_time).isoformat(),
                      "timeMax": utc_datetime(end_time).isoformat(), "items": [{"id": "primary"}]})
            if response.status_code != 200:
                from app.infrastructure.connectors.google_errors import google_api_error_from_response
                raise google_api_error_from_response("google_calendar", response, "free_busy")
            calendar = (response.json().get("calendars") or {}).get("primary")
            if not isinstance(calendar, dict) or calendar.get("errors") or "busy" not in calendar:
                raise ValueError("Calendar availability was not confirmed by the provider")
            return available_intervals(start_time, end_time,
                [(period["start"], period["end"]) for period in calendar["busy"]], duration_minutes)


# Register with factory
ConnectorFactory.register("google_calendar", GoogleCalendarConnector)
