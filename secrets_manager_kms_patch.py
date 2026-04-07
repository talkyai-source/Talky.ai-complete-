# secrets_manager_kms_patch.py
#
# This file shows EXACTLY what to change in secrets_manager.py to wire in
# the KMS backend. Apply these changes to the existing file.
#
# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 1: Add import at the top of the file (after existing imports)
# ─────────────────────────────────────────────────────────────────────────────
#
# ADD this line after the existing imports:
#
#   from app.core.kms import get_kms_backend, KMSBackend
#
# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 2: Replace __init__ method
# ─────────────────────────────────────────────────────────────────────────────
#
# REMOVE this block (lines ~90-112):
#
#   def __init__(
#       self,
#       db_pool: asyncpg.Pool,
#       master_key: Optional[bytes] = None,
#       redis_client: Optional[aioredis.Redis] = None,
#       kms_provider: Optional[str] = None,
#   ):
#       self.db_pool = db_pool
#       self.redis = redis_client
#       self.kms_provider = kms_provider
#       if master_key:
#           self._master_kek = master_key
#       else:
#           self._master_kek = os.getenv("SECRETS_MASTER_KEY", secrets.token_bytes(32))
#           if isinstance(self._master_kek, str):
#               self._master_kek = self._master_kek.encode()
#
# REPLACE WITH:

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

# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 3: Replace _encrypt_dek and _decrypt_dek with async KMS versions
# ─────────────────────────────────────────────────────────────────────────────
#
# REMOVE these two methods (lines ~118-128):
#
#   def _encrypt_dek(self, dek: bytes) -> bytes:
#       """Encrypt DEK with master KEK using simple XOR for demo (use KMS in prod)"""
#       key = hashlib.sha256(self._master_kek).digest()
#       return bytes(a ^ b for a, b in zip(dek, key))
#
#   def _decrypt_dek(self, encrypted_dek: bytes) -> bytes:
#       """Decrypt DEK with master KEK"""
#       key = hashlib.sha256(self._master_kek).digest()
#       return bytes(a ^ b for a, b in zip(encrypted_dek, key))
#
# REPLACE WITH:

    async def _wrap_dek(self, dek: bytes) -> bytes:
        """Wrap (encrypt) a DEK using the configured KMS backend."""
        return await self._kms.wrap_key(dek)

    async def _unwrap_dek(self, wrapped_dek: bytes) -> bytes:
        """Unwrap (decrypt) a DEK using the configured KMS backend."""
        return await self._kms.unwrap_key(wrapped_dek)

# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 4: Update all callers of _encrypt_dek / _decrypt_dek
# ─────────────────────────────────────────────────────────────────────────────
#
# Search for all occurrences of:
#   self._encrypt_dek(dek)   →  await self._wrap_dek(dek)
#   self._decrypt_dek(...)   →  await self._unwrap_dek(...)
#
# There are exactly 4 call sites:
#   Line ~208:  encrypted_dek = self._encrypt_dek(dek)
#   Line ~304:  dek = self._decrypt_dek(encrypted_dek)
#   Line ~449:  encrypted_dek = self._encrypt_dek(dek)
#   Line ~653:  dek = self._decrypt_dek(row["encrypted_dek"])
#
# Replace each:
#   encrypted_dek = self._encrypt_dek(dek)       →  encrypted_dek = await self._wrap_dek(dek)
#   dek = self._decrypt_dek(encrypted_dek)        →  dek = await self._unwrap_dek(encrypted_dek)
#   dek = self._decrypt_dek(row["encrypted_dek"]) →  dek = await self._unwrap_dek(row["encrypted_dek"])
#
# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 5: Remove unused imports (hashlib is no longer used for DEK wrapping)
# ─────────────────────────────────────────────────────────────────────────────
#
# hashlib is still used for _hash_api_key — keep it.
# Remove only the import of 'os' if it's no longer used elsewhere.
# (It likely is still used — leave it.)
#
# ─────────────────────────────────────────────────────────────────────────────
# AUTOMATED PATCH (bash one-liner to apply changes 4 automatically):
# ─────────────────────────────────────────────────────────────────────────────
#
# Run from the backend/ directory:
#
#   sed -i \
#     's/encrypted_dek = self\._encrypt_dek(dek)/encrypted_dek = await self._wrap_dek(dek)/g' \
#     app/domain/services/secrets_manager.py
#
#   sed -i \
#     's/dek = self\._decrypt_dek(encrypted_dek)/dek = await self._unwrap_dek(encrypted_dek)/g' \
#     app/domain/services/secrets_manager.py
#
#   sed -i \
#     's/dek = self\._decrypt_dek(row\["encrypted_dek"\])/dek = await self._unwrap_dek(row["encrypted_dek"])/g' \
#     app/domain/services/secrets_manager.py
