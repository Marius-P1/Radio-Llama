import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_url: str = os.getenv('DB_URL', None)


settings = Settings()
print(settings.db_url)
