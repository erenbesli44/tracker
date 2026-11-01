from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./tracker.db"  # override via env for PostgreSQL
    ENVIRONMENT: str = "local"
    APP_VERSION: str = "0.1.0"
    API_KEY: str | None = None  # required X-API-Key header value for all endpoints except /health
    LLM_DEFAULT_OUTPUT_LANGUAGE: str = "tr"
    MINIMAX_BASE_URL: str | None = None  # OpenAI-compatible, e.g. https://api.minimax.io/v1
    MINIMAX_API_KEY: str | None = None
    MINIMAX_MODEL: str = "MiniMax-M3.1-Flash"
    # Per-request timeout for MiniMax. MiniMax commonly takes 60-180s to emit
    # a long JSON analysis, so the default is generous.
    MINIMAX_TIMEOUT_SECONDS: int = 240
    MINIMAX_RETRY_MAX_ATTEMPTS: int = 4
    MINIMAX_RETRY_BASE_DELAY_SECONDS: float = 1.5
    MINIMAX_RETRY_MAX_DELAY_SECONDS: float = 20.0
    # Characters of (timestamp-rendered) transcript sent to the analysis call (MiniMax-M3.1-Flash).
    # 80k sends a ~60 min video whole (~1.2k rendered chars per minute, ~25k tokens); longer
    # transcripts are cut to head/middle/tail at this size. Tune via env.
    ANALYSIS_TRANSCRIPT_MAX_CHARS: int = 80000
    # > 0: one [MM:SS] marker per that many seconds instead of [MM:SS-MM:SS] on every ~5 s caption
    # segment (-46 % transcript tokens, 22-min video 14.8k -> 10.0k prompt tokens). Opt-in: on
    # 2026-11-01, at medium effort, the sparse form dropped 1-2 borderline topics in 3/3 runs
    # while the per-segment form kept all 8 in 3/3 (one video; see README). Enable after a
    # wider eval. <= 0 keeps the per-segment markers.
    ANALYSIS_TIMESTAMP_EVERY_SECONDS: int = 0
    MINIMAX_SYSTEM_PROMPT: str = (
        "You are a helpful assistant that always responds with a single valid "
        "JSON object and no surrounding prose."
    )
    # reasoning_effort per call type (MiniMax-M3.1-Flash: low|medium|high|xhigh|max). The model
    # uses max when the field is omitted, which spends ~85-90 % of a call on reasoning tokens
    # (measured 2026-11-01). Empty = omit the field. Outlook stays on the model default until the
    # labelled eval (scripts/eval_outlook_prompts.py) clears a lower level: a wrong arrow under a
    # real name is the worst failure. "low" is never used: it dropped 40 % of key levels.
    MINIMAX_EFFORT_ANALYSIS: str = "medium"
    MINIMAX_EFFORT_OUTLOOK: str = ""
    MINIMAX_EFFORT_QUOTE_CLEAN: str = "medium"
    MINIMAX_EFFORT_PARTICIPANTS: str = "medium"
    MINIMAX_EFFORT_INFERENCE: str = "medium"
    MINIMAX_EFFORT_DEFAULT: str = "medium"  # legacy summary / classification / thesis prompts
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
    YOUTUBE_TRANSCRIPT_UNAVAILABLE_RETRY_BASE_HOURS: float = 6.0
    YOUTUBE_TRANSCRIPT_UNAVAILABLE_RETRY_MAX_DAYS: float = 7.0
    YOUTUBE_TRANSCRIPT_PROVIDER_RETRY_BASE_MINUTES: float = 30.0
    YOUTUBE_TRANSCRIPT_PROVIDER_RETRY_MAX_HOURS: float = 6.0
    WEBSHARE_PROXY_USERNAME: str | None = None
    WEBSHARE_PROXY_PASSWORD: str | None = None
    WEBSHARE_PROXY_HOST: str | None = None
    WEBSHARE_PROXY_PORT: int | None = None
    WEBSHARE_PROXY_LIST: str | None = None  # comma-separated host:port entries
    WEBSHARE_PROXY_FILTER_IP_LOCATIONS: str | None = None  # comma-separated country codes
    WEBSHARE_API_KEY: str | None = None  # when set, fetch live proxy list + creds from Webshare API
    WEBSHARE_API_BASE_URL: str = "https://proxy.webshare.io/api/v2"
    WEBSHARE_API_CACHE_TTL_SECONDS: int = 600  # refresh Webshare inventory at most every 10 min
    WEBSHARE_API_TIMEOUT_SECONDS: float = 10.0
    WATCH_CONFIG_PATH: str = "config/watched_channels.yaml"
    BROKER_WATCH_CONFIG_PATH: str = "config/watched_brokers.yaml"
    BIST_TICKERS_PATH: str = "config/bist_tickers.txt"
    REDIS_URL: str | None = None  # e.g. redis://:password@host:6379/0

    # Twitter / X bot — posts new video summaries as tweets.
    TWITTER_API_KEY: str | None = None
    TWITTER_API_SECRET: str | None = None
    TWITTER_ACCESS_TOKEN: str | None = None
    TWITTER_ACCESS_TOKEN_SECRET: str | None = None
    TWITTER_HANDLE: str | None = None  # used to build tweet URLs; falls back to "i"
    TWITTER_DRY_RUN: bool = False  # when True, log tweet text instead of posting
    TWITTER_MAX_POSTS_PER_RUN: int = 5
    TWITTER_FRESHNESS_DAYS: int = 7  # ignore summaries older than this on first run

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
