from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

from .agents import parse_decision_response
from .comparison import build_comparison_scenarios
from .config import SimulationConfig
from .engine import simulate_day


_ORDER = (
    "normal_no_datacenter",
    "normal_dc_no_reclaimed_resident_first",
    "normal_reclaimed_sensitivity_02pct",
    "normal_reclaimed_sensitivity_25pct",
    "normal_reclaimed_sensitivity_50pct",
    "normal_dc_no_reclaimed_proportional",
    "normal_dc_concentrated_facilities",
    "normal_dc_large_campus_resident_first",
    "normal_dc_large_campus_proportional",
    "normal_dc_added_distribution_reservoir",
    "normal_dc_with_onsite_storage",
    "drought_no_datacenter",
    "drought_dc_no_reclaimed_resident_first",
)

_LABELS = {
    "normal_no_datacenter": "基準供給（合成）｜データセンターなし",
    "normal_dc_no_reclaimed_resident_first": "基準供給（合成）｜単独DC・再生水0%・住民優先",
    "normal_reclaimed_sensitivity_02pct": "基準供給（合成）｜単独DC・再生水2%（低利用感度）",
    "normal_reclaimed_sensitivity_25pct": "基準供給（合成）｜単独DC・再生水25%（仮想）",
    "normal_reclaimed_sensitivity_50pct": "基準供給（合成）｜単独DC・再生水50%（仮想）",
    "normal_dc_no_reclaimed_proportional": "基準供給（合成）｜単独DC・再生水0%・比例配分",
    "normal_dc_concentrated_facilities": "基準供給（合成）｜3施設集中・再生水0%",
    "normal_dc_large_campus_resident_first": "基準供給（合成）｜250MW IT負荷感度（仮想）・住民優先",
    "normal_dc_large_campus_proportional": "基準供給（合成）｜250MW IT負荷感度（仮想）・比例配分",
    "normal_dc_added_distribution_reservoir": "基準供給（合成）｜単独DC・地域配水池を増設",
    "normal_dc_with_onsite_storage": "基準供給（合成）｜単独DC・敷地内水槽あり",
    "drought_no_datacenter": "渇水ストレス｜データセンターなし",
    "drought_dc_no_reclaimed_resident_first": "渇水ストレス（供給93%・合成）｜単独DC・再生水0%",
}


def _period_name(start_date: str, days: int) -> str:
    if start_date == "2026-06-01" and days == 102:
        return "夏季〜初秋重点期間"
    return "シミュレーション期間"


def _observed_context_payload(config: SimulationConfig) -> dict[str, Any]:
    context = config.observed_context
    if not context.enabled:
        return {"enabled": False}
    reservoir_start = (
        date.fromisoformat(context.reservoir_reference_start_date)
        if context.reservoir_reference_start_date
        else None
    )
    points = []
    for point in context.reservoir_points:
        point_date = date.fromisoformat(point.date)
        point_day = (point_date - reservoir_start).days + 1 if reservoir_start else 0
        # The post-period point is retained in the source CSV to bracket the
        # final interpolation, but it must not be drawn as an in-period
        # observation (for example, Sep 11 on the Sep 10 endpoint).
        if 1 <= point_day <= config.days:
            points.append(
                {
                    "day": point_day,
                    "date": point.date,
                    "storageL": round(point.storage_l),
                    "capacityL": round(point.capacity_l),
                    "publishedStorageFraction": point.published_storage_fraction,
                }
            )
    return {
        "enabled": True,
        "precipitation": {
            "station": context.precipitation_station,
            "referenceStartDate": context.precipitation_reference_start_date,
            "sourceUrl": context.precipitation_source_url,
            "dataFile": context.precipitation_data_file,
            "sha256": context.precipitation_sha256,
            "usedAsInflow": context.precipitation_used_as_inflow,
            "totalMm": round(sum(context.daily_precipitation_mm), 1),
        },
        "reservoir": {
            "name": context.reservoir_name,
            "referenceStartDate": context.reservoir_reference_start_date,
            "sourceUrl": context.reservoir_source_url,
            "dataFile": context.reservoir_data_file,
            "sha256": context.reservoir_sha256,
            "interpolation": context.reservoir_interpolation,
            "usedForAllocation": context.reservoir_used_for_allocation,
            "points": points,
        },
    }


