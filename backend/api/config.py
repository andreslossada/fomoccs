import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    environment: str = "local"

    db_host: str = "aws-1-sa-east-1.pooler.supabase.com"
    db_port: int = 5432
    db_name: str = "postgres"
    db_user: str = ""
    db_pass: str = ""

    sync_api_key: str = "changeme"
    secret_key: str = "changeme-secret"
    geoapify_api_key: str = ""
    redis_url: str = "redis://localhost:6379/0"

    def model_post_init(self, __context: object) -> None:
        if not self.db_user:
            self.db_user = os.environ.get("USER", "")

    @property
    def database_url(self) -> str:
        userinfo = self.db_user
        if self.db_pass:
            userinfo = f"{self.db_user}:{self.db_pass}"
        return f"postgresql+asyncpg://{userinfo}@{self.db_host}:{self.db_port}/{self.db_name}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
