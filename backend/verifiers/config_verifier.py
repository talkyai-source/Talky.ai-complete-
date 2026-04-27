"""Configuration verification module."""

from typing import Dict
import os


class ConfigVerifier:
    """Verify email verification system configuration."""

    async def verify(self) -> Dict[str, Dict]:
        """Verify all configuration."""
        return {
            "EMAIL_USER configured": await self._verify_email_user(),
            "EMAIL_PASS configured": await self._verify_email_pass(),
            "FRONTEND_URL configured": await self._verify_frontend_url(),
            "API_BASE_URL configured": await self._verify_api_base_url(),
            "DATABASE_URL configured": await self._verify_database_url(),
            "JWT_SECRET configured": await self._verify_jwt_secret(),
            "Config loading works": await self._verify_config_loading(),
        }

    async def _verify_email_user(self) -> Dict:
        """Verify EMAIL_USER is set."""
        email_user = os.getenv("EMAIL_USER")
        if email_user and "@" in email_user:
            return {"passed": True}
        return {
            "passed": False,
            "error": f"EMAIL_USER not set or invalid. Value: {email_user}"
        }

    async def _verify_email_pass(self) -> Dict:
        """Verify EMAIL_PASS is set."""
        email_pass = os.getenv("EMAIL_PASS")
        if email_pass and len(email_pass) > 8:
            return {"passed": True}
        return {
            "passed": False,
            "error": "EMAIL_PASS not set or too short. Must be App Password (16+ chars)"
        }

    async def _verify_frontend_url(self) -> Dict:
        """Verify FRONTEND_URL is set."""
        frontend_url = os.getenv("FRONTEND_URL")
        if frontend_url and frontend_url.startswith("http"):
            return {"passed": True}
        return {
            "passed": False,
            "error": f"FRONTEND_URL not set or invalid. Value: {frontend_url}"
        }

    async def _verify_api_base_url(self) -> Dict:
        """Verify API_BASE_URL is set."""
        api_base_url = os.getenv("API_BASE_URL")
        if api_base_url and api_base_url.startswith("http"):
            return {"passed": True}
        return {
            "passed": False,
            "error": f"API_BASE_URL not set or invalid. Value: {api_base_url}"
        }

    async def _verify_database_url(self) -> Dict:
        """Verify DATABASE_URL is set."""
        database_url = os.getenv("DATABASE_URL")
        if database_url and "postgresql://" in database_url:
            return {"passed": True}
        return {
            "passed": False,
            "error": "DATABASE_URL not set or invalid. Must be PostgreSQL connection string"
        }

    async def _verify_jwt_secret(self) -> Dict:
        """Verify JWT_SECRET is set."""
        jwt_secret = os.getenv("JWT_SECRET")
        if jwt_secret and len(jwt_secret) > 32:
            return {"passed": True}
        return {
            "passed": False,
            "error": "JWT_SECRET not set or too short. Must be 32+ characters"
        }

    async def _verify_config_loading(self) -> Dict:
        """Verify FastAPI config loads correctly."""
        try:
            from app.core.config import get_settings
            settings = get_settings()

            # Check email fields
            if not settings.email_user or not settings.email_pass:
                return {
                    "passed": False,
                    "error": "Settings object missing email_user or email_pass"
                }

            return {"passed": True}
        except Exception as e:
            return {
                "passed": False,
                "error": f"Failed to load config: {e}"
            }
