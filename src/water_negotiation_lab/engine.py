from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from .config import FacilityConfig, SimulationConfig


@dataclass(slots=True)
class FacilityDemand:
    name: str
    it_energy_kwh: float
    direct_water_requirement_l: float
    potable_requirement_l: float
    reclaimed_requirement_l: float
    onsite_storage_start_l: float
    onsite_discharge_l: float
    operational_utility_requirement_l: float
    onsite_refill_requirement_l: float
    allowed_potable_requirement_l: float


def facility_demand(
    facility: FacilityConfig,
    day_number: int,
    potable_restriction_multiplier: float,
    onsite_storage_start_l: float | None = None,
) -> FacilityDemand:
    active = day_number >= facility.start_day
    energy = facility.it_load_mw * 1000.0 * 24.0 * facility.utilization if active else 0.0
    direct = energy * facility.wue_l_per_kwh
    potable = direct * facility.potable_water_share
    reclaimed = direct * facility.reclaimed_water_share
    tank_start = (
        facility.onsite_potable_initial_storage_l
        if onsite_storage_start_l is None
        else onsite_storage_start_l
    )
    tank_discharge = min(tank_start, potable)
    operational_utility = max(0.0, potable - tank_discharge)
    tank_after_discharge = tank_start - tank_discharge
    target = (
        facility.onsite_potable_storage_capacity_l
        * facility.onsite_potable_target_fraction
    )
    refill_requirement = (
        min(
            max(0.0, target - tank_after_discharge),
            facility.onsite_potable_max_refill_l_per_day,
        )
        if active
        else 0.0
    )
    return FacilityDemand(
        name=facility.name,
        it_energy_kwh=energy,
        direct_water_requirement_l=direct,
        potable_requirement_l=potable,
        reclaimed_requirement_l=reclaimed,
        onsite_storage_start_l=tank_start,
        onsite_discharge_l=tank_discharge,
        operational_utility_requirement_l=operational_utility,
        onsite_refill_requirement_l=refill_requirement,
        allowed_potable_requirement_l=(operational_utility + refill_requirement)
        * potable_restriction_multiplier,
    )


def allocate_water(
    available_l: float,
    resident_demand_l: float,
    datacenter_allowed_potable_l: float,
    policy: str,
) -> tuple[float, float]:
    if policy == "resident_first":
        resident_supply = min(resident_demand_l, available_l)
        dc_supply = min(datacenter_allowed_potable_l, available_l - resident_supply)
        return resident_supply, dc_supply
    if policy == "proportional":
        total_demand = resident_demand_l + datacenter_allowed_potable_l
        if total_demand <= 0:
            return 0.0, 0.0
        fraction = min(1.0, available_l / total_demand)
        return resident_demand_l * fraction, datacenter_allowed_potable_l * fraction
    raise ValueError(f"unsupported allocation policy: {policy}")


def _distribute(total: float, weights: list[float]) -> list[float]:
    weight_total = sum(weights)
    if weight_total <= 0:
        return [0.0 for _ in weights]
    return [total * weight / weight_total for weight in weights]


