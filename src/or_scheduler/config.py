from pathlib import Path

from dotenv import find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Search parent directories for .env — works whether called from project root or Assignment/
_env_file = find_dotenv(usecwd=False) or ".env"


class Settings(BaseSettings):
    database_url: str
    pool_size: int = 20
    max_overflow: int = 10
    echo_sql: bool = False

    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
