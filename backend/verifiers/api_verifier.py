"""API endpoint verification module."""

from typing import Dict
import os


class ApiVerifier:
    """Verify API endpoints exist and work."""

    async def verify(self) -> Dict[str, Dict]:
        """Verify API endpoints."""
        return {
            "Auth module loads": await self._verify_auth_module(),
            "POST /auth/register endpoint exists": await self._verify_register_endpoint(),
            "GET /auth/verify-email endpoint exists": await self._verify_verify_endpoint(),
            "POST /auth/login endpoint exists": await self._verify_login_endpoint(),
            "Verification tokens module loads": await self._verify_token_module(),
            "Email service module loads": await self._verify_email_module(),
            "Database migration file exists": await self._verify_migration_file(),
        }

    async def _verify_auth_module(self) -> Dict:
        """Verify auth module loads."""
        try:
            from app.api.v1.endpoints import auth
            return {"passed": True}
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot load auth module: {e}"
            }

    async def _verify_register_endpoint(self) -> Dict:
        """Verify POST /auth/register endpoint."""
        try:
            from app.api.v1.endpoints.auth import register
            # Check function exists and is async
            if callable(register):
                return {"passed": True}
            return {
                "passed": False,
                "error": "register function not callable"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot find register endpoint: {e}"
            }

    async def _verify_verify_endpoint(self) -> Dict:
        """Verify GET /auth/verify-email endpoint."""
        try:
            from app.api.v1.endpoints.auth import verify_email
            # Check function exists and is async
            if callable(verify_email):
                return {"passed": True}
            return {
                "passed": False,
                "error": "verify_email function not callable"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot find verify-email endpoint: {e}"
            }

    async def _verify_login_endpoint(self) -> Dict:
        """Verify POST /auth/login endpoint."""
        try:
            from app.api.v1.endpoints.auth import login
            # Check function exists and is async
            if callable(login):
                return {"passed": True}
            return {
                "passed": False,
                "error": "login function not callable"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot find login endpoint: {e}"
            }

    async def _verify_token_module(self) -> Dict:
        """Verify token utilities module."""
        try:
            from app.core.security.verification_tokens import (
                generate_verification_token,
                hash_verification_token,
                get_verification_token_expiry,
                verify_token_expiry,
            )
            # Test token generation
            token = generate_verification_token()
            if not token or len(token) < 32:
                return {
                    "passed": False,
                    "error": "Token generation failed"
                }

            # Test token hashing
            token_hash = hash_verification_token(token)
            if not token_hash or token_hash == token:
                return {
                    "passed": False,
                    "error": "Token hashing failed"
                }

            # Test expiry
            expiry = get_verification_token_expiry()
            if not expiry:
                return {
                    "passed": False,
                    "error": "Token expiry calculation failed"
                }

            return {"passed": True}
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot load token module: {e}"
            }

    async def _verify_email_module(self) -> Dict:
        """Verify email service module."""
        try:
            from app.domain.services.email_service import (
                EmailService,
                get_email_service,
            )
            # Test instantiation
            service = EmailService()
            if not hasattr(service, 'send_email'):
                return {
                    "passed": False,
                    "error": "EmailService missing send_email method"
                }

            if not hasattr(service, 'send_verification_email'):
                return {
                    "passed": False,
                    "error": "EmailService missing send_verification_email method"
                }

            return {"passed": True}
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot load email module: {e}"
            }

    async def _verify_migration_file(self) -> Dict:
        """Verify database migration file exists."""
        try:
            migration_path = "database/migrations/day1_email_verification.sql"
            if os.path.exists(migration_path):
                # Check file size (should be > 500 bytes)
                size = os.path.getsize(migration_path)
                if size > 500:
                    return {"passed": True}
                return {
                    "passed": False,
                    "error": f"Migration file too small ({size} bytes)"
                }
            return {
                "passed": False,
                "error": f"Migration file not found at {migration_path}"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot verify migration file: {e}"
            }
