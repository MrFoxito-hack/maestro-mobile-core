from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    app_name: str = "MAEstro CHF"
    database_path: Path = Path("./data/chf.db")
    default_grant_bytes: int = Field(default=10 * 1024 * 1024, gt=0, le=2**53-1)
    validity_time_seconds: int = Field(default=300, ge=1, le=86400)
    orphan_grace_seconds: int = Field(default=60, ge=1)
    admin_token: SecretStr | None = None
    reader_token: SecretStr | None = None
    sbi_tokens: dict[str, SecretStr] = Field(default_factory=dict)
    sbi_lab_no_auth: bool = False
    max_request_bytes: int = Field(default=262144, ge=1024, le=1048576)

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CHF_", extra="ignore")

    def prepare(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_security(self, *, management: bool):
        from uuid import UUID
        if management:
            if not self.admin_token or len(self.admin_token.get_secret_value()) < 32:
                raise ValueError('CHF_ADMIN_TOKEN must contain at least 32 characters')
            if self.reader_token and (len(self.reader_token.get_secret_value()) < 32 or
                    self.reader_token.get_secret_value() == self.admin_token.get_secret_value()):
                raise ValueError('CHF_READER_TOKEN must be distinct and at least 32 characters')
        elif not self.sbi_lab_no_auth:
            if not self.sbi_tokens:
                raise ValueError('configure CHF_SBI_TOKENS or explicitly opt into isolated lab no-auth')
            for identity, token in self.sbi_tokens.items():
                UUID(identity)
                if len(token.get_secret_value()) < 32:
                    raise ValueError('each SBI token must contain at least 32 characters')
            values = [token.get_secret_value() for token in self.sbi_tokens.values()]
            if len(set(values)) != len(values):
                raise ValueError('each SMF requires a distinct token')
            if self.admin_token and self.admin_token.get_secret_value() in values:
                raise ValueError('administrative and SBI credentials must differ')


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare()
    return settings
