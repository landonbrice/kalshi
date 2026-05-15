from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="KALSHI_",
        extra="ignore",
    )

    api_key_id: str = Field(..., alias="KALSHI_API_KEY_ID")
    private_key_path: Path = Field(..., alias="KALSHI_PRIVATE_KEY_PATH")
    base_url: str = Field(
        default="https://api.elections.kalshi.com/trade-api/v2",
        alias="KALSHI_BASE_URL",
    )
    db_path: Path = Field(default=Path("./data/kalshi.db"), alias="KALSHI_DB_PATH")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
