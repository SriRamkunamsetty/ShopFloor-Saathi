"""Dependency container: builds all long-lived services once."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from saathi.config import Settings
from saathi.data.synthetic import generate_telemetry
from saathi.engine import DiagnosisEngine
from saathi.tools.knowledge import KnowledgeBase
from saathi.tools.spares import SpareParts
from saathi.tools.telemetry import TelemetryStore
from saathi.tools.workorders import (
    FirestoreRepository,
    InMemoryRepository,
    JsonFileRepository,
    WorkOrderRepository,
    WorkOrderService,
)


@dataclass
class Services:
    settings: Settings
    telemetry: TelemetryStore
    knowledge: KnowledgeBase
    spares: SpareParts
    engine: DiagnosisEngine
    work_orders: WorkOrderService


def build_repository(settings: Settings) -> WorkOrderRepository:
    if settings.repo_backend == "firestore":
        return FirestoreRepository()
    if settings.repo_backend == "json":
        return JsonFileRepository(settings.data_dir / "work_orders.json")
    return InMemoryRepository()


def build_services(
    settings: Settings | None = None,
    *,
    telemetry: TelemetryStore | None = None,
    now: datetime | None = None,
    repository: WorkOrderRepository | None = None,
) -> Services:
    settings = settings or Settings.from_env()
    telemetry = telemetry or TelemetryStore(generate_telemetry(end=now))
    knowledge = KnowledgeBase.from_default_data()
    spares = SpareParts.from_default_data()
    return Services(
        settings=settings,
        telemetry=telemetry,
        knowledge=knowledge,
        spares=spares,
        engine=DiagnosisEngine(settings, telemetry, knowledge, spares),
        work_orders=WorkOrderService(repository or build_repository(settings)),
    )


@lru_cache(maxsize=1)
def get_services() -> Services:
    """Process-wide singleton used by the ADK tools and the API."""
    return build_services()
