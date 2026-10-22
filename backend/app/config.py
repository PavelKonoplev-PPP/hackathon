from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "hackathon"
    postgres_user: str = "bi_user"
    postgres_password: str = ""
    postgres_schema: str = "raw"

    postgres_reader_user: str = "bi_reader"
    postgres_reader_password: str = ""

    schema_catalog_path: str = "data/schema-catalog.json"
    relationship_catalog_path: str = "data/relationship-catalog.json"

    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def reader_password(self) -> str:
        return self.postgres_reader_password or self.postgres_password


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
