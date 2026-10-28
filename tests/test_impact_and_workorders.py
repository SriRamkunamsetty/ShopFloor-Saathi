from __future__ import annotations

import pytest

from saathi.config import Settings
from saathi.domain import FAILURE_MODES, get_machine
from saathi.models import WorkOrderStatus
from saathi.tools.impact import estimate_impact
from saathi.tools.workorders import InMemoryRepository as Repo
from saathi.tools.workorders import (
    JsonFileRepository,
    WorkOrderError,
    WorkOrderService,
)


def test_impact_matches_hand_calculation():
    s = Settings(
        downtime_cost_inr_per_hour=60_000, energy_tariff_inr_per_kwh=10, grid_ef_kg_per_kwh=0.5
    )
    m = get_machine("M-07")  # 11 kW
    e = estimate_impact(s, m, "HDF")
    mode = FAILURE_MODES["HDF"]
    avoided = mode.unplanned_downtime_min - mode.planned_repair_min
    assert e.downtime_minutes_avoided == avoided
    assert e.downtime_cost_avoided_inr == pytest.approx(avoided / 60 * 60_000)
    kwh = 11 * mode.degraded_hours_if_ignored * mode.degraded_power_penalty
    assert e.energy_kwh_saved == pytest.approx(kwh, abs=0.01)
    assert e.co2_kg_avoided == pytest.approx(kwh * 0.5, abs=0.01)
    assert e.total_inr == pytest.approx(
        e.downtime_cost_avoided_inr + e.energy_cost_saved_inr, abs=0.01
    )
    assert e.assumptions["downtime_cost_inr_per_hour"] == 60_000


def test_impact_never_negative_and_validates_inputs():
    s = Settings()
    m = get_machine("M-01")
    assert estimate_impact(s, m, "TWF", planned_repair_min=10_000).downtime_minutes_avoided == 0
    with pytest.raises(ValueError):
        estimate_impact(s, m, "TWF", planned_repair_min=-1)
    with pytest.raises(KeyError):
        estimate_impact(s, m, "NOPE")


def _draft(services, machine="M-07", symptom="garam"):
    return services.engine.diagnose(machine, symptom).recommended


def test_create_draft_is_pending_and_idempotent(services):
    wo = services.work_orders
    order, created = wo.create_draft(_draft(services), "op-1")
    again, created_again = wo.create_draft(_draft(services), "op-2")
    assert created and not created_again
    assert order.status.value == "pending_approval" and again.id == order.id
    assert len(wo.list()) == 1


def test_decision_lifecycle_and_state_guards(services):
    wo = services.work_orders
    order, _ = wo.create_draft(_draft(services), "op-1")
    with pytest.raises(WorkOrderError):
        wo.complete(order.id, 20, "sup")  # cannot complete before approval
    approved = wo.decide(order.id, True, "sup-1", "ok")
    assert approved.status.value == "approved" and approved.decided_by == "sup-1"
    with pytest.raises(WorkOrderError):
        wo.decide(order.id, False, "sup-1", "")  # cannot decide twice
    done = wo.complete(order.id, 22.5, "sup-1")
    assert done.status.value == "completed" and done.actual_downtime_min == 22.5
    with pytest.raises(WorkOrderError):
        wo.get("WO-00000000-AAAAAA")


def test_rejected_order_allows_a_new_draft(services):
    wo = services.work_orders
    first, _ = wo.create_draft(_draft(services), "op-1")
    wo.decide(first.id, False, "sup-1", "not now")
    second, created = wo.create_draft(_draft(services), "op-1")
    assert created and second.id != first.id


def test_impact_summary_counts_only_approved_or_completed(services):
    wo = services.work_orders
    a, _ = wo.create_draft(_draft(services, "M-07"), "op")
    b, _ = wo.create_draft(_draft(services, "M-03", ""), "op")
    assert wo.impact_summary()["work_orders_counted"] == 0
    wo.decide(a.id, True, "sup", "")
    wo.decide(b.id, False, "sup", "")
    summary = wo.impact_summary()
    assert summary["work_orders_counted"] == 1 and summary["pending_approval"] == 0
    assert summary["total_inr"] == a.draft.impact.total_inr


def test_json_repository_persists_across_restarts(services, tmp_path):
    path = tmp_path / "wo.json"
    svc1 = WorkOrderService(JsonFileRepository(path))
    order, _ = svc1.create_draft(_draft(services), "op-1")
    svc1.decide(order.id, True, "sup-1", "go")
    svc2 = WorkOrderService(JsonFileRepository(path))
    reloaded = svc2.get(order.id)
    assert reloaded.status.value == "approved" and reloaded.draft.machine_id == "M-07"
    assert not list(tmp_path.glob("*.tmp"))


def test_repository_returns_copies(services):
    repo = Repo()
    svc = WorkOrderService(repo)
    order, _ = svc.create_draft(_draft(services), "op")
    order.status = order.status.__class__.COMPLETED  # mutate the returned copy
    assert svc.get(order.id).status.value == "pending_approval"


# ---- Firestore adapter, exercised against a tiny in-memory fake of the client API ----


class _Snap:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _Doc:
    def __init__(self, store, doc_id):
        self._store, self._id = store, doc_id

    def get(self):
        return _Snap(self._store.get(self._id))

    def set(self, data):
        self._store[self._id] = data


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def stream(self):
        return (_Snap(r) for r in self._rows)


class _Collection(_Query):
    def __init__(self):
        self.store: dict[str, dict] = {}

    def document(self, doc_id):
        return _Doc(self.store, doc_id)

    def where(self, field, op, value):
        assert op == "=="
        return _Query([r for r in self.store.values() if r[field] == value])

    def stream(self):
        return (_Snap(r) for r in self.store.values())


class _Client:
    def __init__(self):
        self.col = _Collection()

    def collection(self, _name):
        return self.col


def test_firestore_repository_full_lifecycle(services):
    from saathi.tools.workorders import FirestoreRepository

    svc = WorkOrderService(FirestoreRepository(client=_Client()))
    order, created = svc.create_draft(_draft(services), "op-1")
    again, created_again = svc.create_draft(_draft(services), "op-2")
    assert created and not created_again and again.id == order.id
    svc.decide(order.id, True, "sup-1", "go")
    assert [o.id for o in svc.list(None)] == [order.id]
    assert svc.list(WorkOrderStatus.PENDING_APPROVAL) == []
    assert svc.get(order.id).status == WorkOrderStatus.APPROVED
    assert svc.impact_summary()["work_orders_counted"] == 1