def _run_rows(config: SimulationConfig) -> list[dict[str, Any]]:
    storage = config.initial_storage_l
    regional_source_storage = (
        config.regional_source.initial_storage_l if config.regional_source.enabled else None
    )
    onsite_storage = {
        facility.name: facility.onsite_potable_initial_storage_l
        for facility in config.facilities
    }
    cumulative_shortage = 0.0
    cumulative_regional_dc_consumptive = 0.0
    rows: list[dict[str, Any]] = []
    for day_index in range(config.days):
        row = simulate_day(
            config,
            day_index,
            storage,
            1.0,
            onsite_storage,
            regional_source_storage,
        )
        cumulative_shortage += float(row["resident_shortage_l"])
        cumulative_regional_dc_consumptive += float(
            row["regional_source_incremental_dc_consumptive_use_l"]
        )
        row["cumulative_resident_shortage_l"] = cumulative_shortage
        row["cumulative_regional_source_incremental_dc_consumptive_use_l"] = (
            cumulative_regional_dc_consumptive
        )
        observed_reference = float(
            row["observed_reservoir_reference_storage_l"]
        )
        row["observed_reservoir_counterfactual_with_dc_l"] = max(
            0.0, observed_reference - cumulative_regional_dc_consumptive
        )
        row["observed_reservoir_counterfactual_delta_l"] = (
            row["observed_reservoir_counterfactual_with_dc_l"]
            - observed_reference
        )
        rows.append(row)
        storage = float(row["storage_end_l"])
        if config.regional_source.enabled:
            regional_source_storage = float(row["regional_source_storage_end_l"])
        onsite_storage = {
            str(item["name"]): float(item["onsite_storage_end_l"])
            for item in row["facilities"]
        }
    return rows


