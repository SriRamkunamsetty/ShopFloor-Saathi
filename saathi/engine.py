"""Deterministic diagnosis pipeline: telemetry -> retrieval -> ranked hypotheses -> work-order draft.

This is the grounded evidence layer. The Gemini agents (saathi/agents.py) call the same tools for
natural-language, voice and photo interaction, and the API falls back to this pipeline when no LLM
is configured. Because it is deterministic it is unit-tested and used as the golden reference.
"""

from __future__ import annotations

import logging

from saathi.config import Settings
from saathi.domain import FAILURE_MODES, get_machine
from saathi.guardrails import LOTO_NOTE
from saathi.models import (
    Citation,
    DiagnosisReport,
    Finding,
    Hypothesis,
    Severity,
    WorkOrderDraft,
)
from saathi.tools.impact import estimate_impact
from saathi.tools.knowledge import Hit, KnowledgeBase, expand_text
from saathi.tools.spares import SpareParts
from saathi.tools.telemetry import TelemetryStore

log = logging.getLogger(__name__)
_ACTIONABLE = {Severity.CRITICAL, Severity.WARNING}
_MAX_CONFIDENCE = 0.95


class DiagnosisEngine:
    def __init__(
        self,
        settings: Settings,
        telemetry: TelemetryStore,
        knowledge: KnowledgeBase,
        spares: SpareParts,
    ):
        self._settings = settings
        self._telemetry = telemetry
        self._kb = knowledge
        self._spares = spares

    @staticmethod
    def _telemetry_confidence(finding: Finding, symptom_match: bool, cited: bool) -> float:
        """Heuristic confidence in [0, 0.95]; NOT a calibrated probability.

        base 0.55 (warning) / 0.70 (critical) + up to 0.15 for how long the violation has lasted
        + 0.05 if the operator's words match the failure mode + 0.05 if the manual backs it up.
        """
        base = 0.70 if finding.severity == Severity.CRITICAL else 0.55
        persistence = 0.15 * min(1.0, finding.longest_run / 12)
        score = base + persistence + (0.05 if symptom_match else 0.0) + (0.05 if cited else 0.0)
        return round(min(score, _MAX_CONFIDENCE), 2)

    def diagnose(
        self, machine_id: str, symptom: str = "", window_minutes: int = 60
    ) -> DiagnosisReport:
        machine = get_machine(machine_id)
        trace = [f"Telemetry agent: scanning last {window_minutes} min for {machine_id}"]
        findings = self._telemetry.detect(machine_id, window_minutes)
        actionable = [f for f in findings if f.severity in _ACTIONABLE and f.failure_mode]
        trace.append(
            f"Telemetry agent: {len(actionable)} actionable finding(s), "
            f"{len(findings) - len(actionable)} informational"
        )

        symptom_tokens = set(expand_text(symptom))
        incidents: list[Citation] = []
        if symptom or actionable:
            query = f"{symptom} {' '.join(f.title for f in actionable)}"
            incidents = [h.to_citation() for h in self._kb.search(query, k=3, kind="incident")]
        trace.append(f"Knowledge agent: {len(incidents)} similar past incident(s) retrieved")

        hypotheses: list[Hypothesis] = []
        seen_modes: set[str] = set()
        for finding in actionable:
            code = finding.failure_mode or ""
            if code in seen_modes:
                continue  # e.g. TWF and TWF_EARLY both point at tool wear; keep the strongest
            seen_modes.add(code)
            mode = FAILURE_MODES[code]
            chunk = self._kb.manual_for_failure_mode(code)
            citations: list[Citation] = []
            if chunk:
                matches = [
                    h
                    for h in self._kb.search(chunk.heading, k=3, kind="manual")
                    if h.chunk is chunk
                ]
                citations.append((matches[0] if matches else Hit(chunk, 0.0)).to_citation())
            mode_tokens = set(expand_text(mode.label))
            symptom_match = bool(symptom_tokens & mode_tokens)
            reasoning = [
                f"{finding.title}: {finding.description}",
                f"Rule {finding.rule_id} ({finding.threshold}) was violated in "
                f"{finding.samples_in_violation} of {finding.window_samples} samples"
                f"{' and is still ongoing' if finding.ongoing else ' but has recovered'}.",
            ]
            if symptom_match:
                reasoning.append("The operator's description matches this failure mode.")
            hypotheses.append(
                Hypothesis(
                    failure_mode=code,
                    label=mode.label,
                    confidence=self._telemetry_confidence(finding, symptom_match, bool(citations)),
                    supported_by_telemetry=True,
                    reasoning=reasoning,
                    citations=citations,
                )
            )
        trace.append(
            f"Diagnosis agent: ranked {len(hypotheses)} hypothesis(es) from telemetry evidence"
        )

        if not hypotheses and symptom:
            for hit in self._kb.search(symptom, k=2, kind="manual"):
                hypotheses.append(
                    Hypothesis(
                        failure_mode=hit.chunk.failure_mode,
                        label=hit.chunk.heading,
                        confidence=round(min(0.4, 0.2 + hit.score), 2),
                        supported_by_telemetry=False,
                        reasoning=[
                            "Telemetry shows no rule violation, so this is based only on the "
                            "operator's description and the manual. Verify on the machine."
                        ],
                        citations=[hit.to_citation()],
                    )
                )
            if hypotheses:
                trace.append(
                    "Diagnosis agent: no telemetry evidence, falling back to manual search (low confidence)"
                )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        recommended = self._draft(machine_id, hypotheses, actionable, trace) if hypotheses else None

        abstained = not hypotheses
        reason = None
        advisory = None
        if abstained:
            reason = (
                "No telemetry rule is violated and the description did not match any manual "
                "section. I do not have enough evidence to recommend an action; please check "
                "the machine and describe what you see, hear or smell."
            )
            trace.append("Diagnosis agent: abstaining (insufficient evidence)")
        elif recommended is None:
            advisory = (
                "No work order was drafted because telemetry shows no fault. The hypotheses "
                "come from the manual and your description only; check the machine and tell me "
                "what you find."
            )
        return DiagnosisReport(
            machine_id=machine_id,
            machine_name=machine.name,
            symptom=symptom,
            findings=findings,
            hypotheses=hypotheses,
            similar_incidents=incidents,
            recommended=recommended,
            abstained=abstained,
            abstain_reason=reason,
            advisory_note=advisory,
            trace=trace,
        )

    def _draft(
        self,
        machine_id: str,
        hypotheses: list[Hypothesis],
        actionable: list[Finding],
        trace: list[str],
    ) -> WorkOrderDraft | None:
        top = next((h for h in hypotheses if h.failure_mode and h.supported_by_telemetry), None)
        if top is None or top.failure_mode is None:
            return None  # never draft a work order without telemetry evidence
        code = top.failure_mode
        mode = FAILURE_MODES[code]
        machine = get_machine(machine_id)
        chunk = self._kb.manual_for_failure_mode(code)
        steps = self._kb.extract_steps(chunk) if chunk else []
        if not steps:
            steps = ["Inspect the machine per the maintenance manual and report findings."]
        parts = [self._spares.check(p) for p in mode.parts]
        finding = next(f for f in actionable if f.failure_mode == code)
        priority = "P1" if finding.severity == Severity.CRITICAL and finding.ongoing else "P2"
        impact = estimate_impact(self._settings, machine, code)
        trace.append(
            f"Action agent: drafted {priority} work order for {code}; awaiting supervisor approval"
        )
        trace.append(
            f"Impact agent: INR {impact.total_inr:,.0f} projected, {impact.co2_kg_avoided} kg CO2 (deterministic maths)"
        )
        missing = [p.part for p in parts if not p.in_stock]
        safety = [LOTO_NOTE]
        if missing:
            safety.append(f"Parts not in stock: {', '.join(missing)}. Order before starting.")
        return WorkOrderDraft(
            title=f"{mode.label} on {machine_id}",
            machine_id=machine_id,
            failure_mode=code,
            priority=priority,
            steps=steps,
            safety_notes=safety,
            parts=parts,
            est_downtime_min=mode.planned_repair_min,
            impact=impact,
            citations=top.citations,
        )
