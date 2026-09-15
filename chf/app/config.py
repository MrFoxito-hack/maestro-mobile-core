from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MAEstro CHF"
    database_path: Path = Path("./data/chf.db")
    default_grant_bytes: int = 10 * 1024 * 1024
    validity_time_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CHF_", extra="ignore")

    def prepare(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare()
    return settings

