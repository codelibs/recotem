"""Inference service configuration from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database (read-only access to Django's PostgreSQL)
    database_url: str = "postgresql://recotem_user:recotem_pass@localhost:5432/recotem"

    # Redis for model events (Pub/Sub on db 3)
    model_events_redis_url: str = "redis://localhost:6379/3"

    # Django SECRET_KEY for HMAC verification
    secret_key: str = "VeryBadSecret@ChangeThis"

    # Service settings
    inference_port: int = 8081
    inference_max_loaded_models: int = 10
    inference_rate_limit: str = "100/minute"
    inference_preload_model_ids: str = (
        ""  # Comma-separated model IDs to pre-load on startup
    )

    # Model storage
    media_root: str = "/data"
    recotem_storage_type: str = ""

    # Allow unsigned legacy pickle files
    pickle_allow_legacy_unsigned: bool = False

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
