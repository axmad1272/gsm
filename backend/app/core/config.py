from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GSM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://gsm:gsm@localhost:5432/gsm"
    # dev: пользователь передаётся заголовком X-User-Id. Для продакшена нужен SSO/JWT.
    auth_mode: str = "dev"
    storage_dir: str = "var/storage"
    public_base_url: str = "http://localhost:8000"
    # Успешные исправления оператора после первой печати.
    operator_correction_limit: int = 3
    didox_mode: str = "fake"  # fake | http


@lru_cache
def get_settings() -> Settings:
    return Settings()
