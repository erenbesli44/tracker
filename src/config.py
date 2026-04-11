from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./tracker.db"
    ENVIRONMENT: str = "local"
    APP_VERSION: str = "0.1.0"
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TIMEOUT_SECONDS: int = 90
    LLM_DEFAULT_OUTPUT_LANGUAGE: str = "tr"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
