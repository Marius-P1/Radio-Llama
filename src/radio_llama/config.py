"""Settings read from the environment and from a `.env` file."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """A setting is missing or invalid. The message names the variable."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # API keys
    mistral_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    elevenlabs_api_key: SecretStr | None = None

    # Radio settings
    radio_language: Literal["en", "fr"] = "en"
    llm_provider: Literal["mistral", "anthropic", "fake"] = "mistral"
    llm_model: str = "mistral-small-latest"
    tts_backend: Literal["elevenlabs", "fake"] = "elevenlabs"
    elevenlabs_model_id: str = "eleven_multilingual_v2"

    # Paths
    cache_dir: Path = Path("cache")
    output_dir: Path = Path("output")
    catalog_path: Path = Path("catalog/catalog.json")
    prompts_dir: Path = Path("prompts")
    # Built frontend served at `/` when the folder exists.
    frontend_dist: Path = Path("frontend/dist")

    def require_api_key(self, provider: str) -> str:
        """Return the API key for `provider` as a plain string, or raise ConfigError."""
        if provider not in ("mistral", "anthropic", "elevenlabs"):
            raise ValueError(f"Unknown provider: {provider!r}")
        field = f"{provider}_api_key"
        secret: SecretStr | None = getattr(self, field)
        if secret is None:
            variable = field.upper()
            raise ConfigError(
                f"{variable} is not set. Add it to .env or export it. See .env.example."
            )
        return secret.get_secret_value()


def load_settings() -> Settings:
    """Build Settings, turning validation errors into one readable ConfigError."""
    try:
        return Settings()
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            variable = "_".join(str(part) for part in error["loc"]).upper()
            message = error["msg"]
            value = error.get("input")
            if value is not None:
                message = f"{message} (got {value!r})"
            lines.append(f"{variable}: {message}")
        raise ConfigError("\n".join(lines)) from None
