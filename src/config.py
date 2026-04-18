from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./tracker.db"  # override via env for PostgreSQL
    ENVIRONMENT: str = "local"
    APP_VERSION: str = "0.1.0"
    GEMINI_API_KEY: str | None = None
    # API model id for "Gemini 3.1 Flash Lite".
    GEMINI_MODEL: str = "gemini-3.1-flash-lite-preview"
    GEMINI_TIMEOUT_SECONDS: int = 90
    GEMINI_RETRY_MAX_ATTEMPTS: int = 4
    GEMINI_RETRY_BASE_DELAY_SECONDS: float = 1.5
    GEMINI_RETRY_MAX_DELAY_SECONDS: float = 20.0
    LLM_DEFAULT_OUTPUT_LANGUAGE: str = "tr"
    YOUTUBE_PROXY_ENABLED: bool = False
    YOUTUBE_PROXY_MODE: str = "direct"  # direct | rotating
    YOUTUBE_PROXY_RETRIES: int = 3
    YOUTUBE_PROXY_BACKOFF_SECONDS: float = 1.0
    YOUTUBE_PROXY_MAX_BACKOFF_SECONDS: float = 8.0
    YOUTUBE_PROXY_FAILURE_THRESHOLD: int = 2
    YOUTUBE_PROXY_COOLDOWN_SECONDS: int = 180
    YOUTUBE_REQUEST_MIN_DELAY_SECONDS: float = 0.5
    YOUTUBE_REQUEST_MAX_DELAY_SECONDS: float = 1.5
    YOUTUBE_MAX_CONCURRENT_REQUESTS: int = 2
    WEBSHARE_PROXY_USERNAME: str | None = None
    WEBSHARE_PROXY_PASSWORD: str | None = None
    WEBSHARE_PROXY_HOST: str | None = None
    WEBSHARE_PROXY_PORT: int | None = None
    WEBSHARE_PROXY_LIST: str | None = None  # comma-separated host:port entries
    WEBSHARE_PROXY_FILTER_IP_LOCATIONS: str | None = None  # comma-separated country codes
    WATCH_CONFIG_PATH: str = "config/watched_channels.yaml"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
