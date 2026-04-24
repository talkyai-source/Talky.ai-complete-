"""
Secrets Management Service - Centralized secrets with envelope encryption
"""
import hashlib
import hmac
import json
import os
import secrets
import string
from base64 import b64decode, b64encode
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from app.core.kms import get_kms_backend, KMSBackend
import redis.asyncio as aioredis
import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel


class SecretType(str, Enum):
    """Types of secrets"""
    PLATFORM = "PLATFORM"              # Platform-level secrets (DB credentials, etc.)
    TENANT_API_KEY = "TENANT_API_KEY"  # Tenant API keys
    WEBHOOK_HMAC = "WEBHOOK_HMAC"      # Webhook HMAC signing keys
    INTEGRATION_OAUTH = "INTEGRATION_OAUTH"  # Third-party OAuth tokens
    EMERGENCY_BREAK_GLASS = "EMERGENCY_BREAK_GLASS"  # Emergency access keys


class SecretStatus(str, Enum):
    """Secret status"""
    ACTIVE = "active"
    ROTATED = "rotated"  # Old version after rotation
    REVOKED = "revoked"
    EXPIRED = "expired"
    COMPROMISED = "compromised"


class SecretAccessType(str, Enum):
    """Types of secret access"""
    CREATE = "create"
    READ = "read"
    ROTATE = "rotate"
    REVOKE = "revoke"
    VALIDATE = "validate"


class SecretMetadata(BaseModel):
    """Secret metadata"""
    secret_id: UUID
    tenant_id: Optional[UUID]
    secret_type: SecretType
    secret_name: str
    description: Optional[str]
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    last_accessed_at: Optional[datetime]
    access_count: int
    status: SecretStatus
    permissions: dict
    rotated_from: Optional[UUID]
    rotated_to: Optional[UUID]


class SecretValue(BaseModel):
    """Secret value wrapper"""
    value: dict  # The actual secret data
    metadata: SecretMetadata


