from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Mutable license data never belongs here."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="FPOS_LICENSE_", extra="ignore"
    )

    app_name: str = "FPOS License"
    environment: str = "development"
    data_dir: Path = Path("/data")
    database_url: str | None = None
    admin_username: str = "fieter"
    admin_password_hash: str = ""
    signing_private_key_b64: str = ""
    signing_key_seed: str = Field(default="development-only-change-me", min_length=16)
    allowed_origins: str = ""

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'fpos-license.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
