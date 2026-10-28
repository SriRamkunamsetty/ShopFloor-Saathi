"""Runtime configuration, read once from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    auth_mode: str = "dev"  # dev | firebase
    repo_backend: str = "memory"  # memory | json | firestore
    data_dir: Path = Path("./var")

    llm_enabled: bool = False
    fast_model: str = "gemini-2.5-flash"
    pro_model: str = "gemini-2.5-pro"

    # Business assumptions. They are surfaced in every impact estimate.
    downtime_cost_inr_per_hour: float = 75_000.0
    energy_tariff_inr_per_kwh: float = 8.5
    grid_ef_kg_per_kwh: float = 0.71

    rate_limit_per_minute: int = 60
    cors_origins: tuple[str, ...] = ()
    max_image_bytes: int = 5 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @classmethod
    def from_env(cls) -> Settings:
        origins = tuple(
            o.strip() for o in os.getenv("SAATHI_CORS_ORIGINS", "").split(",") if o.strip()
        )
        settings = cls(
            environment=os.getenv("SAATHI_ENV", "development"),
            auth_mode=os.getenv("SAATHI_AUTH_MODE", "dev"),
            repo_backend=os.getenv("SAATHI_REPO_BACKEND", "memory"),
            data_dir=Path(os.getenv("SAATHI_DATA_DIR", "./var")),
            llm_enabled=_env_bool("SAATHI_LLM_ENABLED", False),
            fast_model=os.getenv("SAATHI_FAST_MODEL", "gemini-2.5-flash"),
            pro_model=os.getenv("SAATHI_PRO_MODEL", "gemini-2.5-pro"),
            downtime_cost_inr_per_hour=_env_float("SAATHI_DOWNTIME_COST_INR_PER_HOUR", 75_000.0),
            energy_tariff_inr_per_kwh=_env_float("SAATHI_ENERGY_TARIFF_INR_PER_KWH", 8.5),
            grid_ef_kg_per_kwh=_env_float("SAATHI_GRID_EF_KG_PER_KWH", 0.71),
            rate_limit_per_minute=int(_env_float("SAATHI_RATE_LIMIT_PER_MINUTE", 60)),
            cors_origins=origins,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.auth_mode not in {"dev", "firebase"}:
            raise ValueError(f"SAATHI_AUTH_MODE must be dev or firebase, got {self.auth_mode!r}")
        if self.repo_backend not in {"memory", "json", "firestore"}:
            raise ValueError(f"Unknown SAATHI_REPO_BACKEND {self.repo_backend!r}")
        if self.is_production and self.auth_mode == "dev":
            # Header-based roles are trivially spoofable; never allow them in production.
            raise ValueError("SAATHI_AUTH_MODE=dev is not allowed when SAATHI_ENV=production")
