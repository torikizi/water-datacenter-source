#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


FIELDS = (
    "scenario",
    "scenario_label",
    "source_condition",
    "climate_internal",
    "resident_shortage_ml",
    "additional_resident_shortage_vs_no_dc_ml",
    "datacenter_potable_withdrawal_ml",
    "datacenter_potable_consumptive_use_ml",
    "datacenter_reclaimed_withdrawal_ml",
    "final_local_buffer_ml",
    "final_regional_source_ml",
    "first_local_buffer_empty_day",
    "first_resident_shortage_day",
    "resident_shortage_days",
    "reclaimed_water_share_input",
    "reclaimed_water_context",
)


def _ml(row: dict[str, Any], key: str) -> float:
    return round(float(row[key]) / 1_000_000.0, 6)


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario": row["scenario"],
        "scenario_label": row["scenario_label"],
        "source_condition": row["source_condition"],
        "climate_internal": row["climate"],
        "resident_shortage_ml": _ml(row, "resident_shortage_l"),
        "additional_resident_shortage_vs_no_dc_ml": _ml(
            row, "additional_resident_shortage_vs_no_dc_l"
        ),
        "datacenter_potable_withdrawal_ml": _ml(
            row, "datacenter_potable_withdrawal_l"
        ),
        "datacenter_potable_consumptive_use_ml": _ml(
            row, "datacenter_potable_consumptive_use_l"
        ),
        "datacenter_reclaimed_withdrawal_ml": _ml(
            row, "datacenter_reclaimed_withdrawal_l"
        ),
        "final_local_buffer_ml": _ml(row, "final_storage_l"),
        "final_regional_source_ml": _ml(row, "regional_source_storage_final_l"),
        "first_local_buffer_empty_day": row["first_local_buffer_empty_day"],
        "first_resident_shortage_day": row["first_resident_shortage_day"],
        "resident_shortage_days": row["resident_shortage_days"],
        "reclaimed_water_share_input": row["reclaimed_water_share_input"],
        "reclaimed_water_context": row["reclaimed_water_context"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export comparison summary as a flat CSV")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(flatten(row) for row in payload["scenarios"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
