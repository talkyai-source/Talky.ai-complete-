"""
Provider Validation Module
Validates all provider configurations on startup
"""
import os
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a configuration validation check."""
    provider: str
    setting: str
    is_valid: bool
    message: str


class ProviderValidator:
    """
    Validates provider configurations at startup.
    
    Ensures all required API keys and settings are present
    before the application starts accepting requests.
    """
    
    # Required environment variables by provider
    REQUIRED_ENV_VARS = {
        "stt": [("DEEPGRAM_API_KEY", "Deepgram STT provider")],
        "tts": [("CARTESIA_API_KEY", "Cartesia TTS provider")],
        "llm": [("GROQ_API_KEY", "Groq LLM provider")],
        "telephony": [
            ("VONAGE_API_KEY", "Vonage telephony"),
            ("VONAGE_API_SECRET", "Vonage telephony"),
        ],
        "database": [
            ("SUPABASE_URL", "Supabase database"),
            ("SUPABASE_SERVICE_KEY", "Supabase database"),
        ],
        "cache": [("REDIS_URL", "Redis cache (optional)")],
    }
    
    # Optional but recommended
    OPTIONAL_ENV_VARS = {
        "telephony": [("VONAGE_APP_ID", "Vonage application ID")],
    }
    
    def __init__(self, strict: bool = False):
        """
        Initialize validator.
        
        Args:
            strict: If True, treat warnings as errors
        """
        self.strict = strict
        self.results: List[ValidationResult] = []
    
    def validate_all(self) -> Tuple[bool, List[ValidationResult]]:
        """
        Validate all provider configurations.
        
        Returns:
            Tuple of (all_valid, list of results)
        """
        self.results = []
        
        # Check required vars
        for provider, vars_list in self.REQUIRED_ENV_VARS.items():
            for env_var, description in vars_list:
                value = os.getenv(env_var)
                
                # Special case: Redis is optional in development
                if env_var == "REDIS_URL":
                    if not value:
                        self._add_warning(provider, env_var, 
                            f"{description} not configured (in-memory fallback will be used)")
                    else:
                        self._add_success(provider, env_var, f"{description} configured")
                else:
                    if not value:
                        self._add_error(provider, env_var, 
                            f"{description} requires {env_var} to be set")
                    else:
                        self._add_success(provider, env_var, f"{description} configured")
        
        # Check optional vars
        for provider, vars_list in self.OPTIONAL_ENV_VARS.items():
            for env_var, description in vars_list:
                value = os.getenv(env_var)
                if not value:
                    self._add_warning(provider, env_var,
                        f"{description} not configured (optional)")
                else:
                    self._add_success(provider, env_var, f"{description} configured")
        
        # Determine overall validity
        errors = [r for r in self.results if not r.is_valid and "optional" not in r.message.lower()]
        all_valid = len(errors) == 0
        
        return all_valid, self.results
    
    def _add_success(self, provider: str, setting: str, message: str):
        """Add successful validation result."""
        self.results.append(ValidationResult(
            provider=provider,
            setting=setting,
            is_valid=True,
            message=message
        ))
    
    def _add_error(self, provider: str, setting: str, message: str):
        """Add error validation result."""
        self.results.append(ValidationResult(
            provider=provider,
            setting=setting,
            is_valid=False,
            message=message
        ))
    
    def _add_warning(self, provider: str, setting: str, message: str):
        """Add warning validation result."""
        self.results.append(ValidationResult(
            provider=provider,
            setting=setting,
            is_valid=not self.strict,  # Warnings become errors in strict mode
            message=f"WARNING: {message}"
        ))
    
    def log_results(self):
        """Log all validation results."""
        errors = [r for r in self.results if not r.is_valid]
        warnings = [r for r in self.results if r.is_valid and "WARNING" in r.message]
        successes = [r for r in self.results if r.is_valid and "WARNING" not in r.message]
        
        if successes:
            logger.info("Provider configuration validated:")
            for r in successes:
                logger.info(f"  ✓ [{r.provider}] {r.message}")
        
        if warnings:
            for r in warnings:
                logger.warning(f"  ⚠ [{r.provider}] {r.message}")
        
        if errors:
            logger.error("Provider configuration errors:")
            for r in errors:
                logger.error(f"  ✗ [{r.provider}] {r.message}")
    
    def get_error_summary(self) -> Optional[str]:
        """Get summary of errors for exception message."""
        errors = [r for r in self.results if not r.is_valid]
        if not errors:
            return None
        
        lines = ["Provider configuration errors:"]
        for r in errors:
            lines.append(f"  - {r.setting}: {r.message}")
        return "\n".join(lines)


def validate_providers_on_startup(strict: bool = False) -> None:
    """
    Validate all providers at startup.
    
    Call this from FastAPI startup event.
    
    Args:
        strict: If True, fail on warnings too
        
    Raises:
        RuntimeError: If required configuration is missing
    """
    validator = ProviderValidator(strict=strict)
    all_valid, results = validator.validate_all()
    validator.log_results()
    
    if not all_valid:
        error_msg = validator.get_error_summary()
        raise RuntimeError(error_msg)
    
    logger.info("All provider configurations validated successfully")
