"""Static domain knowledge: machines and failure-mode metadata.

Failure-mode detection thresholds follow the documented failure rules of the public AI4I 2020
Predictive Maintenance dataset (UCI). Repair times, unplanned-downtime durations and degraded-power
penalties are ASSUMPTIONS for a small CNC shop; they are shown to the user with every estimate and
must be replaced with the plant's own numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MACHINE_ID_RE = re.compile(r"^M-\d{2}$")


@dataclass(frozen=True)
class Machine:
    machine_id: str
    name: str
    product_type: str  # L / M / H quality variant, used by the overstrain rule
    rated_kw: float


@dataclass(frozen=True)
class FailureMode:
    code: str
    label: str
    parts: tuple[str, ...]
    planned_repair_min: int  # fix now, during a planned stop
    unplanned_downtime_min: int  # if the fault is ignored until the machine fails
    degraded_power_penalty: float  # fraction of rated power wasted while running degraded
    degraded_hours_if_ignored: float  # how long it would keep running degraded before failing


MACHINES: tuple[Machine, ...] = (
    Machine("M-01", "CNC Mill 1 (Bracket line)", "M", 7.5),
    Machine("M-02", "CNC Mill 2 (Housing line)", "L", 7.5),
    Machine("M-03", "CNC Lathe 3 (Shaft line)", "M", 9.0),
    Machine("M-04", "VMC 4 (Flange line)", "H", 11.0),
    Machine("M-05", "CNC Mill 5 (Gear blanks)", "M", 7.5),
    Machine("M-06", "CNC Lathe 6 (Bush line)", "L", 9.0),
    Machine("M-07", "VMC 7 (Pump casing line)", "M", 11.0),
    Machine("M-08", "CNC Mill 8 (Fixtures)", "H", 7.5),
)

FAILURE_MODES: dict[str, FailureMode] = {
    "HDF": FailureMode(
        "HDF",
        "Heat dissipation failure",
        ("cooling-fan-assy", "coolant-pump-seal"),
        planned_repair_min=25,
        unplanned_downtime_min=240,
        degraded_power_penalty=0.08,
        degraded_hours_if_ignored=6.0,
    ),
    "TWF": FailureMode(
        "TWF",
        "Tool wear failure",
        ("carbide-insert-set",),
        planned_repair_min=15,
        unplanned_downtime_min=120,
        degraded_power_penalty=0.05,
        degraded_hours_if_ignored=2.0,
    ),
    "PWF": FailureMode(
        "PWF",
        "Power / drive failure",
        ("spindle-drive-fuse", "vfd-power-module"),
        planned_repair_min=45,
        unplanned_downtime_min=360,
        degraded_power_penalty=0.0,
        degraded_hours_if_ignored=1.0,
    ),
    "OSF": FailureMode(
        "OSF",
        "Overstrain failure",
        ("tool-holder", "clamp-set"),
        planned_repair_min=30,
        unplanned_downtime_min=300,
        degraded_power_penalty=0.03,
        degraded_hours_if_ignored=2.0,
    ),
}

_BY_ID = {m.machine_id: m for m in MACHINES}


def get_machine(machine_id: str) -> Machine:
    if not MACHINE_ID_RE.match(machine_id or ""):
        raise ValueError(f"Invalid machine id {machine_id!r}; expected format M-01")
    try:
        return _BY_ID[machine_id]
    except KeyError as exc:
        raise KeyError(f"Unknown machine {machine_id}") from exc
