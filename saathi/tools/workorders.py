"""Work-order lifecycle and storage.

The agent can only DRAFT work orders (status pending_approval). Approval, rejection and
completion are separate, role-checked API operations performed by a human supervisor.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from saathi.models import (
    ImpactEstimate,
    WorkOrder,
    WorkOrderDraft,
    WorkOrderStatus,
    utcnow,
)


class WorkOrderError(Exception):
    """Invalid state transition or unknown work order."""


class WorkOrderRepository(ABC):
    @abstractmethod
    def get(self, work_order_id: str) -> WorkOrder | None: ...

    @abstractmethod
    def save(self, work_order: WorkOrder) -> None: ...

    @abstractmethod
    def list(self, status: WorkOrderStatus | None = None) -> list[WorkOrder]: ...

    @abstractmethod
    def find_open_by_key(self, idempotency_key: str) -> WorkOrder | None: ...


class InMemoryRepository(WorkOrderRepository):
    def __init__(self) -> None:
        self._items: dict[str, WorkOrder] = {}
        self._lock = threading.RLock()

    def get(self, work_order_id: str) -> WorkOrder | None:
        with self._lock:
            item = self._items.get(work_order_id)
            return item.model_copy(deep=True) if item else None

    def save(self, work_order: WorkOrder) -> None:
        with self._lock:
            self._items[work_order.id] = work_order.model_copy(deep=True)

    def list(self, status: WorkOrderStatus | None = None) -> list[WorkOrder]:
        with self._lock:
            items = [i.model_copy(deep=True) for i in self._items.values()]
        items = [i for i in items if status is None or i.status == status]
        return sorted(items, key=lambda i: i.created_at, reverse=True)

    def find_open_by_key(self, idempotency_key: str) -> WorkOrder | None:
        with self._lock:
            for item in self._items.values():
                if item.idempotency_key == idempotency_key and item.status in (
                    WorkOrderStatus.PENDING_APPROVAL,
                    WorkOrderStatus.APPROVED,
                ):
                    return item.model_copy(deep=True)
        return None


class JsonFileRepository(InMemoryRepository):
    """Single-instance persistence for local runs. Use Firestore on Cloud Run."""

    def __init__(self, path: Path):
        super().__init__()
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            for raw in json.loads(path.read_text(encoding="utf-8")):
                item = WorkOrder.model_validate(raw)
                self._items[item.id] = item

    def save(self, work_order: WorkOrder) -> None:
        with self._lock:
            super().save(work_order)
            payload = [i.model_dump(mode="json") for i in self._items.values()]
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                os.replace(tmp, self._path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise


class FirestoreRepository(WorkOrderRepository):
    """Firestore-backed repository for Cloud Run (needs the `gcp` extra and ADC credentials).

    NOTE: not exercised by the unit tests (no emulator in CI); verify against the Firestore
    emulator or a dev project before relying on it.
    """

    def __init__(self, collection: str = "work_orders", client=None):
        if client is None:
            from google.cloud import firestore  # lazy import, optional dependency

            client = firestore.Client()
        self._col = client.collection(collection)

    def get(self, work_order_id: str) -> WorkOrder | None:
        snap = self._col.document(work_order_id).get()
        return WorkOrder.model_validate(snap.to_dict()) if snap.exists else None

    def save(self, work_order: WorkOrder) -> None:
        self._col.document(work_order.id).set(work_order.model_dump(mode="json"))

    def list(self, status: WorkOrderStatus | None = None) -> list[WorkOrder]:
        query = self._col if status is None else self._col.where("status", "==", status.value)
        items = [WorkOrder.model_validate(d.to_dict()) for d in query.stream()]
        return sorted(items, key=lambda i: i.created_at, reverse=True)

    def find_open_by_key(self, idempotency_key: str) -> WorkOrder | None:
        query = self._col.where("idempotency_key", "==", idempotency_key)
        for doc in query.stream():
            item = WorkOrder.model_validate(doc.to_dict())
            if item.status in (WorkOrderStatus.PENDING_APPROVAL, WorkOrderStatus.APPROVED):
                return item
        return None


class WorkOrderService:
    def __init__(self, repository: WorkOrderRepository):
        self._repo = repository
        self._lock = threading.Lock()

    def create_draft(self, draft: WorkOrderDraft, created_by: str) -> tuple[WorkOrder, bool]:
        """Create a pending work order. Returns (work_order, created_now).

        Idempotent per machine + failure mode while an order is open, so repeated diagnoses or
        retried requests never produce duplicate orders.
        """
        key = f"{draft.machine_id}:{draft.failure_mode or 'general'}"
        with self._lock:
            existing = self._repo.find_open_by_key(key)
            if existing:
                return existing, False
            order = WorkOrder(
                id=f"WO-{utcnow():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
                idempotency_key=key,
                created_by=created_by,
                draft=draft,
            )
            self._repo.save(order)
            return order, True

    def decide(self, work_order_id: str, approve: bool, decided_by: str, note: str) -> WorkOrder:
        with self._lock:
            order = self._require(work_order_id)
            if order.status != WorkOrderStatus.PENDING_APPROVAL:
                raise WorkOrderError(f"Work order is {order.status.value}, cannot decide again")
            order.status = WorkOrderStatus.APPROVED if approve else WorkOrderStatus.REJECTED
            order.decided_by = decided_by
            order.decided_at = utcnow()
            order.decision_note = note or None
            self._repo.save(order)
            return order

    def complete(
        self, work_order_id: str, actual_downtime_min: float, completed_by: str
    ) -> WorkOrder:
        with self._lock:
            order = self._require(work_order_id)
            if order.status != WorkOrderStatus.APPROVED:
                raise WorkOrderError("Only approved work orders can be completed")
            order.status = WorkOrderStatus.COMPLETED
            order.actual_downtime_min = actual_downtime_min
            order.completed_at = utcnow()
            self._repo.save(order)
            return order

    def get(self, work_order_id: str) -> WorkOrder:
        return self._require(work_order_id)

    def list(self, status: WorkOrderStatus | None = None) -> list[WorkOrder]:
        return self._repo.list(status)

    def impact_summary(self) -> dict[str, float | int]:
        """Totals over approved and completed orders (projected, not audited savings)."""
        counted = [
            o
            for o in self._repo.list()
            if o.status in (WorkOrderStatus.APPROVED, WorkOrderStatus.COMPLETED)
        ]
        total = ImpactEstimate(
            downtime_minutes_avoided=sum(o.draft.impact.downtime_minutes_avoided for o in counted),
            downtime_cost_avoided_inr=sum(
                o.draft.impact.downtime_cost_avoided_inr for o in counted
            ),
            energy_kwh_saved=sum(o.draft.impact.energy_kwh_saved for o in counted),
            energy_cost_saved_inr=sum(o.draft.impact.energy_cost_saved_inr for o in counted),
            co2_kg_avoided=sum(o.draft.impact.co2_kg_avoided for o in counted),
            total_inr=sum(o.draft.impact.total_inr for o in counted),
            assumptions={},
        )
        return {
            "work_orders_counted": len(counted),
            "pending_approval": len(self._repo.list(WorkOrderStatus.PENDING_APPROVAL)),
            "downtime_minutes_avoided": round(total.downtime_minutes_avoided, 1),
            "downtime_cost_avoided_inr": round(total.downtime_cost_avoided_inr, 2),
            "energy_kwh_saved": round(total.energy_kwh_saved, 2),
            "energy_cost_saved_inr": round(total.energy_cost_saved_inr, 2),
            "co2_kg_avoided": round(total.co2_kg_avoided, 2),
            "total_inr": round(total.total_inr, 2),
        }

    def _require(self, work_order_id: str) -> WorkOrder:
        order = self._repo.get(work_order_id)
        if order is None:
            raise WorkOrderError(f"Unknown work order {work_order_id}")
        return order
