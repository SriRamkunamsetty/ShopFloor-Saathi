"""Golden scenarios for the deterministic evidence pipeline (runs in CI, no LLM needed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCENARIOS = json.loads(
    (Path(__file__).parent.parent / "eval" / "golden_scenarios.json").read_text()
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_golden_scenario(services, scenario):
    expect = scenario["expect"]
    report = services.engine.diagnose(scenario["machine_id"], scenario["symptom"])

    assert report.abstained is expect["abstained"]
    assert (report.recommended is not None) is expect["work_order"]
    if expect.get("advisory"):
        assert report.advisory_note
    if report.abstained:
        assert report.abstain_reason and not report.hypotheses
        return

    top = report.hypotheses[0]
    assert top.failure_mode == expect["failure_mode"]
    assert 0 <= top.confidence <= 0.95
    assert top.citations, "every hypothesis must carry a citation"
    if expect["work_order"]:
        rec = report.recommended
        assert rec.priority == expect["priority"]
        assert [p.part for p in rec.parts if not p.in_stock] == expect["missing_parts"]
        assert rec.steps and rec.steps[0].lower().startswith(("stop", "reduce"))
        assert any("lockout" in n.lower() for n in rec.safety_notes)
        assert rec.impact.total_inr > 0


def test_no_work_order_without_telemetry_evidence(services):
    """A confident-sounding complaint alone must never produce a work order."""
    report = services.engine.diagnose("M-08", "machine garam ho raha hai overheating hot smell fan")
    assert report.recommended is None


def test_trace_lists_each_agent_stage(services):
    trace = services.engine.diagnose("M-07", "garam").trace
    for stage in (
        "Telemetry agent",
        "Knowledge agent",
        "Diagnosis agent",
        "Action agent",
        "Impact agent",
    ):
        assert any(line.startswith(stage) for line in trace), stage
