from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "EMS educativo 4G/5G"
    api_prefix: str = "/api/v1"
    secret_key: str = "change-this-development-key"
    access_token_minutes: int = 480
    execution_mode: Literal["simulated", "local", "remote"] = "simulated"
    catalog_path: Path = Path(__file__).resolve().parents[1] / "catalog" / "scenarios.json"
    profiles_path: Path = Path(__file__).resolve().parents[1] / "catalog" / "component_profiles.json"
    mock_state_path: Path = Path(__file__).resolve().parents[1] / "catalog" / "mock_states.json"
    database_path: Path = Path("./data/ems.db")
    laboratory_database_path: Path | None = None
    laboratory_ai_enabled: bool = False
    capture_dir: Path = Path("./data/captures")
    backup_dir: Path = Path("./data/backups")
    allowed_config_roots: list[Path] = [Path("./data/testbed-config")]
    allowed_interfaces: list[str] = ["lo", "ogstun", "enp0s8", "any"]
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "open5gs"
    enable_mongo: bool = False
    enable_real_captures: bool = False
    metrics_collection_interval_seconds: int = 10
    metrics_retention_days: int = 30
    open5gs_info_port: int = 9090
    operation_output_limit: int = 100_000
    chf_management_enabled: bool = False
    chf_management_port: int = 8082
    # Management stays on the core loopback; credentials never reach the browser.
    chf_reader_token_file: str = "/home/emsadmin/maestro-charging/management/reader.token"
    chf_admin_environment_file: str = "/home/emsadmin/maestro-charging/management/management.env"
    nwdaf_enabled: bool = False
    multi_upf_enabled: bool = False
    nwdaf_port: int = 8085
    nwdaf_token_file: str = "/home/emsadmin/maestro-charging/nwdaf/nwdaf.token"
    testbed_host: str | None = None
    ssh_port: int = 22
    upf_ssh_port: int = 2223
    upf2_ssh_port: int = 2224
    gnb_ssh_port: int = 2225
    ue_ssh_port: int = 2226
    ssh_user: str | None = None
    ssh_key_path: Path | None = None
    ssh_password: str | None = None
    ssh_strict_host_key: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    model_config = SettingsConfigDict(env_file=".env", env_prefix="EMS_", extra="ignore")

    @field_validator("allowed_config_roots", mode="before")
    @classmethod
    def parse_roots(cls, value):
        if isinstance(value, str):
            if value.startswith("[") and value.endswith("]"):
                try:
                    import json
                    parsed = json.loads(value)
                    return [Path(item.strip()) for item in parsed if item.strip()]
                except Exception:
                    pass
            return [Path(item.strip()) for item in value.split(",") if item.strip()]
        return value

    def prepare_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        for root in self.allowed_config_roots:
            root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare_directories()
    return settings