def _compact_simulated_scenario(
    *,
    name: str,
    label: str,
    climate: str,
    config: SimulationConfig,
    rows: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the browser payload from rows already computed by Python.

    The browser only selects and formats these values.  It never reimplements
    the water balance, which keeps the interactive view on the same numerical
    path as the JSONL simulation artifacts.
    """

    compact_rows = []
    for index, row in enumerate(rows):
        day_number = int(row["day"])
        active = [item for item in config.facilities if day_number >= item.start_day]
        installed_mw = sum(item.it_load_mw for item in active)
        energy = float(row["datacenter_it_energy_kwh"])
        effective_mw = energy / 24_000.0
        direct = float(row["datacenter_direct_water_requirement_l"])
        effective_wue = direct / energy if energy else 0.0
        active_weight = sum(item.it_load_mw * item.utilization for item in active)
        reclaimed_share = (
            sum(
                item.it_load_mw * item.utilization * item.reclaimed_water_share
                for item in active
            )
            / active_weight
            if active_weight
            else 0.0
        )
        additional_shortage = float(row["cumulative_resident_shortage_l"]) - float(
            baseline[index]["cumulative_resident_shortage_l"]
        )
        compact_rows.append(
            [
                day_number,
                round(float(row["storage_end_l"])),
                round(float(row["resident_supply_l"])),
                round(float(row["resident_shortage_l"])),
                round(float(row["cumulative_resident_shortage_l"])),
                round(float(row["datacenter_potable_withdrawal_l"])),
                round(float(row["datacenter_reclaimed_withdrawal_l"])),
                round(float(row["datacenter_evaporation_l"])),
                round(float(row["datacenter_blowdown_l"])),
                round(float(row["datacenter_regional_return_l"])),
                round(float(row["datacenter_recoverable_wastewater_l"])),
                round(float(row["source_inflow_l"])),
                max(0, round(additional_shortage)),
                len(active),
                round(installed_mw, 2),
                round(effective_mw, 2),
                round(effective_wue, 3),
                round(reclaimed_share, 4),
                round(float(row["storage_capacity_l"])),
                round(float(row["datacenter_onsite_storage_end_l"])),
                round(float(row["datacenter_onsite_storage_capacity_l"])),
                round(float(row["reservoir_commissioning_fill_l"])),
                round(float(row["regional_source_storage_end_l"])),
                round(float(row["regional_source_capacity_l"])),
                round(
                    float(
                        row[
                            "cumulative_regional_source_incremental_dc_consumptive_use_l"
                        ]
                    )
                ),
                round(
                    (
                        float(row["regional_source_storage_end_l"])
                        - float(baseline[index]["regional_source_storage_end_l"])
                    )
                    if config.regional_source.enabled
                    else (
                        float(row["storage_end_l"])
                        - float(baseline[index]["storage_end_l"])
                    )
                ),
                round(float(baseline[index]["storage_end_l"])),
                round(float(baseline[index]["storage_capacity_l"])),
                1.0,
                1.0,
                round(float(row["datacenter_water_shortage_l"])),
                False,
                0,
                0,
                0,
                0,
                round(float(row["storage_start_l"])),
                round(float(row["spill_l"])),
                float(row["observed_precipitation_mm"]),
                round(float(row["observed_reservoir_reference_storage_l"])),
                round(float(row["observed_reservoir_reference_capacity_l"])),
                round(
                    float(row["observed_reservoir_reference_storage_fraction"]),
                    4,
                ),
                bool(row["observed_reservoir_reference_interpolated"]),
                round(float(row["observed_reservoir_counterfactual_with_dc_l"])),
                round(float(row["observed_reservoir_counterfactual_delta_l"])),
            ]
        )
    return {
        "id": name,
        "label": label,
        "climate": climate,
        "policy": (
            "住民優先" if config.allocation_policy == "resident_first" else "比例配分"
        ),
        "location": config.location.label_ja,
        "serviceArea": config.location.water_service_area,
        "sourceSystem": config.location.source_system,
        "regionalSourceName": config.regional_source.name or "地域配水バッファ",
        "regionalObservedDate": config.regional_source.observed_date,
        "regionalEvidenceUrl": config.regional_source.evidence_url,
        "startDate": config.start_date,
        "periodName": _period_name(config.start_date, config.days),
        "population": config.population,
        "capacity": round(config.storage_capacity_l),
        "residentDemand": round(
            config.population * config.per_capita_potable_demand_l_per_day
        ),
        "primaryStorageMode": (
            "regional" if config.regional_source.enabled else "local"
        ),
        "agentMode": False,
        "agentProvider": "",
        "agentAudit": "",
        "agentEvents": [],
        "defaultDay": 1,
        "rows": compact_rows,
    }


_DEMO_LOADS_MW = (
    40.0,
    60.0,
    80.0,
    100.0,
    110.0,
    120.0,
    124.6,
    124.7,
    125.0,
    130.0,
    140.0,
    150.0,
    175.0,
    200.0,
    225.0,
    250.0,
    275.0,
    300.0,
)


def _demo_load_config(base: SimulationConfig, load_mw: float) -> SimulationConfig:
    if not base.facilities:
        raise ValueError("game view requires at least one data-center facility template")
    config = base.clone(name=f"demo_single_dc_{load_mw:g}mw_proportional")
    config.agents.enabled = False
    config.allocation_policy = "proportional"
    first = base.facilities[0]
    config.facilities = [
        replace(
            first,
            name="hypothetical_single_campus",
            it_load_mw=load_mw,
            potable_water_share=1.0,
            reclaimed_water_share=0.0,
            onsite_potable_storage_capacity_l=0.0,
            onsite_potable_initial_storage_l=0.0,
            onsite_potable_max_refill_l_per_day=0.0,
        )
    ]
    config.validate()
    return config


def _resident_shortage_threshold_mw(base: SimulationConfig) -> float | None:
    """Find this configuration's first >1 L cumulative-shortage load."""

    if not base.facilities or base.facilities[0].start_day > base.days:
        return None

    def has_shortage(load_mw: float) -> bool:
        return sum(
            float(row["resident_shortage_l"])
            for row in _run_rows(_demo_load_config(base, load_mw))
        ) > 1.0

    low = 0.0
    high = max(500.0, base.comparison_large_campus_it_load_mw)
    while not has_shortage(high):
        high *= 2.0
        if high > 100_000:
            return None
    for _ in range(40):
        middle = (low + high) / 2.0
        if has_shortage(middle):
            high = middle
        else:
            low = middle
    return high


def _demo_metrics(
    rows: list[dict[str, Any]], baseline: list[dict[str, Any]]
) -> dict[str, Any]:
    first_shortage_day = next(
        (int(row["day"]) for row in rows if float(row["resident_shortage_l"]) > 1.0),
        None,
    )
    first_empty_day = next(
        (int(row["day"]) for row in rows if float(row["storage_end_l"]) <= 1.0),
        None,
    )
    resident_supply = sum(float(row["resident_supply_l"]) for row in rows)
    baseline_supply = sum(float(row["resident_supply_l"]) for row in baseline)
    supply_reduction = max(0.0, baseline_supply - resident_supply)
    return {
        "firstResidentShortageDay": first_shortage_day,
        "residentShortageDays": sum(
            float(row["resident_shortage_l"]) > 1.0 for row in rows
        ),
        "firstBufferEmptyDay": first_empty_day,
        "cumulativeResidentDemandL": round(
            sum(
                float(row["resident_supply_l"]) + float(row["resident_shortage_l"])
                for row in rows
            )
        ),
        "cumulativeResidentSupplyL": round(resident_supply),
        "residentSupplyReductionVsNoDcL": round(supply_reduction),
        "residentSupplyReductionPercent": round(
            supply_reduction / baseline_supply * 100.0 if baseline_supply else 0.0,
            3,
        ),
        "cumulativeResidentShortageL": round(
            sum(float(row["resident_shortage_l"]) for row in rows)
        ),
        "cumulativeItEnergyKwh": round(
            sum(float(row["datacenter_it_energy_kwh"]) for row in rows)
        ),
        "cumulativeCoolingRequirementL": round(
            sum(float(row["datacenter_direct_water_requirement_l"]) for row in rows)
        ),
        "cumulativePotableWithdrawalL": round(
            sum(float(row["datacenter_potable_withdrawal_l"]) for row in rows)
        ),
        "cumulativePotableConsumptiveUseL": round(
            sum(float(row["datacenter_potable_consumptive_use_l"]) for row in rows)
        ),
        "cumulativeDatacenterWaterShortageL": round(
            sum(float(row["datacenter_water_shortage_l"]) for row in rows)
        ),
        "finalStorageL": round(float(rows[-1]["storage_end_l"])),
    }


def _build_two_case_demo(
    base: SimulationConfig,
    no_dc_scenario: dict[str, Any],
    baseline_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    threshold = _resident_shortage_threshold_mw(base)
    threshold_tenth = (
        math.ceil(threshold * 10.0) / 10.0 if threshold is not None else None
    )
    max_no_shortage_tenth = (
        math.floor(threshold * 10.0) / 10.0 if threshold is not None else None
    )
    default_load = base.comparison_large_campus_it_load_mw or 250.0
    load_values = sorted(
        {
            *_DEMO_LOADS_MW,
            float(default_load),
            *(() if threshold_tenth is None else (threshold_tenth,)),
            *(
                ()
                if max_no_shortage_tenth is None
                else (max_no_shortage_tenth,)
            ),
        }
    )
    load_scenarios = []
    for load_mw in load_values:
        config = _demo_load_config(base, load_mw)
        rows = _run_rows(config)
        scenario = _compact_simulated_scenario(
            name=config.name,
            label=f"データセンターあり｜設備IT負荷 {load_mw:g} MW",
            climate="normal",
            config=config,
            rows=rows,
            baseline=baseline_rows,
        )
        metrics = _demo_metrics(rows, baseline_rows)
        scenario.update(
            {
                "itLoadMw": load_mw,
                "facilityStartDay": config.facilities[0].start_day,
                "dailyItEnergyKwh": [
                    round(float(row["datacenter_it_energy_kwh"])) for row in rows
                ],
                "dailyCoolingRequirementL": [
                    round(float(row["datacenter_direct_water_requirement_l"]))
                    for row in rows
                ],
                "dailyPotableRequirementL": [
                    round(float(row["datacenter_potable_requirement_l"])) for row in rows
                ],
                "metrics": metrics,
                "defaultDay": metrics["firstResidentShortageDay"]
                or config.facilities[0].start_day,
            }
        )
        load_scenarios.append(scenario)

    no_dc = dict(no_dc_scenario)
    no_dc.update(
        {
            "itLoadMw": 0.0,
            "facilityStartDay": None,
            "dailyItEnergyKwh": [0 for _ in baseline_rows],
            "dailyCoolingRequirementL": [0 for _ in baseline_rows],
            "dailyPotableRequirementL": [0 for _ in baseline_rows],
            "metrics": _demo_metrics(baseline_rows, baseline_rows),
            "defaultDay": load_scenarios[
                min(
                    range(len(load_scenarios)),
                    key=lambda index: abs(
                        load_scenarios[index]["itLoadMw"] - default_load
                    ),
                )
            ]["defaultDay"],
        }
    )
    threshold_scenario = (
        min(
            load_scenarios,
            key=lambda scenario: abs(scenario["itLoadMw"] - threshold_tenth),
        )
        if threshold_tenth is not None
        else None
    )
    first = base.facilities[0]
    return {
        "cases": [
            {"id": "without_dc", "label": "データセンターなし"},
            {
                "id": "with_dc",
                "label": "データセンターあり（IT負荷を変更）",
            },
        ],
        "defaultCase": "with_dc",
        "defaultLoadMw": default_load,
        "noDatacenter": no_dc,
        "loadScenarios": load_scenarios,
        "threshold": {
            "residentShortageStartsAboveL": 1.0,
            "calculatedMw": round(threshold, 4) if threshold is not None else None,
            "displayMw": threshold_tenth,
            "maxNoShortageMw": max_no_shortage_tenth,
            "firstShortageDayAtDisplayMw": (
                threshold_scenario["metrics"]["firstResidentShortageDay"]
                if threshold_scenario is not None
                else None
            ),
        },
        "assumptions": {
            "allocationPolicy": "proportional",
            "facilityCount": 1,
            "facilityStartDay": first.start_day,
            "utilization": first.utilization,
            "wueLPerKwh": first.wue_l_per_kwh,
            "potableWaterShare": 1.0,
            "reclaimedWaterShare": 0.0,
            "initialStorageL": base.initial_storage_l,
            "storageCapacityL": base.storage_capacity_l,
            "days": base.days,
        },
        "calculationAuthority": "python_precomputed",
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


_ENGINE_CONFIG_FIELDS = (
    "start_date",
    "days",
    "population",
    "per_capita_potable_demand_l_per_day",
    "initial_storage_l",
    "storage_capacity_l",
    "source_l_per_day",
    "monthly_demand_multipliers",
    "source_mode",
    "service_headroom_fraction",
    "source_schedule_l",
    "monthly_source_multipliers",
    "source_scenario_multiplier",
    "allocation_policy",
)


def _physical_counterfactual_signature(config: SimulationConfig) -> dict[str, Any]:
    """Return inputs that can change the local agent-on/off water outcome.

    Names, evidence metadata, comparison-only knobs, and the independent regional
    reference layer do not feed the local allocation engine, so they are omitted.
    Facility and reservoir names are also presentation identifiers; ordering and
    every numeric operating input remain part of the signature.
    """

    facilities = []
    for facility in config.facilities:
        values = asdict(facility)
        values.pop("name")
        facilities.append(values)
    additions = []
    for addition in config.reservoir_additions:
        values = asdict(addition)
        values.pop("name")
        additions.append(values)
    return {
        **{name: getattr(config, name) for name in _ENGINE_CONFIG_FIELDS},
        "reservoir_additions": additions,
        "facilities": facilities,
    }


def _agent_config_from_summary(configuration: dict[str, Any]) -> SimulationConfig:
    """Rehydrate the audited configuration without trusting a separate TOML file."""

    from .config import (
        AgentConfig,
        FacilityConfig,
        LocationConfig,
        ObservedContextConfig,
        ObservedReservoirPoint,
        RegionalSourceConfig,
        ReservoirAddition,
    )

    values = dict(configuration)
    try:
        values["location"] = LocationConfig(**values["location"])
        values["regional_source"] = RegionalSourceConfig(**values["regional_source"])
        if "observed_context" in values:
            observed_values = dict(values["observed_context"])
            observed_values["reservoir_points"] = [
                ObservedReservoirPoint(**item)
                for item in observed_values.get("reservoir_points", [])
            ]
            values["observed_context"] = ObservedContextConfig(**observed_values)
        values["reservoir_additions"] = [
            ReservoirAddition(**item) for item in values["reservoir_additions"]
        ]
        values["facilities"] = [FacilityConfig(**item) for item in values["facilities"]]
        values["agents"] = AgentConfig(**values["agents"])
        config = SimulationConfig(**values)
        config.validate()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("agent run contains an invalid serialized configuration") from exc
    return config


def _compact_agent_run(
    agent_run_dir: Path,
    baseline: list[dict[str, Any]],
    agent_off_reference: list[dict[str, Any]],
    agent_off_config: SimulationConfig,
) -> dict[str, Any]:
    rows = _read_jsonl(agent_run_dir / "water_balance.jsonl")
    events = _read_jsonl(agent_run_dir / "llm_messages.jsonl")
    summary = json.loads((agent_run_dir / "summary.json").read_text(encoding="utf-8"))
    configuration = summary["configuration"]
    agent_config = _agent_config_from_summary(configuration)
    if not rows or not events:
        raise ValueError("agent run must contain water rows and agent events")
    if len(rows) != len(baseline) or len(rows) != len(agent_off_reference):
        raise ValueError("agent run and counterfactual rows must cover the same number of days")
    actual_signature = _physical_counterfactual_signature(agent_config)
    expected_signature = _physical_counterfactual_signature(agent_off_config)
    if actual_signature != expected_signature:
        mismatches = [
            name
            for name in actual_signature
            if actual_signature[name] != expected_signature[name]
        ]
        raise ValueError(
            "agent run physical configuration does not match the visible agent-off "
            f"counterfactual: {', '.join(mismatches)}"
        )
    facilities = configuration.get("facilities", [])
    cumulative_consumptive = 0.0
    compact_rows = []
    for index, row in enumerate(rows):
        day_number = int(row["day"])
        active = [item for item in facilities if day_number >= int(item["start_day"])]
        installed_mw = sum(float(item["it_load_mw"]) for item in active)
        energy = float(row["datacenter_it_energy_kwh"])
        direct = float(row["datacenter_direct_water_requirement_l"])
        effective_mw = energy / 24_000.0
        effective_wue = direct / energy if energy else 0.0
        active_weight = sum(
            float(item["it_load_mw"]) * float(item["utilization"]) for item in active
        )
        reclaimed_share = (
            sum(
                float(item["it_load_mw"])
                * float(item["utilization"])
                * float(item["reclaimed_water_share"])
                for item in active
            )
            / active_weight
            if active_weight
            else 0.0
        )
        cumulative_consumptive += float(
            row["regional_source_incremental_dc_consumptive_use_l"]
        )
        observed = agent_config.observed_context.values_for_day(index)
        observed_storage = float(
            row.get(
                "observed_reservoir_reference_storage_l",
                observed["reservoir_storage_l"],
            )
        )
        observed_counterfactual = max(
            0.0, observed_storage - cumulative_consumptive
        )
        baseline_row = baseline[min(index, len(baseline) - 1)]
        reference_row = agent_off_reference[index]
        if row["date"] != baseline_row["date"] or row["date"] != reference_row["date"]:
            raise ValueError("agent run dates do not align with counterfactual rows")
        additional_shortage = float(row["cumulative_resident_shortage_l"]) - float(
            baseline_row["cumulative_resident_shortage_l"]
        )
        compact_rows.append(
            [
                day_number,
                round(float(row["storage_end_l"])),
                round(float(row["resident_supply_l"])),
                round(float(row["resident_shortage_l"])),
                round(float(row["cumulative_resident_shortage_l"])),
                round(float(row["datacenter_potable_withdrawal_l"])),
                round(float(row["datacenter_reclaimed_withdrawal_l"])),
                round(float(row["datacenter_evaporation_l"])),
                round(float(row["datacenter_blowdown_l"])),
                round(float(row["datacenter_regional_return_l"])),
                round(float(row["datacenter_recoverable_wastewater_l"])),
                round(float(row["source_inflow_l"])),
                max(0, round(additional_shortage)),
                len(active),
                round(installed_mw, 2),
                round(effective_mw, 2),
                round(effective_wue, 3),
                round(reclaimed_share, 4),
                round(float(row["storage_capacity_l"])),
                round(float(row["datacenter_onsite_storage_end_l"])),
                round(float(row["datacenter_onsite_storage_capacity_l"])),
                round(float(row["reservoir_commissioning_fill_l"])),
                round(float(row["storage_end_l"])),
                round(float(row["storage_capacity_l"])),
                round(cumulative_consumptive),
                round(
                    float(row["storage_end_l"])
                    - float(baseline_row["storage_end_l"])
                ),
                round(float(baseline_row["storage_end_l"])),
                round(float(baseline_row["storage_capacity_l"])),
                round(float(row["datacenter_potable_restriction_multiplier"]), 4),
                round(
                    float(
                        row.get(
                            "datacenter_potable_restriction_multiplier_next_day",
                            row["datacenter_potable_restriction_multiplier"],
                        )
                    ),
                    4,
                ),
                round(float(row["datacenter_water_shortage_l"])),
                bool(row.get("agent_council_convened")),
                int(row.get("agent_decision_round") or 0),
                round(float(row["storage_end_l"]) - float(reference_row["storage_end_l"])),
                round(
                    float(row["datacenter_potable_withdrawal_l"])
                    - float(reference_row["datacenter_potable_withdrawal_l"])
                ),
                round(
                    float(row["datacenter_water_shortage_l"])
                    - float(reference_row["datacenter_water_shortage_l"])
                ),
                round(float(row["storage_start_l"])),
                round(float(row["spill_l"])),
                float(
                    row.get(
                        "observed_precipitation_mm",
                        observed["precipitation_mm"],
                    )
                ),
                round(observed_storage),
                round(
                    float(
                        row.get(
                            "observed_reservoir_reference_capacity_l",
                            observed["reservoir_capacity_l"],
                        )
                    )
                ),
                round(
                    float(
                        row.get(
                            "observed_reservoir_reference_storage_fraction",
                            observed["reservoir_storage_fraction"],
                        )
                    ),
                    4,
                ),
                bool(
                    row.get(
                        "observed_reservoir_reference_interpolated",
                        observed["reservoir_interpolated"],
                    )
                ),
                round(
                    float(
                        row.get(
                            "observed_reservoir_counterfactual_with_dc_l",
                            observed_counterfactual,
                        )
                    )
                ),
                round(
                    float(
                        row.get(
                            "observed_reservoir_counterfactual_delta_l",
                            observed_counterfactual - observed_storage,
                        )
                    )
                ),
            ]
        )

    grouped_events: dict[int, dict[str, Any]] = {}
    legacy_long_response_count = 0
    normalized_count = 0
    fatal_count = 0
    for event in events:
        day = int(event["day"])
        group = grouped_events.setdefault(
            day,
            {
                "day": day,
                "date": event["date"],
                "round": int(event.get("decision_round", 0)),
                "reason": event.get("decision_reason", "scheduled_review"),
                "decisions": [],
            },
        )
        role = event["role"]
        parsed_response = event["parsed_response"]
        accepted = bool(event["valid"])
        normalizations = list(event.get("normalizations", []))
        legacy_long_response = False
        if not accepted and event.get("raw_response"):
            reparsed, revalidation_errors, revalidations = parse_decision_response(
                event["raw_response"], role.get("allowed_actions", [])
            )
            finish_reason = event.get("provider_metadata", {}).get("finish_reason")
            if finish_reason not in (None, "stop"):
                revalidation_errors.append(
                    f"provider finish_reason was {finish_reason!r}, not 'stop'"
                )
            if not revalidation_errors and revalidations:
                # Preserve the historical event outcome.  The legacy run fed a
                # no_action fallback to later agents, so this is presentation
                # context only and must never be counted as an accepted action.
                parsed_response = reparsed
                normalizations = revalidations
                legacy_long_response = True
                legacy_long_response_count += 1
            else:
                fatal_count += 1
        elif accepted and normalizations:
            normalized_count += 1
        group["decisions"].append(
            {
                "role": role["role"],
                "label": role["label_ja"],
                "phase": role.get(
                    "decision_phase",
                    "historical_sequence",
                ),
                "action": parsed_response["action"],
                "message": parsed_response["message"],
                "reason": parsed_response["reason"],
                "valid": accepted,
                "strictValid": bool(event.get("strict_valid", accepted))
                and not normalizations,
                "normalized": bool(accepted and normalizations),
                "legacyLongResponse": legacy_long_response,
                "normalizations": normalizations,
            }
        )

    provider_metadata = events[0].get("provider_metadata", {})
    provider_type = provider_metadata.get("provider_type")
    is_mock = provider_type == "mock" or bool(provider_metadata.get("mock"))
    is_ds4 = provider_type == "ds4"
    fallback_used = bool(provider_metadata.get("fallback_used"))
    ds4_not_configured = (
        provider_metadata.get("selection_reason") == "ds4_not_configured"
    )
    legacy_ds4 = (
        provider_type is None
        and provider_metadata.get("model") == "deepseek-v4-flash"
        and "finish_reason" in provider_metadata
    )
    is_historical_sequence = any(
        "decision_phase" not in event.get("role", {}) for event in events
    )
    if fallback_used:
        model = "MockProvider（DS4接続失敗から自動切替）"
    elif ds4_not_configured:
        model = "MockProvider（DS4未設定）"
    else:
        model = "MockProvider" if is_mock else provider_metadata.get("model", "provider未確認")
    valid_count = sum(bool(event["valid"]) for event in events)
    if legacy_long_response_count:
        agent_audit = (
            f"{valid_count} STRICT · {legacy_long_response_count} LEGACY LONG / RAW SAVED"
        )
    elif normalized_count:
        agent_audit = (
            f"{valid_count} / {len(events)} ACCEPTED · {normalized_count} TEXT NORMALIZED"
        )
    elif fatal_count:
        agent_audit = f"{valid_count} / {len(events)} ACCEPTED · {fatal_count} HELD"
    else:
        agent_audit = f"{valid_count} / {len(events)} JSON VALID"
    location = configuration.get("location", {})
    policy_changes = summary.get("agent_policy_changes", [])
    default_day = int(policy_changes[0]["day"]) if policy_changes else int(events[0]["day"])
    return {
        "id": "agent_decision_season",
        "label": (
            (
                "DS4接続失敗→再現モック｜4者会議・250MWストレス"
                if fallback_used
                else (
                    "DS4未設定→再現モック｜4者会議・250MWストレス"
                    if ds4_not_configured
                    else "夏季〜初秋モック｜4者会議・250MWストレス"
                )
            )
            if is_mock
            else (
                (
                    "実DS4｜4者会議・250MWストレス（初回監査）"
                    if is_historical_sequence
                    else "実DS4｜4者会議・250MWストレス（改善後監査）"
                )
                if is_ds4
                else (
                    (
                        "旧実DS4｜4者会議・250MWストレス（初回監査・識別子導入前）"
                        if is_historical_sequence
                        else "旧実DS4｜4者会議・250MWストレス（改善後監査・識別子導入前）"
                    )
                    if legacy_ds4
                    else "外部LLM｜4者会議・250MWストレス（provider未確認）"
                )
            )
        ),
        "climate": "normal",
        "policy": "住民優先＋エージェント判断",
        "location": location.get("label_ja", "合成地域"),
        "serviceArea": location.get("water_service_area", "合成配水区域"),
        "sourceSystem": location.get("source_system", "合成水源"),
        "regionalSourceName": "地域配水バッファ",
        "regionalObservedDate": "",
        "regionalEvidenceUrl": "",
        "startDate": configuration["start_date"],
        "periodName": _period_name(
            str(configuration["start_date"]), int(configuration["days"])
        ),
        "population": int(configuration["population"]),
        "capacity": round(float(configuration["storage_capacity_l"])),
        "residentDemand": round(
            float(configuration["population"])
            * float(configuration["per_capita_potable_demand_l_per_day"])
        ),
        "primaryStorageMode": "local",
        "agentMode": True,
        "agentProvider": model,
        "agentAudit": agent_audit,
        "agentEvents": list(grouped_events.values()),
        "defaultDay": default_day,
        "rows": compact_rows,
    }


def _compact_dataset(
    base: SimulationConfig,
    agent_run_dir: str | Path | None = None,
) -> dict[str, Any]:
    available = {
        name: (climate, config)
        for name, climate, config in build_comparison_scenarios(base)
        if name in _ORDER
    }
    ordered = [name for name in _ORDER if name in available]
    simulated = {name: _run_rows(available[name][1]) for name in ordered}
    normal_baseline = simulated["normal_no_datacenter"]

    scenarios = []
    for name in ordered:
        climate, config = available[name]
        rows = simulated[name]
        baseline = normal_baseline
        if climate == "drought":
            drought_no_dc = next(
                item for item in build_comparison_scenarios(base) if item[0] == "drought_no_datacenter"
            )[2]
            baseline = _run_rows(drought_no_dc)
        scenarios.append(
            _compact_simulated_scenario(
                name=name,
                label=_LABELS[name],
                climate=climate,
                config=config,
                rows=rows,
                baseline=baseline,
            )
        )
    default_scenario = "normal_dc_no_reclaimed_resident_first"
    if agent_run_dir is not None:
        agent_path = Path(agent_run_dir)
        required = {
            agent_path / "water_balance.jsonl",
            agent_path / "llm_messages.jsonl",
            agent_path / "summary.json",
        }
        if not all(path.is_file() for path in required):
            missing = ", ".join(str(path) for path in sorted(required) if not path.is_file())
            raise FileNotFoundError(f"agent run is incomplete: {missing}")
        agent_off_reference = simulated.get("normal_dc_large_campus_resident_first")
        agent_off_entry = available.get("normal_dc_large_campus_resident_first")
        if agent_off_reference is None or agent_off_entry is None:
            raise ValueError("agent game requires the 250 MW resident-first counterfactual")
        scenarios.insert(
            0,
            _compact_agent_run(
                agent_path,
                normal_baseline,
                agent_off_reference,
                agent_off_entry[1],
            ),
        )
        default_scenario = "agent_decision_season"
    no_dc_scenario = next(
        scenario for scenario in scenarios if scenario["id"] == "normal_no_datacenter"
    )
    return {
        "schemaVersion": 3,
        "rowColumns": [
            "day",
            "storage_end_l",
            "resident_supply_l",
            "resident_shortage_l",
            "cumulative_resident_shortage_l",
            "datacenter_potable_withdrawal_l",
            "datacenter_reclaimed_withdrawal_l",
            "datacenter_evaporation_l",
            "datacenter_blowdown_l",
            "datacenter_regional_return_l",
            "datacenter_recoverable_wastewater_l",
            "source_inflow_l",
            "additional_resident_shortage_l",
            "active_facility_count",
            "installed_it_load_mw",
            "effective_it_load_mw",
            "effective_wue_l_per_kwh",
            "reclaimed_water_share",
            "storage_capacity_l",
            "onsite_storage_end_l",
            "onsite_storage_capacity_l",
            "reservoir_commissioning_fill_l",
            "primary_storage_end_l",
            "primary_storage_capacity_l",
            "cumulative_potable_consumptive_use_l",
            "primary_storage_delta_vs_no_dc_l",
            "baseline_storage_end_l",
            "baseline_storage_capacity_l",
            "restriction_multiplier",
            "next_day_restriction_multiplier",
            "datacenter_water_shortage_l",
            "agent_council_convened",
            "agent_decision_round",
            "storage_delta_vs_agent_off_l",
            "dc_withdrawal_delta_vs_agent_off_l",
            "dc_shortage_delta_vs_agent_off_l",
            "storage_start_l",
            "spill_l",
            "observed_precipitation_mm",
            "observed_reservoir_reference_storage_l",
            "observed_reservoir_reference_capacity_l",
            "observed_reservoir_reference_storage_fraction",
            "observed_reservoir_reference_interpolated",
            "observed_reservoir_counterfactual_with_dc_l",
            "observed_reservoir_counterfactual_delta_l",
        ],
        "observedContext": _observed_context_payload(base),
        "default": default_scenario,
        "scenarios": scenarios,
        "demo": _build_two_case_demo(base, no_dc_scenario, normal_baseline),
    }


def write_game_view(
    config: SimulationConfig,
    output_path: str | Path,
    *,
    fragment: bool = False,
    agent_run_dir: str | Path | None = None,
) -> None:
    template = files("water_negotiation_lab").joinpath("game-template.html").read_text(
        encoding="utf-8"
    )
    data = json.dumps(
        _compact_dataset(config, agent_run_dir),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    content = template.replace("__SIMULATION_DATA__", data)
    if not fragment:
        content = (
            "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Water Grid Simulation</title></head>"
            "<body style=\"margin:0;padding:20px;background:#07111f\">"
            + content
            + "</body></html>"
        )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
