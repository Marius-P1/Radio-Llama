"""Settings read from the environment and from a `.env` file."""

from datetime import datetime, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """A setting is missing or invalid. The message names the variable."""


class Settings(BaseSettings):
    # `.env.example` ships every variable blank. An empty variable means "use the default",
    # not an empty string (which would make every Path point at the current folder).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    # API keys
    mistral_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    elevenlabs_api_key: SecretStr | None = None
    # Jamendo client id from devportal.jamendo.com. Not a secret, but unset means no Jamendo.
    jamendo_client_id: str | None = None

    # Radio settings
    radio_language: Literal["en", "fr"] = "en"
    # IANA name such as Europe/Paris. The host greets by this clock (D031). Unset: the
    # machine's local time, which is UTC inside a Docker container.
    radio_timezone: str | None = None
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

    @field_validator("radio_timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(
                    "unknown timezone, use an IANA name such as Europe/Paris"
                ) from None
        return value

    @property
    def tzinfo(self) -> tzinfo:
        """The timezone the host lives in."""
        if self.radio_timezone is not None:
            return ZoneInfo(self.radio_timezone)
        local = datetime.now().astimezone().tzinfo
        assert local is not None  # astimezone() always fills it in
        return local

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
