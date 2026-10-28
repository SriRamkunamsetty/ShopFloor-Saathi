"""Pydantic models shared by the engine, repository and API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Finding(BaseModel):
    """One telemetry observation that violates a rule."""

    rule_id: str
    failure_mode: str | None = None
    severity: Severity
    title: str
    description: str
    first_seen: datetime
    last_seen: datetime
    samples_in_violation: int
    window_samples: int
    longest_run: int
    ongoing: bool
    evidence: dict[str, float]
    threshold: str


class MachineStatus(BaseModel):
    machine_id: str
    name: str
    status: Literal["healthy", "warning", "critical"]
    latest: dict[str, float]
    last_sample_at: datetime
    findings: list[Finding] = Field(default_factory=list)


class Citation(BaseModel):
    source: str
    heading: str
    kind: Literal["manual", "incident"]
    score: float
    excerpt: str


class PartCheck(BaseModel):
    part: str
    description: str
    in_stock: bool
    quantity: int
    bin: str | None = None
    lead_time_days: int | None = None


class ImpactEstimate(BaseModel):
    downtime_minutes_avoided: float
    downtime_cost_avoided_inr: float
    energy_kwh_saved: float
    energy_cost_saved_inr: float
    co2_kg_avoided: float
    total_inr: float
    assumptions: dict[str, float | str]
    note: str = "Estimate under the stated assumptions, not a measured result."


class Hypothesis(BaseModel):
    failure_mode: str | None
    label: str
    confidence: float = Field(
        ge=0, le=1, description="Heuristic score, not a calibrated probability"
    )
    supported_by_telemetry: bool
    reasoning: list[str]
    citations: list[Citation] = Field(default_factory=list)


class WorkOrderDraft(BaseModel):
    title: str
    machine_id: str
    failure_mode: str | None
    priority: Literal["P1", "P2", "P3"]
    steps: list[str]
    safety_notes: list[str]
    parts: list[PartCheck]
    est_downtime_min: int
    impact: ImpactEstimate
    citations: list[Citation] = Field(default_factory=list)


class DiagnosisReport(BaseModel):
    machine_id: str
    machine_name: str
    generated_at: datetime = Field(default_factory=utcnow)
    symptom: str
    findings: list[Finding]
    hypotheses: list[Hypothesis]
    similar_incidents: list[Citation]
    recommended: WorkOrderDraft | None
    abstained: bool = False
    abstain_reason: str | None = None
    advisory_note: str | None = None
    trace: list[str] = Field(default_factory=list, description="Ordered, human-readable plan trace")


class WorkOrderStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class WorkOrder(BaseModel):
    id: str
    status: WorkOrderStatus = WorkOrderStatus.PENDING_APPROVAL
    idempotency_key: str
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str
    draft: WorkOrderDraft
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    actual_downtime_min: float | None = None
    completed_at: datetime | None = None


# ----- API request bodies -----


class DiagnoseRequest(BaseModel):
    machine_id: str = Field(pattern=r"^M-\d{2}$")
    symptom: str = Field(default="", max_length=2000)
    create_work_order: bool = True


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    image_base64: str | None = None
    image_mime: Literal["image/jpeg", "image/png", "image/webp"] | None = None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


class CompleteRequest(BaseModel):
    actual_downtime_min: float = Field(ge=0, le=60 * 24 * 7)
