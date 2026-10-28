"""Spare-parts inventory lookup (read-only)."""

from __future__ import annotations

import json
from pathlib import Path

from saathi.models import PartCheck

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "spare_parts.json"


class SpareParts:
    def __init__(self, inventory: dict[str, dict]):
        self._inventory = inventory

    @classmethod
    def from_default_data(cls) -> SpareParts:
        return cls(json.loads(DEFAULT_PATH.read_text(encoding="utf-8")))

    def check(self, part: str, quantity_needed: int = 1) -> PartCheck:
        if quantity_needed < 1:
            raise ValueError("quantity_needed must be at least 1")
        item = self._inventory.get(part)
        if item is None:
            return PartCheck(
                part=part,
                description="Not in inventory catalogue",
                in_stock=False,
                quantity=0,
            )
        return PartCheck(
            part=part,
            description=item["description"],
            in_stock=item["quantity"] >= quantity_needed,
            quantity=item["quantity"],
            bin=item.get("bin"),
            lead_time_days=item.get("lead_time_days"),
        )
