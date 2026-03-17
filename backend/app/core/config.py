"""
Configuration Management
Loads settings from YAML files and environment variables
"""
import yaml
import os
from pathlib import Path
from typing import Any, Dict
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_ROOT / ".env"


def _normalize_origin(url: str) -> str:
    """Return scheme://host[:port] for CORS comparisons."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url

    if not parsed.scheme or not parsed.hostname:
        return url

    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    netloc = hostname
    if parsed.port is not None:
        netloc = f"{hostname}:{parsed.port}"

    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def _loopback_alias(origin: str) -> str | None:
    normalized = _normalize_origin(origin)
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return None

    hostname = parsed.hostname
    if hostname == "localhost":
        alias = "127.0.0.1"
    elif hostname == "127.0.0.1":
        alias = "localhost"
    else:
        return None

    netloc = alias if parsed.port is None else f"{alias}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


class Settings(BaseSettings):
    """Application settings loaded from environment"""
    
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="allow"
    )
    
    environment: str = "development"
    debug: bool = True

    # API Settings
    api_prefix: str = "/api/v1"

    # CORS — populated from CORS_ORIGINS env var (comma-separated)
    # Falls back to FRONTEND_URL if CORS_ORIGINS is not set
    cors_origins: list[str] = ["http://localhost:3000"]
    frontend_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    # Redis/Queue
    redis_url: str = "redis://localhost:6379"

    # Authentication
    jwt_secret: str | None = None
    secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_leeway_seconds: int = 30

    @field_validator("jwt_secret", "secret_key", mode="before")
    @classmethod
    def _normalize_secret_fields(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.lower() in {"null", "none", "undefined"}:
            return None
        return cleaned

    @property
    def allowed_origins(self) -> list[str]:
        """
        Compute the final list of allowed CORS origins.

        Priority:
        1. CORS_ORIGINS env var (comma-separated) if explicitly set
        2. FRONTEND_URL as a single-origin fallback
        """
        # If cors_origins was explicitly set via env and isn't the default
        if self.cors_origins and self.cors_origins != ["http://localhost:3000"]:
            return self._expand_local_aliases(self.cors_origins)
        # Always include the frontend URL
        origins = self._expand_local_aliases([self.frontend_url])
        api_origin = _normalize_origin(self.api_base_url) if self.api_base_url else None
        if api_origin and api_origin not in origins:
            origins.append(api_origin)
        return origins

    @staticmethod
    def _expand_local_aliases(origins: list[str]) -> list[str]:
        expanded: list[str] = []
        seen: set[str] = set()

        for origin in origins:
            normalized = _normalize_origin(origin)
            if normalized not in seen:
                seen.add(normalized)
                expanded.append(normalized)

            alias = _loopback_alias(normalized)
            if alias and alias not in seen:
                seen.add(alias)
                expanded.append(alias)

        return expanded

    @property
    def effective_jwt_secret(self) -> str | None:
        """
        Primary JWT signing secret.

        Resolution order:
        1. JWT_SECRET
        2. SECRET_KEY (legacy fallback)
        """
        return self.jwt_secret or self.secret_key


@lru_cache
def get_settings() -> Settings:
    """Shared cached settings instance."""
    return Settings()


class ConfigManager:
    """Manages loading and merging configuration from multiple sources"""
    
    def __init__(self, env: str = "development"):
        self.env = env
        self.config_dir = Path(__file__).parent.parent.parent / "config"
        self._config: Dict[str, Any] = {}
        self._load_config()
    
    def _load_config(self) -> None:
        """Load configuration files in order of precedence"""
        # Load default config if exists
        default_path = self.config_dir / "default.yaml"
        if default_path.exists():
            self._config = self._load_yaml(default_path)
        
        # Load environment-specific config
        env_path = self.config_dir / f"{self.env}.yaml"
        if env_path.exists():
            env_config = self._load_yaml(env_path)
            self._deep_merge(self._config, env_config)
        
        # Load provider config
        providers_path = self.config_dir / "providers.yaml"
        if providers_path.exists():
            providers_config = self._load_yaml(providers_path)
            self._deep_merge(self._config, providers_config)
        
        # Substitute environment variables
        self._substitute_env_vars(self._config)
    
    def _load_yaml(self, path: Path) -> Dict:
        """Load YAML file"""
        with open(path, 'r') as f:
            return yaml.safe_load(f) or {}
    
    def _substitute_env_vars(self, config: Dict) -> None:
        """Replace ${VAR_NAME} with environment variable values"""
        for key, value in config.items():
            if isinstance(value, dict):
                self._substitute_env_vars(value)
            elif isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                env_var = value[2:-1]
                config[key] = os.getenv(env_var, value)
    
    def _deep_merge(self, base: Dict, override: Dict) -> None:
        """Recursively merge override into base"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation
        Example: config.get("providers.stt.active") -> "deepgram"
        """
        keys = key_path.split('.')
        value = self._config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def get_provider_config(self, provider_type: str) -> Dict:
        """Get active provider configuration"""
        active = self.get(f"providers.{provider_type}.active")
        if not active:
            raise ValueError(f"No active {provider_type} provider configured")
        
        config = self.get(f"providers.{provider_type}.{active}", {})
        return config
    
    def get_websocket_config(self) -> Dict:
        """Get WebSocket configuration with defaults"""
        return self.get("websocket", {
            "max_connections": 1000,
            "connection_timeout_seconds": 300,
            "heartbeat_interval_seconds": 30,
            "heartbeat_timeout_seconds": 5,
            "max_message_size_bytes": 65536,
            "audio_chunk_size_ms": 80,
            "audio_buffer_size": 100,
            "transcript_buffer_size": 50,
            "enable_latency_tracking": True,
            "latency_warning_threshold_ms": 500,
            "latency_error_threshold_ms": 1000,
            "use_binary_audio": True,
            "use_json_audio": False,
        })
