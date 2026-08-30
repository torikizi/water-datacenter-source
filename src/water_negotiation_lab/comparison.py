from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import FacilityConfig, ReservoirAddition, SimulationConfig
from .reporting import DISCLAIMER, write_chart, write_json
from .runner import run_simulation


_PUBLIC_SCENARIO_LABELS = {
    "normal_no_datacenter": "Synthetic baseline — no data center",
    "normal_dc_no_reclaimed_resident_first": "Synthetic baseline — single DC, resident-first",
    "normal_dc_no_reclaimed_proportional": "Synthetic baseline — single DC, proportional",
    "drought_no_datacenter": "Synthetic drought stress — no data center",
    "drought_dc_no_reclaimed_resident_first": "Synthetic drought stress — single DC, resident-first",
    "normal_dc_single_facility": "Synthetic baseline — single DC",
    "normal_dc_concentrated_facilities": "Synthetic baseline — three concentrated facilities",
    "normal_dc_large_campus_resident_first": "Synthetic baseline — 250 MW sensitivity, resident-first",
    "normal_dc_large_campus_proportional": "Synthetic baseline — 250 MW sensitivity, proportional",
    "normal_dc_added_distribution_reservoir": "Synthetic baseline — single DC, added distribution reservoir",
    "normal_dc_with_onsite_storage": "Synthetic baseline — single DC, onsite storage",
    "normal_reclaimed_sensitivity_00pct": "Synthetic baseline — reclaimed water 0%",
    "normal_reclaimed_sensitivity_02pct": "Synthetic baseline — reclaimed water 2%",
    "normal_reclaimed_sensitivity_25pct": "Synthetic baseline — reclaimed water 25%",
    "normal_reclaimed_sensitivity_50pct": "Synthetic baseline — reclaimed water 50%",
}


def _source_condition(climate: str) -> str:
    return "synthetic_drought_stress" if climate == "drought" else "synthetic_baseline"


def _set_reclaimed_share(facility: FacilityConfig, reclaimed_share: float) -> FacilityConfig:
    return replace(
        facility,
        reclaimed_water_share=reclaimed_share,
        potable_water_share=1.0 - reclaimed_share,
    )


def build_comparison_scenarios(base: SimulationConfig) -> list[tuple[str, str, SimulationConfig]]:
    first = base.facilities[:1]
    all_facilities = base.facilities

    def scenario(name: str, climate: str = "normal") -> SimulationConfig:
        item = base.clone(name=name)
        item.agents.enabled = False
        if climate == "drought":
            item.source_scenario_multiplier *= base.drought_source_multiplier
            if item.regional_source.enabled:
                item.regional_source.reference_daily_net_change_l = (
                    base.comparison_drought_regional_net_change_l_per_day
                )
        return item

    cases: list[tuple[str, str, SimulationConfig]] = []
    no_dc_normal = scenario("normal_no_datacenter")
    no_dc_normal.facilities = []
    cases.append((no_dc_normal.name, "normal", no_dc_normal))

    dc_resident = scenario("normal_dc_no_reclaimed_resident_first")
    dc_resident.allocation_policy = "resident_first"
    dc_resident.facilities = [_set_reclaimed_share(item, 0.0) for item in first]
    cases.append((dc_resident.name, "normal", dc_resident))

    dc_proportional = scenario("normal_dc_no_reclaimed_proportional")
    dc_proportional.allocation_policy = "proportional"
    dc_proportional.facilities = [_set_reclaimed_share(item, 0.0) for item in first]
    cases.append((dc_proportional.name, "normal", dc_proportional))

    no_dc_drought = scenario("drought_no_datacenter", "drought")
    no_dc_drought.facilities = []
    cases.append((no_dc_drought.name, "drought", no_dc_drought))

    dc_drought = scenario("drought_dc_no_reclaimed_resident_first", "drought")
    dc_drought.allocation_policy = "resident_first"
    dc_drought.facilities = [_set_reclaimed_share(item, 0.0) for item in first]
    cases.append((dc_drought.name, "drought", dc_drought))

    single = scenario("normal_dc_single_facility")
    single.facilities = list(first)
    cases.append((single.name, "normal", single))

    concentrated = scenario("normal_dc_concentrated_facilities")
    concentrated.facilities = list(all_facilities)
    cases.append((concentrated.name, "normal", concentrated))

    if base.comparison_large_campus_it_load_mw > 0 and first:
        large_resident_first = scenario("normal_dc_large_campus_resident_first")
        large_resident_first.allocation_policy = "resident_first"
        large_resident_first.facilities = [
            replace(
                _set_reclaimed_share(first[0], 0.0),
                name="hypothetical_large_campus",
                it_load_mw=base.comparison_large_campus_it_load_mw,
            )
        ]
        cases.append((large_resident_first.name, "normal", large_resident_first))

        large_proportional = scenario("normal_dc_large_campus_proportional")
        large_proportional.allocation_policy = "proportional"
        large_proportional.facilities = [
            replace(
                _set_reclaimed_share(first[0], 0.0),
                name="hypothetical_large_campus",
                it_load_mw=base.comparison_large_campus_it_load_mw,
            )
        ]
        cases.append((large_proportional.name, "normal", large_proportional))

    if base.comparison_added_reservoir_capacity_l > 0:
        added_reservoir = scenario("normal_dc_added_distribution_reservoir")
        added_reservoir.facilities = [_set_reclaimed_share(item, 0.0) for item in first]
        added_reservoir.reservoir_additions.append(
            ReservoirAddition(
                name="hypothetical_distribution_reservoir",
                commission_day=base.comparison_added_reservoir_commission_day,
                added_capacity_l=base.comparison_added_reservoir_capacity_l,
                commissioning_fill_l=base.comparison_added_reservoir_fill_l,
            )
        )
        cases.append((added_reservoir.name, "normal", added_reservoir))

    if base.comparison_dc_onsite_storage_capacity_l > 0 and first:
        onsite = scenario("normal_dc_with_onsite_storage")
        onsite.facilities = [
            replace(
                first[0],
                onsite_potable_storage_capacity_l=(
                    base.comparison_dc_onsite_storage_capacity_l
                ),
                onsite_potable_initial_storage_l=(
                    base.comparison_dc_onsite_initial_storage_l
                ),
                onsite_potable_max_refill_l_per_day=(
                    base.comparison_dc_onsite_max_refill_l_per_day
                ),
            )
        ]
        cases.append((onsite.name, "normal", onsite))

    for share in base.sensitivity_reclaimed_shares:
        percentage = int(round(share * 100))
        sensitivity = scenario(f"normal_reclaimed_sensitivity_{percentage:02d}pct")
        sensitivity.facilities = [_set_reclaimed_share(item, share) for item in first]
        cases.append((sensitivity.name, "normal", sensitivity))
    for _, _, item in cases:
        item.validate()
    return cases