class SecretsManager:
    """
    Centralized secrets management with envelope encryption.

    Features:
    - Envelope encryption (DEK encrypted by KEK)
    - Automatic rotation tracking
    - Access audit logging
    - API key generation and validation
    - Compromise recovery
    """

    API_KEY_PREFIX = "tk_live_"
    API_KEY_PREFIX_TEST = "tk_test_"
    API_KEY_LENGTH = 48

    def __init__(
        self,
        db_pool,
        master_key=None,        # kept for backward compat — ignored when KMS is configured
        redis_client=None,
        kms_provider=None,      # kept for backward compat — use KMS_PROVIDER env var instead
    ):
        self.db_pool = db_pool
        self.redis = redis_client
        # KMS backend — auto-selects aws or local based on KMS_PROVIDER env var
        self._kms: KMSBackend = get_kms_backend()

    def _generate_dek(self) -> bytes:
        """Generate a new Data Encryption Key"""
        return AESGCM.generate_key(bit_length=256)

    async def _wrap_dek(self, dek: bytes) -> bytes:
        """Wrap (encrypt) a DEK using the configured KMS backend."""
        return await self._kms.wrap_key(dek)

    async def _unwrap_dek(self, wrapped_dek: bytes) -> bytes:
        """Unwrap (decrypt) a DEK using the configured KMS backend."""
        return await self._kms.unwrap_key(wrapped_dek)

    def _encrypt_value(self, value: dict, dek: bytes) -> tuple[bytes, bytes]:
        """Encrypt secret value with DEK using AES-256-GCM"""
        aesgcm = AESGCM(dek)
        iv = os.urandom(12)  # 96-bit IV for GCM
        plaintext = json.dumps(value).encode()
        ciphertext = aesgcm.encrypt(iv, plaintext, None)
        return ciphertext, iv

    def _decrypt_value(self, ciphertext: bytes, iv: bytes, dek: bytes) -> dict:
        """Decrypt secret value with DEK"""
        aesgcm = AESGCM(dek)
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
        return json.loads(plaintext.decode())

    def _generate_api_key(self, test: bool = False) -> str:
        """Generate a secure API key"""
        prefix = self.API_KEY_PREFIX_TEST if test else self.API_KEY_PREFIX
        # Generate random part
        alphabet = string.ascii_letters + string.digits
        random_part = ''.join(secrets.choice(alphabet) for _ in range(self.API_KEY_LENGTH))
        return f"{prefix}{random_part}"

    def _hash_api_key(self, api_key: str) -> str:
        """Hash API key for storage (only store hash, not actual key)"""
        return hashlib.sha256(api_key.encode()).hexdigest()

    def _verify_api_key(self, api_key: str, stored_hash: str) -> bool:
        """Constant-time API key verification"""
        computed_hash = self._hash_api_key(api_key)
        return hmac.compare_digest(computed_hash, stored_hash)

    async def create(
        self,
        secret_type: SecretType,
        owner_type: str,  # tenant, user, system
        owner_id: UUID | str,
        value: dict,
        secret_name: Optional[str] = None,
        description: Optional[str] = None,
        created_by: Optional[UUID | str] = None,
        permissions: Optional[dict] = None,
        rotation_days: Optional[int] = 90,
        test_key: bool = False,
    ) -> UUID:
        """
        Create a new encrypted secret.

        Args:
            secret_type: Type of secret
            owner_type: Owner category
            owner_id: UUID of owner
            value: Secret value (dict)
            secret_name: Human-readable name
            description: Description
            created_by: User creating the secret
            permissions: Access control dict
            rotation_days: Days until rotation recommended
            test_key: Whether this is a test API key

        Returns:
            secret_id: UUID of created secret
        """
        secret_id = uuid4()
        owner_uuid = UUID(owner_id) if isinstance(owner_id, str) else owner_id
        creator_uuid = UUID(created_by) if isinstance(created_by, str) else created_by

        # For API keys, generate and store separately
        api_key = None
        api_key_hash = None
        if secret_type == SecretType.TENANT_API_KEY:
            api_key = self._generate_api_key(test=test_key)
            api_key_hash = self._hash_api_key(api_key)
            # Store the generated key in the value
            value["api_key_hash"] = api_key_hash
            value["prefix"] = api_key[:16]  # First 16 chars for identification

        # Generate DEK and encrypt
        dek = self._generate_dek()
        encrypted_dek = await self._wrap_dek(dek)
        ciphertext, iv = self._encrypt_value(value, dek)

        # Calculate expiration
        expires_at = None
        if rotation_days:
            expires_at = datetime.utcnow() + timedelta(days=rotation_days)

        # Determine tenant_id from owner
        tenant_id = owner_uuid if owner_type == "tenant" else None

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tenant_secrets (
                    secret_id, tenant_id, created_by, secret_type, secret_name,
                    description, encrypted_value, encrypted_dek, iv, algorithm,
                    permissions, version, expires_at, is_active
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                secret_id, tenant_id, creator_uuid, secret_type.value,
                secret_name or f"{secret_type.value}_{secret_id}",
                description, ciphertext, encrypted_dek, iv, "AES-256-GCM",
                json.dumps(permissions) if permissions else '{}',
                1, expires_at, True
            )

            # Log access
            await conn.execute(
                """
                INSERT INTO secret_access_log (
                    secret_id, tenant_id, accessed_by, access_type,
                    access_reason, success
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                secret_id, tenant_id, creator_uuid, SecretAccessType.CREATE.value,
                "Secret created", True
            )

        # Cache the API key temporarily for return (only time it's available)
        if api_key and self.redis:
            await self.redis.setex(
                f"secret:api_key:{secret_id}",
                60,  # 1 minute
                api_key
            )

        return secret_id, api_key if api_key else None

    async def get(
        self,
        secret_id: UUID | str,
        accessed_by: Optional[UUID | str] = None,
        access_reason: Optional[str] = None,
        access_type: SecretAccessType = SecretAccessType.READ,
    ) -> Optional[dict]:
        """
        Retrieve and decrypt a secret.

        Args:
            secret_id: Secret to retrieve
            accessed_by: User accessing the secret
            access_reason: Business reason for access
            access_type: Type of access

        Returns:
            Decrypted secret value or None if not found/revoked
        """
        secret_uuid = UUID(secret_id) if isinstance(secret_id, str) else secret_id
        accessor_uuid = UUID(accessed_by) if isinstance(accessed_by, str) else accessed_by

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenant_secrets WHERE secret_id = $1",
                secret_uuid
            )

            if not row:
                return None

            if not row["is_active"]:
                # Log failed access attempt
                await conn.execute(
                    """
                    INSERT INTO secret_access_log (
                        secret_id, tenant_id, accessed_by, access_type,
                        access_reason, success, failure_reason
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    secret_uuid, row["tenant_id"], accessor_uuid, access_type.value,
                    access_reason, False, "Secret is not active"
                )
                return None

            # Decrypt
            encrypted_dek = row["encrypted_dek"]
            dek = await self._unwrap_dek(encrypted_dek)

            try:
                value = self._decrypt_value(
                    row["encrypted_value"],
                    row["iv"],
                    dek
                )
            except Exception as e:
                # Log decryption failure
                await conn.execute(
                    """
                    INSERT INTO secret_access_log (
                        secret_id, tenant_id, accessed_by, access_type,
                        access_reason, success, failure_reason
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    secret_uuid, row["tenant_id"], accessor_uuid, access_type.value,
                    access_reason, False, f"Decryption failed: {str(e)}"
                )
                return None

            # Update access tracking
            await conn.execute(
                """
                UPDATE tenant_secrets
                SET last_accessed_at = NOW(), last_accessed_by = $1, access_count = access_count + 1
                WHERE secret_id = $2
                """,
                accessor_uuid, secret_uuid
            )

            # Log access
            await conn.execute(
                """
                INSERT INTO secret_access_log (
                    secret_id, tenant_id, accessed_by, access_type,
                    access_reason, success
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                secret_uuid, row["tenant_id"], accessor_uuid, access_type.value,
                access_reason, True
            )

            return value

    async def get_metadata(
        self,
        secret_id: UUID | str,
    ) -> Optional[SecretMetadata]:
        """Get secret metadata without decrypting value"""
        secret_uuid = UUID(secret_id) if isinstance(secret_id, str) else secret_id

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT secret_id, tenant_id, secret_type, secret_name, description,
                       version, created_at, updated_at, expires_at, last_accessed_at,
                       access_count, is_active, is_compromised, permissions,
                       rotated_from, rotated_to, revoked_at
                FROM tenant_secrets
                WHERE secret_id = $1
                """,
                secret_uuid
            )

        if not row:
            return None

        # Determine status
        if row["is_compromised"]:
            status = SecretStatus.COMPROMISED
        elif row["revoked_at"]:
            status = SecretStatus.REVOKED
        elif row["expires_at"] and row["expires_at"] < datetime.utcnow():
            status = SecretStatus.EXPIRED
        elif row["rotated_to"]:
            status = SecretStatus.ROTATED
        elif row["is_active"]:
            status = SecretStatus.ACTIVE
        else:
            status = SecretStatus.REVOKED

        return SecretMetadata(
            secret_id=row["secret_id"],
            tenant_id=row["tenant_id"],
            secret_type=SecretType(row["secret_type"]),
            secret_name=row["secret_name"],
            description=row["description"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            status=status,
            permissions=row["permissions"],
            rotated_from=row["rotated_from"],
            rotated_to=row["rotated_to"],
        )

    async def rotate(
        self,
        secret_id: UUID | str,
        rotated_by: UUID | str,
        grace_period_hours: int = 24,
        new_value: Optional[dict] = None,
    ) -> UUID:
        """
        Rotate a secret to a new value.

        Args:
            secret_id: Secret to rotate
            rotated_by: User performing rotation
            grace_period_hours: Hours old secret remains valid
            new_value: New secret value (if None, generates new)

        Returns:
            new_secret_id: UUID of new secret version
        """
        secret_uuid = UUID(secret_id) if isinstance(secret_id, str) else secret_id
        admin_uuid = UUID(rotated_by) if isinstance(rotated_by, str) else rotated_by

        async with self.db_pool.acquire() as conn:
            # Get current secret
            row = await conn.fetchrow(
                "SELECT * FROM tenant_secrets WHERE secret_id = $1",
                secret_uuid
            )

            if not row:
                raise ValueError("Secret not found")

            # Create new version
            new_secret_id = uuid4()

            if new_value is None and row["secret_type"] == SecretType.TENANT_API_KEY.value:
                # Generate new API key
                new_value = {"permissions": row["permissions"]}
                api_key = self._generate_api_key()
                new_value["api_key_hash"] = self._hash_api_key(api_key)
                new_value["prefix"] = api_key[:16]

            # Encrypt new value
            dek = self._generate_dek()
            encrypted_dek = await self._wrap_dek(dek)
            ciphertext, iv = self._encrypt_value(new_value or {}, dek)

            await conn.execute(
                """
                INSERT INTO tenant_secrets (
                    secret_id, tenant_id, created_by, secret_type, secret_name,
                    description, encrypted_value, encrypted_dek, iv, algorithm,
                    permissions, version, rotated_from, expires_at, is_active
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """,
                new_secret_id, row["tenant_id"], admin_uuid, row["secret_type"],
                row["secret_name"], row["description"], ciphertext, encrypted_dek, iv,
                "AES-256-GCM", row["permissions"], row["version"] + 1,
                secret_uuid,
                datetime.utcnow() + timedelta(days=90),
                True
            )

            # Link old to new
            await conn.execute(
                "UPDATE tenant_secrets SET rotated_to = $1 WHERE secret_id = $2",
                new_secret_id, secret_uuid
            )

            # Schedule old secret revocation after grace period
            revoke_at = datetime.utcnow() + timedelta(hours=grace_period_hours)
            await conn.execute(
                """
                UPDATE tenant_secrets
                SET expires_at = LEAST(expires_at, $1)
                WHERE secret_id = $2
                """,
                revoke_at, secret_uuid
            )

            # Log rotation
            await conn.execute(
                """
                INSERT INTO secret_access_log (
                    secret_id, tenant_id, accessed_by, access_type,
                    access_reason, success
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                secret_uuid, row["tenant_id"], admin_uuid, SecretAccessType.ROTATE.value,
                f"Rotated to new secret: {new_secret_id}", True
            )

        return new_secret_id

    async def revoke(
        self,
        secret_id: UUID | str,
        revoked_by: UUID | str,
        reason: str,
    ) -> bool:
        """
        Revoke a secret immediately.

        Args:
            secret_id: Secret to revoke
            revoked_by: User revoking the secret
            reason: Reason for revocation

        Returns:
            True if revoked successfully
        """
        secret_uuid = UUID(secret_id) if isinstance(secret_id, str) else secret_id
        admin_uuid = UUID(revoked_by) if isinstance(revoked_by, str) else revoked_by

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tenant_id FROM tenant_secrets WHERE secret_id = $1",
                secret_uuid
            )

            if not row:
                return False

            await conn.execute(
                """
                UPDATE tenant_secrets
                SET is_active = FALSE, revoked_at = NOW(), revoked_reason = $1
                WHERE secret_id = $2
                """,
                reason, secret_uuid
            )

            # Log revocation
            await conn.execute(
                """
                INSERT INTO secret_access_log (
                    secret_id, tenant_id, accessed_by, access_type,
                    access_reason, success
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                secret_uuid, row["tenant_id"], admin_uuid, SecretAccessType.REVOKE.value,
                reason, True
            )

        # Invalidate any caches
        if self.redis:
            await self.redis.delete(f"secret:api_key:{secret_uuid}")

        return True

    async def mark_compromised(
        self,
        secret_id: UUID | str,
        reported_by: UUID | str,
        reason: str,
    ) -> bool:
        """
        Mark a secret as compromised and revoke immediately.

        Args:
            secret_id: Secret to mark
            reported_by: User reporting compromise
            reason: Description of compromise

        Returns:
            True if marked successfully
        """
        secret_uuid = UUID(secret_id) if isinstance(secret_id, str) else secret_id
        reporter_uuid = UUID(reported_by) if isinstance(reported_by, str) else reported_by

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tenant_id FROM tenant_secrets WHERE secret_id = $1",
                secret_uuid
            )

            if not row:
                return False

            await conn.execute(
                """
                UPDATE tenant_secrets
                SET is_active = FALSE, is_compromised = TRUE, revoked_at = NOW(), revoked_reason = $1
                WHERE secret_id = $2
                """,
                f"COMPROMISED: {reason}", secret_uuid
            )

            # Log compromise
            await conn.execute(
                """
                INSERT INTO secret_access_log (
                    secret_id, tenant_id, accessed_by, access_type,
                    access_reason, success
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                secret_uuid, row["tenant_id"], reporter_uuid, SecretAccessType.REVOKE.value,
                f"Marked compromised: {reason}", True
            )

        # Notify security team via Redis
        if self.redis:
            await self.redis.publish(
                "security:alerts",
                json.dumps({
                    "type": "secret_compromised",
                    "secret_id": str(secret_uuid),
                    "reason": reason
                })
            )

        return True

    async def validate_api_key(
        self,
        api_key: str,
        required_permission: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Validate an API key and return associated metadata.

        Args:
            api_key: API key to validate
            required_permission: Optional permission to check

        Returns:
            dict with tenant_id, permissions if valid, None otherwise
        """
        if not api_key.startswith((self.API_KEY_PREFIX, self.API_KEY_PREFIX_TEST)):
            return None

        api_key_hash = self._hash_api_key(api_key)
        prefix = api_key[:16]

        async with self.db_pool.acquire() as conn:
            # Find matching secrets by prefix
            rows = await conn.fetch(
                """
                SELECT secret_id, tenant_id, encrypted_value, encrypted_dek, iv, permissions
                FROM tenant_secrets
                WHERE secret_type = 'TENANT_API_KEY'
                AND is_active = TRUE
                AND (expires_at IS NULL OR expires_at > NOW())
                """
            )

            for row in rows:
                # Decrypt and check hash
                dek = await self._unwrap_dek(row["encrypted_dek"])
                try:
                    value = self._decrypt_value(row["encrypted_value"], row["iv"], dek)
                except Exception:
                    continue

                if value.get("api_key_hash") == api_key_hash:
                    # Valid key found
                    permissions = row["permissions"] or {}

                    # Check required permission
                    if required_permission:
                        allowed = permissions.get("permissions", [])
                        if required_permission not in allowed and "*" not in allowed:
                            # Log failed permission check
                            await conn.execute(
                                """
                                INSERT INTO secret_access_log (
                                    secret_id, tenant_id, access_type, presented_permission,
                                    success, failure_reason
                                ) VALUES ($1, $2, $3, $4, $5, $6)
                                """,
                                row["secret_id"], row["tenant_id"], SecretAccessType.VALIDATE.value,
                                required_permission, False, "Permission denied"
                            )
                            return None

                    # Log successful validation
                    await conn.execute(
                        """
                        INSERT INTO secret_access_log (
                            secret_id, tenant_id, access_type, presented_permission, success
                        ) VALUES ($1, $2, $3, $4, $5)
                        """,
                        row["secret_id"], row["tenant_id"], SecretAccessType.VALIDATE.value,
                        required_permission, True
                    )

                    # Update access tracking
                    await conn.execute(
                        """
                        UPDATE tenant_secrets
                        SET last_accessed_at = NOW(), access_count = access_count + 1
                        WHERE secret_id = $1
                        """,
                        row["secret_id"]
                    )

                    return {
                        "secret_id": row["secret_id"],
                        "tenant_id": row["tenant_id"],
                        "permissions": permissions.get("permissions", []),
                        "is_test": api_key.startswith(self.API_KEY_PREFIX_TEST)
                    }

        return None

    async def list_secrets(
        self,
        tenant_id: Optional[UUID | str] = None,
        secret_type: Optional[SecretType] = None,
        include_inactive: bool = False,
    ) -> list[SecretMetadata]:
        """
        List secrets with optional filtering.

        Returns metadata only - not secret values.
        """
        conditions = []
        params = []
        param_idx = 1

        if tenant_id:
            tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
            conditions.append(f"tenant_id = ${param_idx}")
            params.append(tenant_uuid)
            param_idx += 1

        if secret_type:
            conditions.append(f"secret_type = ${param_idx}")
            params.append(secret_type.value)
            param_idx += 1

        if not include_inactive:
            conditions.append("is_active = TRUE")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT secret_id, tenant_id, secret_type, secret_name, description,
                   version, created_at, updated_at, expires_at, last_accessed_at,
                   access_count, is_active, is_compromised, permissions,
                   rotated_from, rotated_to, revoked_at
            FROM tenant_secrets
            {where_clause}
            ORDER BY created_at DESC
        """

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._row_to_metadata(row) for row in rows]

    def _row_to_metadata(self, row: asyncpg.Record) -> SecretMetadata:
        """Convert database row to SecretMetadata"""
        if row["is_compromised"]:
            status = SecretStatus.COMPROMISED
        elif row["revoked_at"]:
            status = SecretStatus.REVOKED
        elif row["expires_at"] and row["expires_at"] < datetime.utcnow():
            status = SecretStatus.EXPIRED
        elif row["rotated_to"]:
            status = SecretStatus.ROTATED
        elif row["is_active"]:
            status = SecretStatus.ACTIVE
        else:
            status = SecretStatus.REVOKED

        return SecretMetadata(
            secret_id=row["secret_id"],
            tenant_id=row["tenant_id"],
            secret_type=SecretType(row["secret_type"]),
            secret_name=row["secret_name"],
            description=row["description"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            status=status,
            permissions=row["permissions"],
            rotated_from=row["rotated_from"],
            rotated_to=row["rotated_to"],
        )

    async def get_expiring_secrets(
        self,
        days: int = 7,
    ) -> list[SecretMetadata]:
        """Get secrets expiring within specified days"""
        cutoff = datetime.utcnow() + timedelta(days=days)

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT secret_id, tenant_id, secret_type, secret_name, description,
                       version, created_at, updated_at, expires_at, last_accessed_at,
                       access_count, is_active, is_compromised, permissions,
                       rotated_from, rotated_to, revoked_at
                FROM tenant_secrets
                WHERE is_active = TRUE
                AND expires_at IS NOT NULL
                AND expires_at <= $1
                ORDER BY expires_at
                """,
                cutoff
            )

        return [self._row_to_metadata(row) for row in rows]


# Convenience function for dependency injection
async def get_secrets_manager(
    db_pool: asyncpg.Pool,
    redis_client: Optional[aioredis.Redis] = None
) -> SecretsManager:
    """Factory function for creating secrets manager"""
    return SecretsManager(db_pool, redis_client=redis_client)
