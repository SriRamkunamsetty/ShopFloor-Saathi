from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from saathi.api import create_app
from saathi.config import Settings
from saathi.data.synthetic import generate_telemetry
from saathi.services import build_services
from saathi.tools.telemetry import TelemetryStore
from saathi.tools.workorders import InMemoryRepository

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
SUPERVISOR = {"X-Saathi-Role": "supervisor", "X-Saathi-User": "sup-1"}
OPERATOR = {"X-Saathi-Role": "operator", "X-Saathi-User": "op-1"}


@pytest.fixture(scope="session")
def telemetry() -> TelemetryStore:
    return TelemetryStore(generate_telemetry(seed=7, end=NOW))


@pytest.fixture
def settings() -> Settings:
    return Settings(rate_limit_per_minute=0)  # unlimited unless a test sets it


@pytest.fixture
def services(settings, telemetry):
    return build_services(settings, telemetry=telemetry, repository=InMemoryRepository())


@pytest.fixture
def client(services):
    with TestClient(create_app(services)) as c:
        yield c
