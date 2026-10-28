"""Telemetry store and explainable rule-based anomaly detection.

Rules follow the documented failure conditions of the AI4I 2020 dataset:
  HDF: (process_temp - air_temp) < 8.6 K and rpm < 1380
  TWF: tool wear >= 200 min (early warning from 180 min)
  PWF: power = torque * rpm * 2*pi/60 below 3500 W or above 9000 W
  OSF: tool_wear * torque above 11000 (L), 12000 (M), 13000 (H) min*Nm
A statistical drift check (info only) compares the window with the earlier baseline.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from saathi.domain import MACHINES, Machine, get_machine
from saathi.models import Finding, MachineStatus, Severity

HDF_MAX_DIFF_K = 8.6
HDF_MAX_RPM = 1380
TWF_LIMIT_MIN = 200
TWF_EARLY_MIN = 180
PWF_LOW_W = 3500
PWF_HIGH_W = 9000
OSF_LIMITS = {"L": 11000, "M": 12000, "H": 13000}
MIN_RUN = 3  # consecutive 5-minute samples before a rule fires (filters single-sample noise)
REQUIRED_COLUMNS = {
    "timestamp",
    "machine_id",
    "air_temp_k",
    "process_temp_k",
    "rpm",
    "torque_nm",
    "tool_wear_min",
}


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    failure_mode: str | None
    title: str
    threshold: str
    mask: Callable[[pd.DataFrame, Machine], pd.Series]
    evidence_cols: tuple[str, ...]
    describe: Callable[[pd.DataFrame], str]
    severity_if_ongoing: Severity = Severity.CRITICAL
    severity_if_resolved: Severity = Severity.WARNING


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["temp_gap_k"] = out["process_temp_k"] - out["air_temp_k"]
    out["power_w"] = out["torque_nm"] * out["rpm"] * 2 * math.pi / 60.0
    return out


RULES: tuple[_Rule, ...] = (
    _Rule(
        "HDF",
        "HDF",
        "Insufficient heat dissipation",
        f"temp gap < {HDF_MAX_DIFF_K} K and rpm < {HDF_MAX_RPM}",
        lambda d, m: (d["temp_gap_k"] < HDF_MAX_DIFF_K) & (d["rpm"] < HDF_MAX_RPM),
        ("temp_gap_k", "rpm", "air_temp_k", "process_temp_k"),
        lambda d: (
            f"Process-air temperature gap fell to {d['temp_gap_k'].min():.1f} K while spindle "
            f"speed dropped to {d['rpm'].min():.0f} rpm."
        ),
    ),
    _Rule(
        "TWF",
        "TWF",
        "Tool wear beyond limit",
        f"tool wear >= {TWF_LIMIT_MIN} min",
        lambda d, m: d["tool_wear_min"] >= TWF_LIMIT_MIN,
        ("tool_wear_min",),
        lambda d: f"Tool wear reached {d['tool_wear_min'].max():.0f} min (limit {TWF_LIMIT_MIN}).",
    ),
    _Rule(
        "TWF_EARLY",
        "TWF",
        "Tool approaching wear limit",
        f"tool wear >= {TWF_EARLY_MIN} min",
        lambda d, m: (d["tool_wear_min"] >= TWF_EARLY_MIN) & (d["tool_wear_min"] < TWF_LIMIT_MIN),
        ("tool_wear_min",),
        lambda d: (
            f"Tool wear is {d['tool_wear_min'].max():.0f} min, approaching the {TWF_LIMIT_MIN} limit."
        ),
        severity_if_ongoing=Severity.WARNING,
        severity_if_resolved=Severity.INFO,
    ),
    _Rule(
        "PWF",
        "PWF",
        "Spindle power outside safe range",
        f"power < {PWF_LOW_W} W or > {PWF_HIGH_W} W",
        lambda d, m: (d["power_w"] < PWF_LOW_W) | (d["power_w"] > PWF_HIGH_W),
        ("power_w", "torque_nm", "rpm"),
        lambda d: (
            f"Spindle power peaked at {d['power_w'].max():.0f} W "
            f"(torque up to {d['torque_nm'].max():.0f} Nm)."
        ),
    ),
    _Rule(
        "OSF",
        "OSF",
        "Overstrain on worn tool",
        "tool wear x torque above the limit for the product type",
        lambda d, m: (d["tool_wear_min"] * d["torque_nm"]) > OSF_LIMITS[m.product_type],
        ("tool_wear_min", "torque_nm"),
        lambda d: (
            f"Wear x torque reached {(d['tool_wear_min'] * d['torque_nm']).max():.0f} "
            "min*Nm, above the allowed limit."
        ),
    ),
)

_DRIFT_METRICS = ("temp_gap_k", "rpm", "torque_nm", "power_w")


def _runs(mask: np.ndarray) -> tuple[int, int]:
    """Return (longest run of True, trailing run of True)."""
    longest = current = 0
    for value in mask:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest, current


class TelemetryStore:
    def __init__(self, df: pd.DataFrame):
        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"Telemetry is missing columns: {sorted(missing)}")
        if df.empty:
            raise ValueError("Telemetry is empty")
        data = df.copy()
        data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
        self._data = _prep(data).sort_values(["machine_id", "timestamp"]).reset_index(drop=True)

    def machine_ids(self) -> list[str]:
        return sorted(self._data["machine_id"].unique())

    def machine_frame(self, machine_id: str) -> pd.DataFrame:
        get_machine(machine_id)  # validates the id format and existence
        frame = self._data[self._data["machine_id"] == machine_id]
        if frame.empty:
            raise KeyError(f"No telemetry for {machine_id}")
        return frame

    def detect(self, machine_id: str, window_minutes: int = 60) -> list[Finding]:
        if not 10 <= window_minutes <= 24 * 60:
            raise ValueError("window_minutes must be between 10 and 1440")
        machine = get_machine(machine_id)
        frame = self.machine_frame(machine_id)
        end = frame["timestamp"].iloc[-1]
        window = frame[frame["timestamp"] > end - pd.Timedelta(minutes=window_minutes)]
        baseline = frame[frame["timestamp"] <= end - pd.Timedelta(minutes=window_minutes)]
        findings: list[Finding] = []

        for rule in RULES:
            mask = rule.mask(window, machine).to_numpy()
            longest, trailing = _runs(mask)
            if longest < MIN_RUN:
                continue
            violating = window[mask]
            ongoing = trailing > 0
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    failure_mode=rule.failure_mode,
                    severity=rule.severity_if_ongoing if ongoing else rule.severity_if_resolved,
                    title=rule.title,
                    description=rule.describe(violating),
                    first_seen=violating["timestamp"].iloc[0].to_pydatetime(),
                    last_seen=violating["timestamp"].iloc[-1].to_pydatetime(),
                    samples_in_violation=int(mask.sum()),
                    window_samples=len(window),
                    longest_run=longest,
                    ongoing=ongoing,
                    evidence={
                        col: round(float(violating[col].iloc[-1]), 2) for col in rule.evidence_cols
                    },
                    threshold=rule.threshold,
                )
            )

        findings.extend(self._drift(window, baseline))
        order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
        return sorted(findings, key=lambda f: (order[f.severity], f.rule_id))

    @staticmethod
    def _drift(window: pd.DataFrame, baseline: pd.DataFrame) -> list[Finding]:
        if len(baseline) < 50 or len(window) < MIN_RUN:
            return []
        out: list[Finding] = []
        for metric in _DRIFT_METRICS:
            std = float(baseline[metric].std())
            if std == 0 or math.isnan(std):
                continue
            z = (float(window[metric].mean()) - float(baseline[metric].mean())) / std
            if abs(z) > 3:
                out.append(
                    Finding(
                        rule_id=f"DRIFT_{metric}",
                        failure_mode=None,
                        severity=Severity.INFO,
                        title=f"{metric} drifting from baseline",
                        description=(
                            f"Window mean of {metric} is {z:+.1f} standard deviations from "
                            "the earlier baseline."
                        ),
                        first_seen=window["timestamp"].iloc[0].to_pydatetime(),
                        last_seen=window["timestamp"].iloc[-1].to_pydatetime(),
                        samples_in_violation=len(window),
                        window_samples=len(window),
                        longest_run=len(window),
                        ongoing=True,
                        evidence={"z_score": round(z, 2)},
                        threshold="|z| > 3 vs baseline",
                    )
                )
        return out

    def status(self, machine_id: str, window_minutes: int = 60) -> MachineStatus:
        machine = get_machine(machine_id)
        frame = self.machine_frame(machine_id)
        findings = self.detect(machine_id, window_minutes)
        severities = {f.severity for f in findings}
        if Severity.CRITICAL in severities:
            state = "critical"
        elif Severity.WARNING in severities:
            state = "warning"
        else:
            state = "healthy"
        last = frame.iloc[-1]
        return MachineStatus(
            machine_id=machine_id,
            name=machine.name,
            status=state,
            latest={
                "air_temp_k": round(float(last["air_temp_k"]), 1),
                "process_temp_k": round(float(last["process_temp_k"]), 1),
                "temp_gap_k": round(float(last["temp_gap_k"]), 1),
                "rpm": round(float(last["rpm"]), 0),
                "torque_nm": round(float(last["torque_nm"]), 1),
                "power_w": round(float(last["power_w"]), 0),
                "tool_wear_min": round(float(last["tool_wear_min"]), 0),
            },
            last_sample_at=last["timestamp"].to_pydatetime(),
            findings=findings,
        )

    def fleet_status(self) -> list[MachineStatus]:
        known = {m.machine_id for m in MACHINES}
        return [self.status(mid) for mid in self.machine_ids() if mid in known]
