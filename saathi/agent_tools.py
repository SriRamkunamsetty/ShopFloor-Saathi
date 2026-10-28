"""Tool functions exposed to the Gemini agents (Google ADK).

Design rules:
  * Tools never raise: errors come back as {"error": ...} so the model can recover or abstain.
  * The model supplies only identifiers and free text. Work-order content (steps, parts, impact)
    always comes from the deterministic engine, so the LLM cannot invent maintenance actions.
  * There is no approval tool. Agents can only draft; a human supervisor approves via the API.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from google.adk.tools import ToolContext

from saathi.guardrails import GuardrailError, sanitize_text
from saathi.services import Services, get_services

log = logging.getLogger(__name__)


def _svc() -> Services:
    return get_services()


def _safe(fn):
    """Convert exceptions into tool-level error payloads."""

    @functools.wraps(
        fn
    )  # keeps the real signature + docstring, which ADK turns into the tool schema
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError, GuardrailError) as exc:
            return {"error": str(exc.args[0] if exc.args else exc)}
        except Exception:  # noqa: BLE001 - last line of defence for the agent loop
            log.exception("tool failed", extra={"tool": fn.__name__})
            return {"error": "Internal tool error; try again or report to the supervisor."}

    return wrapper


@_safe
def list_machines_status() -> dict[str, Any]:
    """List every machine with its health status (healthy, warning, critical) and latest readings.

    Use this first when the user does not name a machine or asks what needs attention.
    """
    fleet = _svc().telemetry.fleet_status()
    return {
        "machines": [
            {
                "machine_id": s.machine_id,
                "name": s.name,
                "status": s.status,
                "latest": s.latest,
                "top_finding": s.findings[0].title if s.findings else None,
            }
            for s in fleet
        ]
    }


@_safe
def get_telemetry_findings(machine_id: str, window_minutes: int = 60) -> dict[str, Any]:
    """Run the rule-based anomaly checks for one machine over a recent window.

    Args:
        machine_id: Machine id such as "M-07".
        window_minutes: Look-back window in minutes (10 to 1440).
    """
    status = _svc().telemetry.status(machine_id, window_minutes)
    return {
        "machine_id": machine_id,
        "status": status.status,
        "latest": status.latest,
        "findings": [f.model_dump(mode="json") for f in status.findings],
    }


@_safe
def search_manuals(query: str, k: int = 3) -> dict[str, Any]:
    """Search the maintenance manuals. Returns cited passages, or an empty list if nothing matches.

    Args:
        query: What to look for, in English, Hindi or Hinglish (e.g. "machine garam ho raha hai").
        k: Number of passages (1 to 5).
    """
    query = sanitize_text(query, 500)
    hits = _svc().knowledge.search(query, k=max(1, min(k, 5)), kind="manual")
    return {"results": [h.to_citation().model_dump() for h in hits]}


@_safe
def find_similar_incidents(query: str, k: int = 3) -> dict[str, Any]:
    """Find similar past maintenance log entries (terse shop-floor notes are understood).

    Args:
        query: Symptom description or fault keywords.
        k: Number of incidents (1 to 5).
    """
    query = sanitize_text(query, 500)
    hits = _svc().knowledge.search(query, k=max(1, min(k, 5)), kind="incident")
    return {"results": [h.to_citation().model_dump() for h in hits]}


@_safe
def check_spare_part(part: str) -> dict[str, Any]:
    """Check whether a spare part is in stock, with its bin and supplier lead time.

    Args:
        part: Part code such as "cooling-fan-assy".
    """
    return _svc().spares.check(sanitize_text(part, 80)).model_dump()


@_safe
def calculate_impact(machine_id: str, failure_mode: str) -> dict[str, Any]:
    """Deterministically estimate avoided downtime cost, energy and CO2 for fixing a fault now.

    Never compute these numbers yourself; always quote this tool's output and its assumptions.

    Args:
        machine_id: Machine id such as "M-07".
        failure_mode: One of HDF, TWF, PWF, OSF.
    """
    from saathi.domain import get_machine
    from saathi.tools.impact import estimate_impact

    svc = _svc()
    return estimate_impact(svc.settings, get_machine(machine_id), failure_mode).model_dump()


@_safe
def draft_work_order(machine_id: str, symptom: str, tool_context: ToolContext) -> dict[str, Any]:
    """Draft a work order for a supervisor to approve. It does NOT start any work.

    The steps, parts and impact are generated from the evidence pipeline, not from your text.
    If telemetry shows no fault, no work order is created and the reason is returned.

    Args:
        machine_id: Machine id such as "M-07".
        symptom: The operator's description of the problem, in their own words.
    """
    svc = _svc()
    symptom = sanitize_text(symptom, 500)
    report = svc.engine.diagnose(machine_id, symptom)
    if report.recommended is None:
        return {
            "created": False,
            "reason": report.advisory_note or report.abstain_reason,
            "hypotheses": [h.label for h in report.hypotheses],
        }
    order, created = svc.work_orders.create_draft(
        report.recommended, tool_context.user_id or "agent"
    )
    return {
        "created": created,
        "already_open": not created,
        "work_order_id": order.id,
        "status": order.status.value,
        "priority": order.draft.priority,
        "title": order.draft.title,
        "steps": order.draft.steps,
        "parts": [p.model_dump() for p in order.draft.parts],
        "safety_notes": order.draft.safety_notes,
        "estimated_downtime_min": order.draft.est_downtime_min,
        "impact": order.draft.impact.model_dump(),
        "next": "A supervisor must approve this work order before anyone starts the work.",
    }