def run_comparison(base: SimulationConfig, output_dir: str | Path) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    results: dict[str, tuple[str, list[dict[str, Any]], dict[str, Any]]] = {}
    for name, climate, config in build_comparison_scenarios(base):
        rows, _, summary = run_simulation(config, out / name, provider=None)
        results[name] = (climate, rows, summary)

    normal_baseline_shortage = results["normal_no_datacenter"][2]["metrics_l"][
        "resident_shortage"
    ]
    drought_baseline_shortage = results["drought_no_datacenter"][2]["metrics_l"][
        "resident_shortage"
    ]
    summary_rows = []
    for name, (climate, rows, summary) in results.items():
        baseline = (
            drought_baseline_shortage if climate == "drought" else normal_baseline_shortage
        )
        resident_shortage = summary["metrics_l"]["resident_shortage"]
        facilities = summary["configuration"]["facilities"]
        reclaimed_share = (
            float(facilities[0]["reclaimed_water_share"]) if facilities else None
        )
        if reclaimed_share is None:
            reclaimed_context = "not_applicable_no_datacenter"
        elif reclaimed_share == 0:
            reclaimed_context = "baseline_no_dedicated_reclaimed_supply"
        elif reclaimed_share <= 0.02:
            reclaimed_context = "low_use_sensitivity_not_an_observed_dc_rate"
        else:
            reclaimed_context = "hypothetical_infrastructure_sensitivity"
        summary_rows.append(
            {
                "scenario": name,
                "scenario_label": _PUBLIC_SCENARIO_LABELS.get(name, name),
                "climate": climate,
                "source_condition": _source_condition(climate),
                "resident_shortage_l": resident_shortage,
                "additional_resident_shortage_vs_no_dc_l": resident_shortage - baseline,
                "final_storage_l": summary["metrics_l"]["final_storage"],
                "datacenter_potable_withdrawal_l": summary["metrics_l"][
                    "datacenter_potable_withdrawal"
                ],
                "datacenter_potable_consumptive_use_l": summary["metrics_l"][
                    "datacenter_potable_consumptive_use"
                ],
                "datacenter_reclaimed_withdrawal_l": summary["metrics_l"][
                    "datacenter_reclaimed_withdrawal"
                ],
                "reclaimed_water_share_input": reclaimed_share,
                "reclaimed_water_context": reclaimed_context,
                "final_distribution_storage_capacity_l": summary["configuration"][
                    "storage_capacity_l"
                ]
                + sum(
                    item["added_capacity_l"]
                    for item in summary["configuration"]["reservoir_additions"]
                ),
                "reservoir_commissioning_fill_l": summary["metrics_l"][
                    "total_reservoir_commissioning_fill"
                ],
                "datacenter_onsite_storage_capacity_l": summary["metrics_l"][
                    "datacenter_onsite_storage_capacity_final"
                ],
                "datacenter_onsite_storage_final_l": summary["metrics_l"][
                    "datacenter_onsite_storage_final"
                ],
                "regional_source_storage_final_l": summary["metrics_l"][
                    "regional_source_storage_final"
                ],
                "regional_source_incremental_dc_consumptive_use_l": summary["metrics_l"][
                    "regional_source_incremental_dc_consumptive_use"
                ],
                "observed_precipitation_total_mm": sum(
                    float(row["observed_precipitation_mm"]) for row in rows
                ),
                "observed_reservoir_reference_final_l": float(
                    rows[-1]["observed_reservoir_reference_storage_l"]
                ),
                "observed_reservoir_counterfactual_with_dc_final_l": float(
                    rows[-1]["observed_reservoir_counterfactual_with_dc_l"]
                ),
                "observed_reservoir_counterfactual_delta_l": float(
                    rows[-1]["observed_reservoir_counterfactual_delta_l"]
                ),
                "first_local_buffer_empty_day": next(
                    (
                        int(row["day"])
                        for row in rows
                        if float(row["storage_end_l"]) <= 1e-6
                    ),
                    None,
                ),
                "first_resident_shortage_day": next(
                    (
                        int(row["day"])
                        for row in rows
                        if float(row["resident_shortage_l"]) > 1e-6
                    ),
                    None,
                ),
                "resident_shortage_days": summary["resident_shortage_days"],
            }
        )
    comparison_summary = {
        "schema_version": 1,
        "disclaimer": DISCLAIMER,
        "counterfactual_definition": (
            "additional resident shortage = resident shortage with data center minus "
            "resident shortage without data center under the same synthetic baseline "
            "or synthetic drought-stress supply condition"
        ),
        "observed_reference": {
            "regional_source_used_in_simulation": base.regional_source.enabled,
            "regional_source_name": base.regional_source.name,
            "observed_date": base.regional_source.observed_date,
            "initial_storage_l": (
                base.regional_source.initial_storage_l
                if base.regional_source.enabled
                else None
            ),
            "capacity_l": (
                base.regional_source.capacity_l if base.regional_source.enabled else None
            ),
            "evidence_url": base.regional_source.evidence_url,
            "regional_source_note": (
                "The allocation layer uses the published 48 ML Hokuso distribution-"
                "reservoir capacity as a full-start operating-buffer proxy. A separate "
                "2025 observed context is diagnostic only."
                if not base.regional_source.enabled
                else "The dated regional-source reference layer is enabled."
            ),
            "community_population_approximation": base.population,
            "observed_system_average_l_per_person_day": (
                base.per_capita_potable_demand_l_per_day
            ),
        },
        "observed_context": {
            "enabled": base.observed_context.enabled,
            "precipitation_station": base.observed_context.precipitation_station,
            "precipitation_reference_start_date": (
                base.observed_context.precipitation_reference_start_date
            ),
            "precipitation_total_mm": sum(
                base.observed_context.daily_precipitation_mm
            ),
            "precipitation_used_as_inflow": (
                base.observed_context.precipitation_used_as_inflow
            ),
            "precipitation_source_url": (
                base.observed_context.precipitation_source_url
            ),
            "reservoir_name": base.observed_context.reservoir_name,
            "reservoir_reference_start_date": (
                base.observed_context.reservoir_reference_start_date
            ),
            "reservoir_interpolation": (
                base.observed_context.reservoir_interpolation
            ),
            "reservoir_used_for_allocation": (
                base.observed_context.reservoir_used_for_allocation
            ),
            "reservoir_source_url": base.observed_context.reservoir_source_url,
            "note": (
                "2025 observations are replayed by month/day as historical context. "
                "Rainfall is not converted to potable inflow, and the nine-dam total "
                "is not treated as Inzai-only allocatable storage."
            ),
        },
        "scenarios": summary_rows,
    }
    write_json(out / "comparison_summary.json", comparison_summary)
    chart_series = [
        (_PUBLIC_SCENARIO_LABELS.get(name, name), value[1])
        for name, value in results.items()
    ]
    storage_panels = []
    if base.regional_source.enabled:
        storage_panels.append(
            ("Regional source reference storage", "regional_source_storage_end_l", chart_series)
        )
    storage_panels.append(
        ("Hokuso 48 ML distribution-buffer proxy", "storage_end_l", chart_series)
    )
    write_chart(
        out / "storage_comparison.svg",
        "Local buffer under modeled service-supply conditions",
        storage_panels,
    )
    write_chart(
        out / "resident_supply_shortage.svg",
        "Resident potable-water outcomes",
        [
            ("Resident supply", "resident_supply_l", chart_series),
            ("Resident cumulative shortage", "cumulative_resident_shortage_l", chart_series),
        ],
    )
    write_chart(
        out / "datacenter_potable_use.svg",
        "Data-center potable-water accounting",
        [
            ("Potable withdrawal", "datacenter_potable_withdrawal_l", chart_series),
            ("Potable consumptive use", "datacenter_potable_consumptive_use_l", chart_series),
        ],
    )
    return comparison_summary
