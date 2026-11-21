"""Runtime configuration, read from environment variables only."""
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("app.config")


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_keys: list[str] = field(default_factory=list)
    model: str = "gemini/gemini-2.0-flash"
    database_url: str = "sqlite:////data/app.db"
    allowed_origins: list[str] = field(default_factory=list)
    enable_failure_simulation: bool = False
    default_system_prompt: str = "You are a helpful, concise AI assistant."
    cache_type: str = "local"  # "local" (in-memory) or "disk"
    cache_dir: str = "/data/cache"
    max_tokens: int = 1024
    request_timeout: int = 30
    max_history_messages: int = 40

    @property
    def db_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must look like sqlite:////data/app.db")
        return self.database_url[len(prefix):]


def load_settings() -> Settings:
    env = os.environ.get
    raw_origins = env("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173")
    origins = ["*"] if raw_origins.strip() == "*" else _csv(raw_origins)
    settings = Settings(
        api_keys=_csv(env("LLM_API_KEYS", "")),
        model=env("LLM_MODEL", Settings.model),
        database_url=env("DATABASE_URL", Settings.database_url),
        allowed_origins=origins,
        enable_failure_simulation=_bool(env("ENABLE_FAILURE_SIMULATION", "false")),
        default_system_prompt=env("DEFAULT_SYSTEM_PROMPT", Settings.default_system_prompt),
        cache_type=env("CACHE_TYPE", "local").strip().lower(),
        cache_dir=env("CACHE_DIR", Settings.cache_dir),
        max_tokens=int(env("MAX_TOKENS", Settings.max_tokens)),
        request_timeout=int(env("REQUEST_TIMEOUT", Settings.request_timeout)),
        max_history_messages=int(env("MAX_HISTORY_MESSAGES", Settings.max_history_messages)),
    )
    if len(settings.api_keys) < 2:
        log.warning("LLM_API_KEYS has %d key(s); at least 2 are needed for failover.", len(settings.api_keys))
    return settings
