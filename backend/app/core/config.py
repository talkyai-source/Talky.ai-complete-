"""
Configuration Management
Loads settings from YAML files and environment variables
"""
import yaml
import os
from pathlib import Path
from typing import Any, Dict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment"""
    
    environment: str = "development"
    debug: bool = True
    
    # API Settings
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]
    
    # Redis/Queue
    redis_url: str = "redis://localhost:6379"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


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
