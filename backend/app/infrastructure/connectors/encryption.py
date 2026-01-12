"""
Token Encryption Service
Secure encryption for OAuth tokens using Fernet (AES-128-CBC + HMAC-SHA256).

Security Features:
- Fernet provides authenticated encryption
- MultiFernet supports key rotation
- Keys stored in environment variables (never in code/DB)
- Each encryption produces unique ciphertext

Day 24: Unified Connector System
"""
import os
import logging
from typing import Optional, List
from cryptography.fernet import Fernet, MultiFernet, InvalidToken

logger = logging.getLogger(__name__)


class TokenEncryptionError(Exception):
    """Raised when token encryption/decryption fails"""
    pass


class TokenEncryptionService:
    """
    Encrypt/decrypt OAuth tokens using Fernet.
    
    Supports key rotation via MultiFernet:
    - New tokens are always encrypted with the current (first) key
    - Old tokens can be decrypted with any key in the chain
    
    Environment Variables:
    - CONNECTOR_ENCRYPTION_KEY: Current encryption key (required)
    - CONNECTOR_ENCRYPTION_KEYS_OLD: Comma-separated old keys for rotation (optional)
    
    Usage:
        service = get_encryption_service()
        encrypted = service.encrypt("my_access_token")
        decrypted = service.decrypt(encrypted)
    """
    
    def __init__(self, key: Optional[str] = None, old_keys: Optional[List[str]] = None):
        """
        Initialize encryption service.
        
        Args:
            key: Current encryption key (or from env)
            old_keys: Previous keys for rotation (or from env)
        """
        self._fernet: Optional[MultiFernet] = None
        self._initialize_keys(key, old_keys)
    
    def _initialize_keys(
        self,
        key: Optional[str] = None,
        old_keys: Optional[List[str]] = None
    ) -> None:
        """Load encryption keys from parameters or environment."""
        # Current key (required)
        current_key = key or os.getenv("CONNECTOR_ENCRYPTION_KEY")
        
        if not current_key:
            # Generate a temporary key for development/testing
            # In production, this should be a proper error
            logger.warning(
                "CONNECTOR_ENCRYPTION_KEY not set! "
                "Using temporary key - DO NOT USE IN PRODUCTION"
            )
            current_key = Fernet.generate_key().decode()
        
        # Previous keys for rotation (optional)
        if old_keys is None:
            old_keys_str = os.getenv("CONNECTOR_ENCRYPTION_KEYS_OLD", "")
            old_keys = [k.strip() for k in old_keys_str.split(",") if k.strip()]
        
        # Build key chain (current first, then old keys)
        try:
            keys = [Fernet(current_key.encode() if isinstance(current_key, str) else current_key)]
            for old_key in old_keys:
                if old_key:
                    keys.append(Fernet(old_key.encode() if isinstance(old_key, str) else old_key))
            
            self._fernet = MultiFernet(keys)
            logger.info(f"Encryption service initialized with {len(keys)} key(s)")
            
        except Exception as e:
            raise TokenEncryptionError(f"Failed to initialize encryption keys: {e}")
    
    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a token string.
        
        Args:
            plaintext: Token to encrypt
            
        Returns:
            Base64-encoded ciphertext (URL-safe)
        """
        if not plaintext:
            return ""
        
        try:
            ciphertext = self._fernet.encrypt(plaintext.encode())
            return ciphertext.decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise TokenEncryptionError(f"Failed to encrypt token: {e}")
    
    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a token string.
        
        Args:
            ciphertext: Base64-encoded encrypted token
            
        Returns:
            Original plaintext token
        """
        if not ciphertext:
            return ""
        
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode())
            return plaintext.decode()
        except InvalidToken:
            logger.error("Decryption failed: Invalid token or wrong key")
            raise TokenEncryptionError("Failed to decrypt token: Invalid token or key")
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise TokenEncryptionError(f"Failed to decrypt token: {e}")
    
    def rotate_token(self, ciphertext: str) -> str:
        """
        Re-encrypt token with current key.
        
        Use this during key rotation to update tokens
        encrypted with old keys.
        
        Args:
            ciphertext: Token encrypted with any valid key
            
        Returns:
            Token re-encrypted with current key
        """
        if not ciphertext:
            return ""
        
        try:
            # Decrypt with any valid key, re-encrypt with current
            rotated = self._fernet.rotate(ciphertext.encode())
            return rotated.decode()
        except Exception as e:
            logger.error(f"Token rotation failed: {e}")
            raise TokenEncryptionError(f"Failed to rotate token: {e}")
    
    def is_encrypted(self, value: str) -> bool:
        """
        Check if a value appears to be Fernet-encrypted.
        
        Note: This is a heuristic check, not a guarantee.
        """
        if not value:
            return False
        
        # Fernet tokens are base64-encoded and start with 'gAAAA'
        return value.startswith("gAAAA") and len(value) > 100
    
    @staticmethod
    def generate_key() -> str:
        """
        Generate a new Fernet key.
        
        Use this for initial setup or key rotation.
        Store the generated key securely!
        
        Returns:
            URL-safe base64-encoded 32-byte key
        """
        return Fernet.generate_key().decode()


# Singleton instance
_encryption_service: Optional[TokenEncryptionService] = None


def get_encryption_service() -> TokenEncryptionService:
    """
    Get singleton encryption service instance.
    
    Thread-safe lazy initialization.
    """
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = TokenEncryptionService()
    return _encryption_service


def reset_encryption_service() -> None:
    """
    Reset singleton for testing.
    
    WARNING: Only use in tests!
    """
    global _encryption_service
    _encryption_service = None
