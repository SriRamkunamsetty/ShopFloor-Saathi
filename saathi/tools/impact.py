"""Deterministic impact accounting. All arithmetic lives here, never in the LLM."""

from __future__ import annotations

from saathi.config import Settings
from saathi.domain import FAILURE_MODES, Machine
from saathi.models import ImpactEstimate


def estimate_impact(
    settings: Settings,
    machine: Machine,
    failure_mode: str,
    *,
    planned_repair_min: float | None = None,
) -> ImpactEstimate:
    """Compare 'fix now in a planned stop' with 'ignore until it fails'.

    downtime avoided = unplanned downtime - planned repair
    energy saved     = rated kW x degraded hours avoided x degraded-power penalty
    CO2 avoided      = energy saved x grid emission factor
    """
    mode = FAILURE_MODES.get(failure_mode)
    if mode is None:
        raise KeyError(f"Unknown failure mode {failure_mode!r}")
    repair_min = mode.planned_repair_min if planned_repair_min is None else planned_repair_min
    if repair_min < 0:
        raise ValueError("planned_repair_min must not be negative")

    avoided_min = max(mode.unplanned_downtime_min - repair_min, 0.0)
    downtime_cost = avoided_min / 60.0 * settings.downtime_cost_inr_per_hour
    energy_kwh = machine.rated_kw * mode.degraded_hours_if_ignored * mode.degraded_power_penalty
    energy_cost = energy_kwh * settings.energy_tariff_inr_per_kwh
    co2_kg = energy_kwh * settings.grid_ef_kg_per_kwh

    return ImpactEstimate(
        downtime_minutes_avoided=round(avoided_min, 1),
        downtime_cost_avoided_inr=round(downtime_cost, 2),
        energy_kwh_saved=round(energy_kwh, 2),
        energy_cost_saved_inr=round(energy_cost, 2),
        co2_kg_avoided=round(co2_kg, 2),
        total_inr=round(downtime_cost + energy_cost, 2),
        assumptions={
            "failure_mode": failure_mode,
            "unplanned_downtime_min_if_ignored": mode.unplanned_downtime_min,
            "planned_repair_min": repair_min,
            "downtime_cost_inr_per_hour": settings.downtime_cost_inr_per_hour,
            "machine_rated_kw": machine.rated_kw,
            "degraded_hours_if_ignored": mode.degraded_hours_if_ignored,
            "degraded_power_penalty": mode.degraded_power_penalty,
            "energy_tariff_inr_per_kwh": settings.energy_tariff_inr_per_kwh,
            "grid_emission_factor_kg_per_kwh": settings.grid_ef_kg_per_kwh,
        },
    )
