from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore"
    )
    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    supabase_url: str = ""
    supabase_publishable_key: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("")
    database_transport: Literal["supabase", "postgres"] = "supabase"
    supabase_secret_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    model_chat: str = ""
    model_fast: str = ""
    model_research: str = ""
    model_fallback: str = ""
    structured_output_mode: Literal["json_schema", "json_object"] = "json_schema"
    model_reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high"] = ""
    provider_credit_preflight_enabled: bool = True
    chat_history_max_bytes: int = Field(8000, ge=1000, le=32000)
    chat_history_excerpt_bytes: int = Field(2000, ge=500, le=8000)
    model_embedding: str = ""
    embedding_dimensions: int = 1536
    serper_api_key: SecretStr = SecretStr("")
    serper_gl: str = "us"
    serper_hl: str = "en"
    editor_user_ids: list[UUID] = []
    publisher_user_id: UUID | None = None
    publisher_access_token: SecretStr = SecretStr("")
    publisher_refresh_token: SecretStr = SecretStr("")
    publisher_email: str = ""
    publisher_password: SecretStr = SecretStr("")
    autonomous_publishing_enabled: bool = False
    decision_mode: Literal["off", "shadow"] = "off"
    decision_primary_model: str = "perplexity/pplx-decider-v1.1-27b"
    decision_fallback_model: str = "typesafe/jev-1.13"
    decision_escalation_model: str = "openai/gpt-6-luna-decisions"
    decision_min_confidence: float = Field(0.8, ge=0, le=1)
    decision_price_ceiling_per_million: float = Field(1, gt=0)
    cors_origins: list[str] = []
    max_searches: int = Field(4, ge=1, le=20)
    max_pages: int = Field(8, ge=1, le=50)
    deep_max_searches: int = Field(10, ge=1, le=30)
    deep_max_pages: int = Field(24, ge=1, le=80)
    chat_web_success_pages: int = Field(4, ge=1, le=8)
    chat_url_success_pages: int = Field(2, ge=1, le=2)
    chat_deep_success_pages: int = Field(8, ge=1, le=24)
    chat_web_search_iterations: int = Field(2, ge=1, le=4)
    chat_deep_search_iterations: int = Field(2, ge=1, le=4)
    max_model_calls: int = Field(12, ge=1, le=30)
    max_output_tokens: int = Field(2400, ge=100, le=16000)
    chat_router_output_tokens: int = Field(768, ge=100, le=16000)
    chat_simple_output_tokens: int = Field(768, ge=100, le=16000)
    chat_normal_output_tokens: int = Field(1200, ge=100, le=16000)
    chat_coding_output_tokens: int = Field(2000, ge=100, le=16000)
    chat_web_output_tokens: int = Field(1800, ge=100, le=16000)
    chat_deep_output_tokens: int = Field(6000, ge=100, le=16000)
    max_total_tokens: int = Field(32000, ge=1000, le=200000)
    model_price_ceiling_per_million: float = Field(50, gt=0)
    max_run_cost_usd: float = Field(1.5, gt=0)
    run_timeout_seconds: int = Field(240, ge=10, le=1800)
    http_timeout_seconds: int = Field(20, ge=1, le=60)
    fetch_max_bytes: int = Field(2_000_000, ge=10000, le=10_000_000)
    worker_poll_seconds: int = Field(3, ge=1)
    job_lease_seconds: int = Field(360, ge=30)
    api_requests_per_minute: int = Field(30, ge=1)
    discovery_enabled: bool = False
    discovery_auto_research: bool = False
    recheck_enabled: bool = False
    recheck_min_age_hours: int = Field(24, ge=1)
    discovery_interval_seconds: int = Field(1800, ge=60)

    @field_validator("publisher_user_id", mode="before")
    @classmethod
    def empty_publisher_id(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_lease(self):
        if self.job_lease_seconds <= self.run_timeout_seconds + 30:
            raise ValueError("JOB_LEASE_SECONDS must exceed RUN_TIMEOUT_SECONDS by >30")
        if self.autonomous_publishing_enabled and not self.publisher_user_id:
            raise ValueError("Autonomous publishing requires PUBLISHER_USER_ID")
        return self

    def missing(self) -> list[str]:
        required = (
            "supabase_url",
            "supabase_publishable_key",
            "supabase_secret_key" if self.database_transport == "supabase" else "database_url",
            "openrouter_api_key",
            "model_chat",
            "model_fast",
            "model_research",
            "serper_api_key",
        )
        return [
            key.upper()
            for key in required
            if not (
                getattr(self, key).get_secret_value()
                if isinstance(getattr(self, key), SecretStr)
                else getattr(self, key)
            )
        ]
