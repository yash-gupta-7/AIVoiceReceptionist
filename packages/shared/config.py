"""Application settings, loaded from environment / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://clinic:clinic@localhost:5432/clinic"
    jwt_secret: str = "change-me"
    jwt_ttl_minutes: int = 480

    llm_provider: str = "grok"
    grok_api_key: str = ""
    grok_model: str = "grok-3-mini"
    grok_base_url: str = "https://api.x.ai/v1"
    llm_timeout_seconds: float = 10.0
    llm_retries: int = 2

    twilio_auth_token: str = ""
    vapi_secret: str = ""  # shared secret Vapi sends in x-vapi-secret
    vapi_api_key: str = ""
    public_url: str = "http://localhost:8000"  # where Vapi reaches the tool webhook
    cliniko_api_key: str = ""  # if set, PMS write-back hits real Cliniko
    cliniko_base_url: str = "https://api.au4.cliniko.com/v1"
    prompt_version: str = "v1"
    clinic_name: str = "City Dental Care"
    clinic_tz: str = "Europe/London"
    transfer_number: str = "+15550100"

    admin_email: str = "admin@clinic.local"
    admin_password: str = "admin123"

    slot_retry_limit: int = 3
    rate_limit_per_minute: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