def simulate_day(
    config: SimulationConfig,
    day_index: int,
    storage_start_l: float,
    potable_restriction_multiplier: float = 1.0,
    onsite_storage_start_l: dict[str, float] | None = None,
    regional_source_storage_start_l: float | None = None,
) -> dict[str, Any]:
    day_number = day_index + 1
    current_date = date.fromisoformat(config.start_date) + timedelta(days=day_index)
    observed_context = config.observed_context.values_for_day(day_index)
    observed_reference_date = (
        date.fromisoformat(config.observed_context.precipitation_reference_start_date)
        + timedelta(days=day_index)
    ).isoformat() if config.observed_context.daily_precipitation_mm else ""
    resident_demand = (
        config.population
        * config.per_capita_potable_demand_l_per_day
        * config.monthly_demand_multipliers[current_date.month - 1]
    )
    if config.source_mode == "calibrated_service":
        # The observed system-wide average demand is the baseline.  This mode
        # applies an explicitly configured service headroom before the climate
        # multiplier, so the no-DC counterfactual is not accidentally forced
        # into deficit by an unrelated synthetic inflow series.
        base_source = resident_demand * (1.0 + config.service_headroom_fraction)
    elif config.source_schedule_l:
        base_source = config.source_schedule_l[day_index]
    else:
        base_source = config.source_l_per_day
    source_inflow = (
        base_source
        * config.monthly_source_multipliers[current_date.month - 1]
        * config.source_scenario_multiplier
    )
    active_additions = [
        item for item in config.reservoir_additions if item.commission_day <= day_number
    ]
    storage_capacity = config.storage_capacity_l + sum(
        item.added_capacity_l for item in active_additions
    )
    commissioning_fill = sum(
        item.commissioning_fill_l
        for item in config.reservoir_additions
        if item.commission_day == day_number
    )
    # The daily step uses a start-of-day inflow pulse.  Capacity is applied to
    # that pulse before the day's withdrawals; see docs/model-boundaries.md.
    before_capacity_limit = storage_start_l + source_inflow + commissioning_fill
    spill = max(0.0, before_capacity_limit - storage_capacity)
    available = min(storage_capacity, before_capacity_limit)

    demands = [
        facility_demand(
            facility,
            day_number,
            potable_restriction_multiplier,
            (onsite_storage_start_l or {}).get(
                facility.name, facility.onsite_potable_initial_storage_l
            ),
        )
        for facility in config.facilities
    ]
    dc_allowed_potable = sum(item.allowed_potable_requirement_l for item in demands)
    resident_supply, dc_potable_withdrawal = allocate_water(
        available, resident_demand, dc_allowed_potable, config.allocation_policy
    )
    storage_end = available - resident_supply - dc_potable_withdrawal

    per_facility_potable = _distribute(
        dc_potable_withdrawal, [item.allowed_potable_requirement_l for item in demands]
    )
    facility_rows: list[dict[str, float | str]] = []
    fate_totals = {
        "evaporation_l": 0.0,
        "blowdown_l": 0.0,
        "regional_return_l": 0.0,
        "recoverable_wastewater_l": 0.0,
        "potable_consumptive_use_l": 0.0,
    }
    reclaimed_withdrawal = 0.0
    direct_requirement = 0.0
    potable_requirement = 0.0
    it_energy = 0.0
    potable_to_process = 0.0
    onsite_storage_start_total = 0.0
    onsite_storage_end_total = 0.0
    onsite_storage_capacity_total = 0.0
    onsite_discharge_total = 0.0
    onsite_refill_total = 0.0
    onsite_balance_error_total = 0.0

    for facility, demand, potable_supplied in zip(config.facilities, demands, per_facility_potable):
        reclaimed_supplied = demand.reclaimed_requirement_l
        allowed_operational = (
            demand.operational_utility_requirement_l * potable_restriction_multiplier
        )
        potable_from_utility_to_process = min(potable_supplied, allowed_operational)
        tank_after_discharge = demand.onsite_storage_start_l - demand.onsite_discharge_l
        tank_refill = min(
            max(0.0, potable_supplied - potable_from_utility_to_process),
            facility.onsite_potable_storage_capacity_l - tank_after_discharge,
        )
        tank_end = tank_after_discharge + tank_refill
        process_potable = demand.onsite_discharge_l + potable_from_utility_to_process
        supplied_total = process_potable + reclaimed_supplied
        evaporation = supplied_total * facility.evaporation_share
        blowdown = supplied_total * facility.blowdown_share
        regional_return = supplied_total * facility.regional_return_share
        recoverable = supplied_total * facility.recoverable_wastewater_share
        potable_fraction = process_potable / supplied_total if supplied_total > 0 else 0.0
        potable_consumptive = evaporation * potable_fraction
        facility_shortage = max(0.0, demand.direct_water_requirement_l - supplied_total)
        onsite_balance_error = (
            demand.onsite_storage_start_l
            + tank_refill
            - demand.onsite_discharge_l
            - tank_end
        )
        facility_rows.append(
            {
                **asdict(demand),
                "potable_withdrawal_l": potable_supplied,
                "potable_to_process_l": process_potable,
                "reclaimed_withdrawal_l": reclaimed_supplied,
                "water_shortage_l": facility_shortage,
                "evaporation_l": evaporation,
                "blowdown_l": blowdown,
                "regional_return_l": regional_return,
                "recoverable_wastewater_l": recoverable,
                "potable_consumptive_use_l": potable_consumptive,
                "onsite_storage_capacity_l": facility.onsite_potable_storage_capacity_l,
                "onsite_storage_refill_l": tank_refill,
                "onsite_storage_end_l": tank_end,
                "onsite_storage_balance_error_l": onsite_balance_error,
            }
        )
        reclaimed_withdrawal += reclaimed_supplied
        direct_requirement += demand.direct_water_requirement_l
        potable_requirement += demand.potable_requirement_l
        it_energy += demand.it_energy_kwh
        potable_to_process += process_potable
        onsite_storage_start_total += demand.onsite_storage_start_l
        onsite_storage_end_total += tank_end
        onsite_storage_capacity_total += facility.onsite_potable_storage_capacity_l
        onsite_discharge_total += demand.onsite_discharge_l
        onsite_refill_total += tank_refill
        onsite_balance_error_total += onsite_balance_error
        fate_totals["evaporation_l"] += evaporation
        fate_totals["blowdown_l"] += blowdown
        fate_totals["regional_return_l"] += regional_return
        fate_totals["recoverable_wastewater_l"] += recoverable
        fate_totals["potable_consumptive_use_l"] += potable_consumptive

    dc_total_supplied = potable_to_process + reclaimed_withdrawal
    dc_water_shortage = max(0.0, direct_requirement - dc_total_supplied)
    resident_shortage = max(0.0, resident_demand - resident_supply)
    balance_error = (
        storage_start_l
        + source_inflow
        + commissioning_fill
        - spill
        - resident_supply
        - dc_potable_withdrawal
        - storage_end
    )

    regional_source_start = 0.0
    regional_reference_net_change = 0.0
    regional_source_spill = 0.0
    regional_source_deficit = 0.0
    regional_source_end = 0.0
    regional_balance_error = 0.0
    if config.regional_source.enabled:
        regional_source_start = (
            config.regional_source.initial_storage_l
            if regional_source_storage_start_l is None
            else regional_source_storage_start_l
        )
        regional_reference_net_change = config.regional_source.reference_daily_net_change_l
        regional_unbounded = (
            regional_source_start
            + regional_reference_net_change
            - fate_totals["potable_consumptive_use_l"]
        )
        regional_source_spill = max(
            0.0, regional_unbounded - config.regional_source.capacity_l
        )
        regional_source_deficit = max(0.0, -regional_unbounded)
        regional_source_end = min(
            config.regional_source.capacity_l, max(0.0, regional_unbounded)
        )
        regional_balance_error = (
            regional_source_start
            + regional_reference_net_change
            - fate_totals["potable_consumptive_use_l"]
            - regional_source_spill
            + regional_source_deficit
            - regional_source_end
        )

    return {
        "schema_version": 1,
        "scenario": config.name,
        "day": day_number,
        "date": current_date.isoformat(),
        "allocation_policy": config.allocation_policy,
        "location_profile_id": config.location.profile_id,
        "location_label_ja": config.location.label_ja,
        "water_service_area": config.location.water_service_area,
        "source_system": config.location.source_system,
        "observed_context_reference_date": observed_reference_date,
        "observed_precipitation_mm": observed_context["precipitation_mm"],
        "observed_precipitation_station": (
            config.observed_context.precipitation_station
        ),
        "observed_precipitation_used_as_inflow": (
            config.observed_context.precipitation_used_as_inflow
        ),
        "observed_reservoir_name": config.observed_context.reservoir_name,
        "observed_reservoir_reference_storage_l": observed_context[
            "reservoir_storage_l"
        ],
        "observed_reservoir_reference_capacity_l": observed_context[
            "reservoir_capacity_l"
        ],
        "observed_reservoir_reference_storage_fraction": observed_context[
            "reservoir_storage_fraction"
        ],
        "observed_reservoir_reference_interpolated": observed_context[
            "reservoir_interpolated"
        ],
        "observed_reservoir_used_for_allocation": (
            config.observed_context.reservoir_used_for_allocation
        ),
        "datacenter_potable_restriction_multiplier": potable_restriction_multiplier,
        "storage_start_l": storage_start_l,
        "community_demand_multiplier": config.monthly_demand_multipliers[
            current_date.month - 1
        ],
        "source_inflow_l": source_inflow,
        "reservoir_commissioning_fill_l": commissioning_fill,
        "active_reservoir_additions": [asdict(item) for item in active_additions],
        "spill_l": spill,
        "water_available_l": available,
        "storage_capacity_l": storage_capacity,
        "resident_demand_l": resident_demand,
        "resident_supply_l": resident_supply,
        "resident_shortage_l": resident_shortage,
        "datacenter_it_energy_kwh": it_energy,
        "datacenter_direct_water_requirement_l": direct_requirement,
        "datacenter_potable_requirement_l": potable_requirement,
        "datacenter_allowed_potable_requirement_l": dc_allowed_potable,
        "datacenter_potable_withdrawal_l": dc_potable_withdrawal,
        "datacenter_potable_to_process_l": potable_to_process,
        "datacenter_reclaimed_withdrawal_l": reclaimed_withdrawal,
        "datacenter_water_shortage_l": dc_water_shortage,
        "datacenter_evaporation_l": fate_totals["evaporation_l"],
        "datacenter_blowdown_l": fate_totals["blowdown_l"],
        "datacenter_regional_return_l": fate_totals["regional_return_l"],
        "datacenter_recoverable_wastewater_l": fate_totals["recoverable_wastewater_l"],
        "datacenter_potable_consumptive_use_l": fate_totals[
            "potable_consumptive_use_l"
        ],
        "datacenter_onsite_storage_start_l": onsite_storage_start_total,
        "datacenter_onsite_storage_refill_l": onsite_refill_total,
        "datacenter_onsite_storage_discharge_l": onsite_discharge_total,
        "datacenter_onsite_storage_end_l": onsite_storage_end_total,
        "datacenter_onsite_storage_capacity_l": onsite_storage_capacity_total,
        "datacenter_onsite_storage_balance_error_l": onsite_balance_error_total,
        "regional_source_name": config.regional_source.name,
        "regional_source_observed_date": config.regional_source.observed_date,
        "regional_source_storage_start_l": regional_source_start,
        "regional_source_reference_net_change_l": regional_reference_net_change,
        "regional_source_incremental_dc_consumptive_use_l": fate_totals[
            "potable_consumptive_use_l"
        ],
        "regional_source_spill_l": regional_source_spill,
        "regional_source_deficit_l": regional_source_deficit,
        "regional_source_storage_end_l": regional_source_end,
        "regional_source_capacity_l": config.regional_source.capacity_l,
        "regional_source_balance_error_l": regional_balance_error,
        "storage_end_l": storage_end,
        "water_balance_error_l": balance_error,
        "facilities": facility_rows,
    }
