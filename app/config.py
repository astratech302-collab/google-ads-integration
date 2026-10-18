from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ADS_", extra="ignore")
    mode: Literal["mock", "live"] = "mock"
    database: Path = Path("var/ads.sqlite3")
    encryption_key: SecretStr | None = None
    api_keys: dict[str, SecretStr] = Field(default_factory=lambda: {"local": SecretStr("local-dev-key")})
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    developer_token: SecretStr = SecretStr("")
    redirect_uri: str = "http://localhost:8000/oauth/callback"
    api_version: str = "v23"
    allow_test_status: bool = False  # Google test accounts report CANCELED/CLOSED; local testing only
    normalization_file: Path = Path("config/url-normalization.json")
    fixture_file: Path = Path("fixtures/google_ads.json")
    min_request_interval: float = Field(default=0.25, ge=0)
    max_retries: int = Field(default=4, ge=0, le=8)
    request_timeout: float = Field(default=30, gt=0, le=120)

    @model_validator(mode="after")
    def live_requirements(self):
        if not self.api_keys or any(not v.get_secret_value() for v in self.api_keys.values()):
            raise ValueError("Configure nonempty integration API keys")
        values = [v.get_secret_value() for v in self.api_keys.values()]
        if len(values) != len(set(values)):
            raise ValueError("Integration principals must have distinct API keys")
        if self.mode == "live":
            if not all([self.encryption_key, self.client_id, self.client_secret.get_secret_value()]):
                raise ValueError("Live mode requires encryption key and OAuth credentials")
            if any(len(v.get_secret_value()) < 32 or v.get_secret_value() == "local-dev-key" for v in self.api_keys.values()):
                raise ValueError("Live integration API keys must have at least 32 characters")
            if not self.redirect_uri.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
                raise ValueError("OAuth redirect URI must use HTTPS or loopback HTTP")
        return self
