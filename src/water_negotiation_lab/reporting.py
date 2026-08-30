from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any, Iterable


DISCLAIMER = (
    "This hypothesis-generation simulation combines explicitly identified observed "
    "references with synthetic counterfactual assumptions; it is not a real-world forecast."
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def summarize(rows: list[dict[str, Any]], configuration: dict[str, Any]) -> dict[str, Any]:
    def total(field: str) -> float:
        return sum(float(row[field]) for row in rows)

    resident_shortage = total("resident_shortage_l")
    return {
        "schema_version": 1,
        "scenario": rows[0]["scenario"],
        "disclaimer": DISCLAIMER,
        "days": len(rows),
        "configuration": configuration,
        "metrics_l": {
            "total_source_inflow": total("source_inflow_l"),
            "total_reservoir_commissioning_fill": total(
                "reservoir_commissioning_fill_l"
            ),
            "total_spill": total("spill_l"),
            "final_storage": float(rows[-1]["storage_end_l"]),
            "minimum_storage": min(float(row["storage_end_l"]) for row in rows),
            "resident_demand": total("resident_demand_l"),
            "resident_supply": total("resident_supply_l"),
            "resident_shortage": resident_shortage,
            "datacenter_direct_water_requirement": total(
                "datacenter_direct_water_requirement_l"
            ),
            "datacenter_potable_requirement": total("datacenter_potable_requirement_l"),
            "datacenter_potable_withdrawal": total("datacenter_potable_withdrawal_l"),
            "datacenter_potable_to_process": total("datacenter_potable_to_process_l"),
            "datacenter_reclaimed_withdrawal": total("datacenter_reclaimed_withdrawal_l"),
            "datacenter_water_shortage": total("datacenter_water_shortage_l"),
            "datacenter_evaporation": total("datacenter_evaporation_l"),
            "datacenter_blowdown": total("datacenter_blowdown_l"),
            "datacenter_regional_return": total("datacenter_regional_return_l"),
            "datacenter_recoverable_wastewater": total(
                "datacenter_recoverable_wastewater_l"
            ),
            "datacenter_potable_consumptive_use": total(
                "datacenter_potable_consumptive_use_l"
            ),
            "datacenter_onsite_storage_final": float(
                rows[-1]["datacenter_onsite_storage_end_l"]
            ),
            "datacenter_onsite_storage_capacity_final": float(
                rows[-1]["datacenter_onsite_storage_capacity_l"]
            ),
            "regional_source_storage_final": float(
                rows[-1]["regional_source_storage_end_l"]
            ),
            "regional_source_storage_minimum": min(
                float(row["regional_source_storage_end_l"]) for row in rows
            ),
            "regional_source_incremental_dc_consumptive_use": total(
                "regional_source_incremental_dc_consumptive_use_l"
            ),
        },
        "observed_context_metrics": {
            "precipitation_total_mm": total("observed_precipitation_mm"),
            "precipitation_used_as_inflow": bool(
                rows[-1]["observed_precipitation_used_as_inflow"]
            ),
            "reservoir_reference_final_l": float(
                rows[-1]["observed_reservoir_reference_storage_l"]
            ),
            "reservoir_counterfactual_with_dc_final_l": float(
                rows[-1]["observed_reservoir_counterfactual_with_dc_l"]
            ),
            "reservoir_counterfactual_delta_l": float(
                rows[-1]["observed_reservoir_counterfactual_delta_l"]
            ),
            "reservoir_used_for_allocation": bool(
                rows[-1]["observed_reservoir_used_for_allocation"]
            ),
        },
        "resident_shortage_days": sum(float(row["resident_shortage_l"]) > 0 for row in rows),
        "datacenter_shortage_days": sum(
            float(row["datacenter_water_shortage_l"]) > 0 for row in rows
        ),
        "maximum_absolute_water_balance_error_l": max(
            abs(float(row["water_balance_error_l"])) for row in rows
        ),
        "maximum_absolute_onsite_storage_balance_error_l": max(
            abs(float(row["datacenter_onsite_storage_balance_error_l"]))
            for row in rows
        ),
        "maximum_absolute_regional_source_balance_error_l": max(
            abs(float(row["regional_source_balance_error_l"])) for row in rows
        ),
    }


_COLORS = (
    "#1261A0",
    "#E4572E",
    "#2E8B57",
    "#8E5EA2",
    "#F2A900",
    "#2F4B7C",
    "#D45087",
    "#00A6A6",
    "#7A5195",
    "#6B705C",
    "#BC4749",
)


def _polyline(values: list[float], x: float, y: float, width: float, height: float, maximum: float) -> str:
    denominator = max(1, len(values) - 1)
    points = []
    for index, value in enumerate(values):
        px = x + width * index / denominator
        py = y + height - height * value / maximum
        points.append(f"{px:.2f},{py:.2f}")
    return " ".join(points)


def write_chart(
    path: Path,
    title: str,
    panels: list[tuple[str, str, list[tuple[str, list[dict[str, Any]]]]]],
) -> None:
    width = 1100
    panel_height = 260
    top = 70
    height = top + len(panels) * panel_height + 70
    left = 105
    right = 280
    plot_width = width - left - right
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;fill:#243447}.title{font-size:24px;font-weight:700}.panel{font-size:16px;font-weight:600}.axis{font-size:12px}.legend{font-size:11px}</style>',
        f'<text x="40" y="38" class="title">{escape(title)}</text>',
    ]
    for panel_index, (panel_title, field, series) in enumerate(panels):
        y = top + panel_index * panel_height
        values_by_series = [
            [float(row[field]) for row in rows] for _, rows in series if rows
        ]
        maximum = max((max(values) for values in values_by_series if values), default=1.0)
        maximum = maximum if maximum > 0 else 1.0
        elements.extend(
            [
                f'<text x="{left}" y="{y - 12}" class="panel">{escape(panel_title)}</text>',
                f'<line x1="{left}" y1="{y}" x2="{left}" y2="{y + 190}" stroke="#718096"/>',
                f'<line x1="{left}" y1="{y + 190}" x2="{left + plot_width}" y2="{y + 190}" stroke="#718096"/>',
                f'<text x="{left - 10}" y="{y + 5}" text-anchor="end" class="axis">{maximum:,.0f}</text>',
                f'<text x="{left - 10}" y="{y + 194}" text-anchor="end" class="axis">0</text>',
                f'<text x="{left}" y="{y + 212}" class="axis">day 1</text>',
            ]
        )
        max_days = max((len(rows) for _, rows in series), default=1)
        elements.append(
            f'<text x="{left + plot_width}" y="{y + 212}" text-anchor="end" class="axis">day {max_days}</text>'
        )
        for series_index, (label, rows) in enumerate(series):
            color = _COLORS[series_index % len(_COLORS)]
            values = [float(row[field]) for row in rows]
            points = _polyline(values, left, y, plot_width, 190, maximum)
            legend_y = y + 5 + series_index * 17
            elements.extend(
                [
                    f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>',
                    f'<line x1="{left + plot_width + 18}" y1="{legend_y}" x2="{left + plot_width + 38}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>',
                    f'<text x="{left + plot_width + 45}" y="{legend_y + 4}" class="legend">{escape(label)}</text>',
                ]
            )
    elements.append(
        f'<text x="40" y="{height - 24}" class="axis">Synthetic assumptions; not a real-world forecast. Unit: liters.</text>'
    )
    elements.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(elements) + "\n", encoding="utf-8")
