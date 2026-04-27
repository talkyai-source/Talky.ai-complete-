"""Database verification module."""

from typing import Dict
import asyncpg
import os


class DatabaseVerifier:
    """Verify email verification database schema."""

    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        self.conn = None

    async def verify(self) -> Dict[str, Dict]:
        """Verify database schema and migrations."""
        try:
            await self._connect()
            results = {
                "Database connection": await self._verify_connection(),
                "user_profiles table exists": await self._verify_table_exists(),
                "is_verified column exists": await self._verify_column("is_verified"),
                "verification_token column exists": await self._verify_column("verification_token"),
                "verification_token_expires_at column exists": await self._verify_column("verification_token_expires_at"),
                "email_verified_at column exists": await self._verify_column("email_verified_at"),
                "Verification token index exists": await self._verify_index("idx_user_profiles_verification_token"),
                "Verification status index exists": await self._verify_index("idx_user_profiles_is_verified"),
                "Data consistency constraint exists": await self._verify_constraint("chk_email_verification_consistency"),
            }
            return results
        except Exception as e:
            return {
                "Database verification": {
                    "passed": False,
                    "error": f"Database verification failed: {e}"
                }
            }
        finally:
            await self._disconnect()

    async def _connect(self) -> None:
        """Connect to PostgreSQL database."""
        if not self.db_url:
            raise Exception("DATABASE_URL not set")
        self.conn = await asyncpg.connect(self.db_url)

    async def _disconnect(self) -> None:
        """Disconnect from database."""
        if self.conn:
            await self.conn.close()

    async def _verify_connection(self) -> Dict:
        """Verify database connection works."""
        try:
            if not self.conn:
                await self._connect()
            result = await self.conn.fetchval("SELECT 1")
            return {"passed": result == 1}
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot connect to database: {e}"
            }

    async def _verify_table_exists(self) -> Dict:
        """Verify user_profiles table exists."""
        try:
            result = await self.conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name='user_profiles'
                )
                """
            )
            if result:
                return {"passed": True}
            return {
                "passed": False,
                "error": "user_profiles table not found"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Error checking table: {e}"
            }

    async def _verify_column(self, column_name: str) -> Dict:
        """Verify column exists in user_profiles."""
        try:
            result = await self.conn.fetchval(
                f"""
                SELECT EXISTS(
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='user_profiles' AND column_name='{column_name}'
                )
                """
            )
            if result:
                return {"passed": True}
            return {
                "passed": False,
                "error": f"Column '{column_name}' not found in user_profiles"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Error checking column: {e}"
            }

    async def _verify_index(self, index_name: str) -> Dict:
        """Verify index exists."""
        try:
            result = await self.conn.fetchval(
                f"""
                SELECT EXISTS(
                    SELECT 1 FROM pg_indexes
                    WHERE indexname='{index_name}'
                )
                """
            )
            if result:
                return {"passed": True}
            return {
                "passed": False,
                "error": f"Index '{index_name}' not found"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Error checking index: {e}"
            }

    async def _verify_constraint(self, constraint_name: str) -> Dict:
        """Verify constraint exists."""
        try:
            result = await self.conn.fetchval(
                f"""
                SELECT EXISTS(
                    SELECT 1 FROM information_schema.constraint_column_usage
                    WHERE constraint_name='{constraint_name}'
                )
                """
            )
            if result:
                return {"passed": True}
            return {
                "passed": False,
                "error": f"Constraint '{constraint_name}' not found"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Error checking constraint: {e}"
            }
