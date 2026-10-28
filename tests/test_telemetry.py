from __future__ import annotations

import pandas as pd
import pytest

from saathi.data.synthetic import generate_telemetry
from saathi.models import Severity
from saathi.tools.telemetry import TelemetryStore
from tests.conftest import NOW

HEALTHY = ["M-01", "M-04", "M-06", "M-08"]
FAULTS = {"M-07": "HDF", "M-03": "TWF", "M-05": "PWF", "M-02": "OSF"}


@pytest.mark.parametrize("machine_id,rule", FAULTS.items())
def test_injected_faults_are_detected_as_critical(telemetry, machine_id, rule):
    status = telemetry.status(machine_id)
    assert status.status == "critical"
    finding = next(f for f in status.findings if f.rule_id == rule)
    assert finding.severity == Severity.CRITICAL and finding.ongoing


@pytest.mark.parametrize("machine_id", HEALTHY)
def test_healthy_machines_have_no_actionable_findings(telemetry, machine_id):
    status = telemetry.status(machine_id)
    assert status.status == "healthy"
    assert all(f.severity == Severity.INFO for f in status.findings)


def test_no_false_positives_or_misses_across_many_seeds():
    for seed in range(25):
        store = TelemetryStore(generate_telemetry(seed=seed, end=NOW))
        for machine_id in HEALTHY:
            assert store.status(machine_id).status == "healthy", (seed, machine_id)
        for machine_id, rule in FAULTS.items():
            rules = {f.rule_id for f in store.status(machine_id).findings}
            assert rule in rules, (seed, machine_id)


def test_overstrain_scenario_does_not_also_trip_power_rule(telemetry):
    rules = {f.rule_id for f in telemetry.status("M-02").findings}
    assert "OSF" in rules and "PWF" not in rules


def test_hdf_requires_both_conditions(telemetry):
    df = generate_telemetry(seed=7, end=NOW)
    mask = (df["machine_id"] == "M-01") & (
        df["timestamp"] >= df["timestamp"].max() - pd.Timedelta(minutes=55)
    )
    df.loc[mask, "rpm"] = 1300  # low speed alone (gap stays normal) must not fire HDF
    rules = {f.rule_id for f in TelemetryStore(df).status("M-01").findings}
    assert "HDF" not in rules


def test_single_noisy_sample_does_not_fire(telemetry):
    df = generate_telemetry(seed=7, end=NOW)
    idx = df[df["machine_id"] == "M-04"].index[-1]
    df.loc[idx, "torque_nm"] = 90  # one spike only
    assert TelemetryStore(df).status("M-04").status == "healthy"


def test_invalid_and_unknown_machine_ids(telemetry):
    with pytest.raises(ValueError):
        telemetry.status("bad-id")
    with pytest.raises(KeyError):
        telemetry.status("M-99")


@pytest.mark.parametrize("window", [0, 5, 100_000])
def test_window_bounds_are_validated(telemetry, window):
    with pytest.raises(ValueError):
        telemetry.detect("M-01", window)


def test_missing_columns_and_empty_input_rejected():
    with pytest.raises(ValueError, match="missing columns"):
        TelemetryStore(pd.DataFrame({"timestamp": [1], "machine_id": ["M-01"]}))
    with pytest.raises(ValueError, match="empty"):
        TelemetryStore(generate_telemetry(end=NOW).iloc[0:0])


def test_synthetic_data_is_deterministic():
    a = generate_telemetry(seed=3, end=NOW)
    b = generate_telemetry(seed=3, end=NOW)
    pd.testing.assert_frame_equal(a, b)
