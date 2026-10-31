"""
Microsoft Outlook Calendar Connector
OAuth 2.0 integration with Microsoft Graph API.

Day 25: Meeting Booking Feature
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


class OutlookCalendarConnector(CalendarProvider):
    """
    Microsoft Outlook Calendar integration via Microsoft Graph API.
    
    Setup Required:
    - Register app in Microsoft Entra admin center
    - Enable Calendar APIs
    - Create OAuth 2.0 credentials
    - Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET env vars
    
    OAuth Scopes:
    - Calendars.ReadWrite
    - OnlineMeetings.ReadWrite (for Teams links)
    - offline_access (for refresh tokens)
    
    API Reference:
    - https://learn.microsoft.com/en-us/graph/api/calendar-list-events
    - https://learn.microsoft.com/en-us/graph/api/user-post-events
    """
    
    # Microsoft OAuth endpoints (common tenant for multi-tenant apps)
    OAUTH_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    OAUTH_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    API_BASE_URL = "https://graph.microsoft.com/v1.0"
    
    @property
    def provider_name(self) -> str:
        return "outlook_calendar"
    
    @property
    def oauth_scopes(self) -> List[str]:
        return [
            "Calendars.ReadWrite",
            "OnlineMeetings.ReadWrite",
            "offline_access"
        ]
    
    def _get_client_credentials(self) -> tuple[str, str]:
        """Get Microsoft OAuth client credentials from environment."""
        client_id = os.getenv("MICROSOFT_CLIENT_ID")
        client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise ValueError(
                "Microsoft OAuth credentials not configured. "
                "Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET environment variables."
            )
        
        return client_id, client_secret
    
    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None
    ) -> str:
        """Generate Microsoft OAuth authorization URL."""
        client_id, _ = self._get_client_credentials()
        
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.oauth_scopes),
            "state": state,
            "response_mode": "query"
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
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.oauth_scopes)
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
            "grant_type": "refresh_token",
            "scope": " ".join(self.oauth_scopes)
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
                refresh_token=token_data.get("refresh_token", refresh_token),
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
        """
        Create a Microsoft Outlook calendar event.
        
        If add_video_conference is True, creates a Microsoft Teams meeting.
        """
        event_body = {
            "subject": title,
            "start": {
                "dateTime": utc_datetime(start_time).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": utc_datetime(end_time).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            }
        }
        
        if description:
            event_body["body"] = {
                "contentType": "text",
                "content": description
            }
        
        if location:
            event_body["location"] = {"displayName": location}
        
        if attendees:
            event_body["attendees"] = [
                {
                    "emailAddress": {"address": email},
                    "type": "required"
                }
                for email in attendees
            ]
        
        if add_video_conference:
            # Create as Teams meeting
            event_body["isOnlineMeeting"] = True
            event_body["onlineMeetingProvider"] = "teamsForBusiness"

        # Calendar-native reminder ("meeting timer"): alert 15 minutes before.
        event_body["isReminderOn"] = True
        event_body["reminderMinutesBeforeStart"] = 15

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/me/calendar/events",
                json=event_body,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"Create event failed: {response.text}")
                raise ValueError(f"Create event failed: {response.text}")
            
            data = response.json()
            
            # Extract Teams meeting link
            video_link = None
            if "onlineMeeting" in data and data["onlineMeeting"]:
                video_link = data["onlineMeeting"].get("joinUrl")
            
            # Parse start/end times
            start_dt = datetime.fromisoformat(data["start"]["dateTime"])
            end_dt = datetime.fromisoformat(data["end"]["dateTime"])
            
            return CalendarEvent(
                id=data["id"],
                title=data.get("subject", ""),
                description=data.get("body", {}).get("content"),
                start_time=start_dt,
                end_time=end_dt,
                timezone=data["start"].get("timeZone", timezone),
                location=data.get("location", {}).get("displayName"),
                attendees=[a["emailAddress"]["address"] for a in data.get("attendees", [])],
                video_link=video_link,
                metadata={"webLink": data.get("webLink")}
            )
    
    async def get_event_reference(self, event_id: str) -> str:
        """Observe one saved default-calendar reference; never infer action success."""
        if not isinstance(event_id, str) or re.fullmatch(r"(?!\.{1,2}$)[!-~]{1,512}", event_id) is None:
            raise ValueError("Invalid saved event reference")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/me/calendar/events/{quote(event_id, safe='')}",
                headers=self._get_auth_headers(), params={"$select": "id"},
            )
        if response.status_code != 200:
            raise ConnectorProviderError(
                provider=self.provider_name, operation="get_event_reference",
                category="not_found" if response.status_code in (404, 410) else "provider_error",
                status_code=response.status_code, message="Calendar observation unavailable",
            )
        data = response.json()
        # Project only the reference, without dates, subject, attendees,
        # body, or provider diagnostics.
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
        """Update a Microsoft Outlook calendar event."""
        update_body = {}
        
        if title is not None:
            update_body["subject"] = title
        if description is not None:
            update_body["body"] = {"contentType": "text", "content": description}
        if location is not None:
            update_body["location"] = {"displayName": location}
        if start_time is not None:
            update_body["start"] = {
                "dateTime": utc_datetime(start_time).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            }
        if end_time is not None:
            update_body["end"] = {
                "dateTime": utc_datetime(end_time).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC"
            }
        if attendees is not None:
            update_body["attendees"] = [
                {"emailAddress": {"address": email}, "type": "required"}
                for email in attendees
            ]
        
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{self.API_BASE_URL}/me/calendar/events/{event_id}",
                json=update_body,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                raise ValueError(f"Update event failed: {response.text}")
            
            data = response.json()
            
            return CalendarEvent(
                id=data["id"],
                title=data.get("subject", ""),
                description=data.get("body", {}).get("content"),
                start_time=datetime.fromisoformat(data["start"]["dateTime"]),
                end_time=datetime.fromisoformat(data["end"]["dateTime"]),
                timezone=data["start"].get("timeZone", "UTC"),
                location=data.get("location", {}).get("displayName"),
                attendees=[a["emailAddress"]["address"] for a in data.get("attendees", [])]
            )
    
    async def delete_event(self, event_id: str) -> bool:
        """Delete a Microsoft Outlook calendar event."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{self.API_BASE_URL}/me/calendar/events/{event_id}",
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
            "startDateTime": utc_datetime(start_time).isoformat(),
            "endDateTime": utc_datetime(end_time).isoformat(),
            "$top": max_results,
            "$orderby": "start/dateTime"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/me/calendarView?{urlencode(params)}",
                headers={**self._get_auth_headers(), "Prefer": 'outlook.timezone="UTC"'}
            )
            
            if response.status_code != 200:
                raise ValueError(f"List events failed: {response.text}")
            
            data = response.json()
            if max_results >= 1000 and data.get("@odata.nextLink"):
                raise ValueError("Calendar availability exceeded the complete lookup limit")
            events = []
            
            for item in data.get("value", []):
                if item.get("isCancelled") or item.get("showAs") == "free":
                    continue
                events.append(CalendarEvent(
                    id=item["id"],
                    title=item.get("subject", ""),
                    description=item.get("body", {}).get("content"),
                    start_time=utc_datetime(item["start"]["dateTime"]),
                    end_time=utc_datetime(item["end"]["dateTime"]),
                    timezone=item["start"].get("timeZone", "UTC"),
                    location=item.get("location", {}).get("displayName"),
                    attendees=[a["emailAddress"]["address"] for a in item.get("attendees", [])]
                ))
            
            return events
    
    async def get_availability(
        self,
        start_time: datetime,
        end_time: datetime,
        duration_minutes: int = 30
    ) -> List[Dict[str, datetime]]:
        """
        Get available time slots by checking busy times.
        
        Uses Graph API calendarView to get events and finds gaps.
        """
        events = await self.list_events(start_time, end_time, max_results=1000)
        return available_intervals(start_time, end_time,
            [(event.start_time, event.end_time) for event in events if event.start_time and event.end_time],
            duration_minutes)


# Register with factory
ConnectorFactory.register("outlook_calendar", OutlookCalendarConnector)
