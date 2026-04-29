from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # External services
    NEON_DATABASE_URL: str = Field(..., description="Postgres connection string for Neon")
    HERA_API_KEY: str = Field("", description="Hera public REST API key (x-api-key)")
    GOOGLE_API_KEY: str = Field("", description="Gemini / Google AI Studio key")

    # Hera mock mode — short-circuits live calls so dev work doesn't burn quota
    HERA_MOCK: bool = Field(False, description="If true, returns canned mp4 URLs instead of calling Hera")

    # Quota guardrails — Hera plan is 200 videos / 100 images per month
    MAX_RENDERS_PER_RUN: int = 12
    MONTHLY_RENDER_HARD_CAP: int = 200
    MONTHLY_RENDER_WARN_THRESHOLD: int = 180  # warn UI when within 20 of cap

    # Models
    GEMINI_MODEL: str = "gemini-3-flash-preview"
    GEMINI_PRO_MODEL: str = "gemini-3.1-pro-preview"
    EMBEDDING_MODEL: str = "gemini-embedding-001"

    # Run loop budgets
    ACCEPT_THRESHOLD: float = 0.7
    MAX_ATTEMPTS_PER_SHOT: int = 3
    RESEARCH_BUDGET: int = 3
    MAX_REPLANS: int = 12
    DURATION_FILTER_BUFFER_SECONDS: float = 5.0  # ± window when filtering templates by duration
    RENDER_TIMEOUT_SECONDS: int = 1200  # Hera renders can take 3-5 min on busy days, esp. complex templates
    POLL_INTERVAL_SECONDS: int = 10

    # Output defaults
    DEFAULT_ASPECT_RATIO: str = "9:16"
    DEFAULT_FPS: int = 30
    DEFAULT_RESOLUTION: str = "720p"
    DEFAULT_SHOT_DURATION: float = 5.0
    TARGET_TOTAL_DURATION: float = 90

    # Templates scrape
    TEMPLATE_CATEGORIES: list[str] = [
        "infographics", "logos", "text", "socialmedia",
        "ads", "overlays", "maps", "others",
    ]
    SCRAPE_PAGE_SIZE: int = 24
    SCRAPE_PACE_SECONDS: float = 0.3        # between Hera page fetches
    SCRAPE_RECORD_PACE_SECONDS: float = 0.3  # between per-record upserts (paces Gemini embeds)
    SCRAPE_DEFAULT_PUBLIC: bool = True

    # Template search weights — must sum to 1.0
    SEARCH_WEIGHT_SIM: float = 0.65
    SEARCH_WEIGHT_POPULAR: float = 0.20
    SEARCH_WEIGHT_TRGM: float = 0.15

    # File store (mp4s only — DB state lives on Neon)
    DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"

    @property
    def RENDERS_DIR(self) -> Path:  # noqa: N802 — uppercase to match the other settings constants
        return self.DATA_DIR / "renders"


settings = Settings()  # type: ignore[call-arg]
settings.RENDERS_DIR.mkdir(parents=True, exist_ok=True)
