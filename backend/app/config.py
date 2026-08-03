"""
app/config.py
─────────────
Centralised configuration for the Risk Gatekeeper backend.
All values are read from environment variables (or a .env file) with
sensible defaults for local development.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./data/risk_gatekeeper.db"

    # ── CORS ───────────────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    # ── Risk Thresholds — Module 2 (ATTVE) ────────────────────────────────────
    transaction_limit_usd: float = 50.0

    # ── Risk Thresholds — Module 6 (Intent Verification) ──────────────────────
    # Jaccard similarity below this value triggers a WARN for intent drift.
    intent_drift_threshold: float = 0.15

    # ── Risk Thresholds — Module 7 (Planning Verification) ────────────────────
    # A plan with more steps than this triggers a WARN for broad scope.
    plan_scope_threshold: int = 10
    # A plan touching more distinct files than this triggers a WARN.
    plan_file_scope_threshold: int = 20


settings = Settings()
