"""
Tests for Token Encryption Service
Day 24: Unified Connector System
"""
import pytest
import os
from unittest.mock import patch

# Set test environment before imports
os.environ["CONNECTOR_ENCRYPTION_KEY"] = "test_key_for_testing_only_32bytes!"


class TestTokenEncryption:
    """Tests for Fernet encryption service"""
    
    def setup_method(self):
        """Reset singleton for each test"""
        from app.infrastructure.connectors.encryption import reset_encryption_service
        reset_encryption_service()
    
    def test_encrypt_decrypt_roundtrip(self):
        """Token survives encrypt/decrypt cycle"""
        from app.infrastructure.connectors.encryption import get_encryption_service
        
        service = get_encryption_service()
        original = "my_super_secret_access_token_12345"
        
        encrypted = service.encrypt(original)
        decrypted = service.decrypt(encrypted)
        
        assert decrypted == original
        assert encrypted != original  # Should be different
    
    def test_empty_string_handling(self):
        """Empty strings return empty strings"""
        from app.infrastructure.connectors.encryption import get_encryption_service
        
        service = get_encryption_service()
        
        assert service.encrypt("") == ""
        assert service.decrypt("") == ""
    
    def test_different_ciphertext_each_time(self):
        """Same plaintext produces different ciphertext (due to IV)"""
        from app.infrastructure.connectors.encryption import get_encryption_service
        
        service = get_encryption_service()
        plaintext = "same_token_value"
        
        encrypted1 = service.encrypt(plaintext)
        encrypted2 = service.encrypt(plaintext)
        
        # Different ciphertext
        assert encrypted1 != encrypted2
        
        # Both decrypt to same value
        assert service.decrypt(encrypted1) == plaintext
        assert service.decrypt(encrypted2) == plaintext
    
    def test_decrypt_with_wrong_key_fails(self):
        """Decryption fails with incorrect key"""
        from app.infrastructure.connectors.encryption import (
            TokenEncryptionService,
            TokenEncryptionError
        )
        from cryptography.fernet import Fernet
        
        # Encrypt with one key
        key1 = Fernet.generate_key().decode()
        service1 = TokenEncryptionService(key=key1)
        encrypted = service1.encrypt("secret_token")
        
        # Try to decrypt with different key
        key2 = Fernet.generate_key().decode()
        service2 = TokenEncryptionService(key=key2)
        
        with pytest.raises(TokenEncryptionError):
            service2.decrypt(encrypted)
    
    def test_key_rotation_with_multifernet(self):
        """Old tokens can be decrypted, new tokens use new key"""
        from app.infrastructure.connectors.encryption import TokenEncryptionService
        from cryptography.fernet import Fernet
        
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        
        # Encrypt with old key
        old_service = TokenEncryptionService(key=old_key)
        old_encrypted = old_service.encrypt("old_token_value")
        
        # Create service with new key + old key for rotation
        rotated_service = TokenEncryptionService(key=new_key, old_keys=[old_key])
        
        # Can still decrypt old token
        decrypted = rotated_service.decrypt(old_encrypted)
        assert decrypted == "old_token_value"
        
        # New encryptions use new key
        new_encrypted = rotated_service.encrypt("new_token_value")
        assert rotated_service.decrypt(new_encrypted) == "new_token_value"
    
    def test_is_encrypted_check(self):
        """Heuristic check for Fernet-encrypted values"""
        from app.infrastructure.connectors.encryption import get_encryption_service
        
        service = get_encryption_service()
        
        # Encrypted value should be detected
        encrypted = service.encrypt("test_token")
        assert service.is_encrypted(encrypted) is True
        
        # Plain text should not be detected
        assert service.is_encrypted("plain_text") is False
        assert service.is_encrypted("") is False
        assert service.is_encrypted("short") is False
    
    def test_generate_key_format(self):
        """Generated keys are valid Fernet keys"""
        from app.infrastructure.connectors.encryption import TokenEncryptionService
        from cryptography.fernet import Fernet
        
        key = TokenEncryptionService.generate_key()
        
        # Key should be valid (Fernet accepts it)
        fernet = Fernet(key.encode())
        
        # Can encrypt/decrypt with generated key
        encrypted = fernet.encrypt(b"test")
        decrypted = fernet.decrypt(encrypted)
        assert decrypted == b"test"
    
    def test_rotate_token(self):
        """Token rotation re-encrypts with current key"""
        from app.infrastructure.connectors.encryption import TokenEncryptionService
        from cryptography.fernet import Fernet
        
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        
        # Encrypt with old key
        old_service = TokenEncryptionService(key=old_key)
        old_encrypted = old_service.encrypt("my_token")
        
        # Create rotated service
        rotated_service = TokenEncryptionService(key=new_key, old_keys=[old_key])
        
        # Rotate the token
        new_encrypted = rotated_service.rotate_token(old_encrypted)
        
        # New encryption should be different
        assert new_encrypted != old_encrypted
        
        # Should decrypt to same value
        assert rotated_service.decrypt(new_encrypted) == "my_token"
        
        # New-only service (without old key) should be able to decrypt rotated token
        new_only_service = TokenEncryptionService(key=new_key)
        assert new_only_service.decrypt(new_encrypted) == "my_token"
