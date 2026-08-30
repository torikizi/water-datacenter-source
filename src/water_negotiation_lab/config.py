from __future__ import annotations

import copy
import csv
import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any


_EPSILON = 1e-9


@dataclass(slots=True)
class FacilityConfig:
    name: str
    start_day: int
    it_load_mw: float
    utilization: float
    wue_l_per_kwh: float
    potable_water_share: float = 1.0
    reclaimed_water_share: float = 0.0
    evaporation_share: float = 0.70
    blowdown_share: float = 0.20
    regional_return_share: float = 0.05
    recoverable_wastewater_share: float = 0.05
    onsite_potable_storage_capacity_l: float = 0.0
    onsite_potable_initial_storage_l: float = 0.0
    onsite_potable_target_fraction: float = 1.0
    onsite_potable_max_refill_l_per_day: float = 0.0

    def validate(self) -> None:
        if not self.name:
            raise ValueError("facility name must not be empty")
        if self.start_day < 1:
            raise ValueError(f"{self.name}: start_day must be >= 1")
        if self.it_load_mw < 0 or self.wue_l_per_kwh < 0:
            raise ValueError(f"{self.name}: load and WUE must be non-negative")
        if not 0 <= self.utilization <= 1:
            raise ValueError(f"{self.name}: utilization must be between 0 and 1")
        if not 0 <= self.potable_water_share <= 1:
            raise ValueError(f"{self.name}: potable water share must be between 0 and 1")
        if not 0 <= self.reclaimed_water_share <= 1:
            raise ValueError(f"{self.name}: reclaimed water share must be between 0 and 1")
        source_total = self.potable_water_share + self.reclaimed_water_share
        if abs(source_total - 1.0) > _EPSILON:
            raise ValueError(f"{self.name}: potable and reclaimed shares must sum to 1")
        fate_shares = (
            self.evaporation_share,
            self.blowdown_share,
            self.regional_return_share,
            self.recoverable_wastewater_share,
        )
        if any(value < 0 for value in fate_shares):
            raise ValueError(f"{self.name}: water fate shares must be non-negative")
        if abs(sum(fate_shares) - 1.0) > _EPSILON:
            raise ValueError(f"{self.name}: water fate shares must sum to 1")
        if self.onsite_potable_storage_capacity_l < 0:
            raise ValueError(f"{self.name}: onsite storage capacity must be non-negative")
        if not 0 <= self.onsite_potable_initial_storage_l <= self.onsite_potable_storage_capacity_l:
            raise ValueError(f"{self.name}: onsite initial storage must be within capacity")
        if not 0 <= self.onsite_potable_target_fraction <= 1:
            raise ValueError(f"{self.name}: onsite storage target must be between 0 and 1")
        if self.onsite_potable_max_refill_l_per_day < 0:
            raise ValueError(f"{self.name}: onsite maximum refill must be non-negative")


@dataclass(slots=True)
class LocationConfig:
    profile_id: str = "synthetic_region"
    label_ja: str = "合成地域"
    study_area: str = "特定の実地域を表さない"
    water_service_area: str = "合成水道区域"
    source_system: str = "合成水源"
    evidence_scope: str = "simulation_assumption"

    def validate(self) -> None:
        if not self.profile_id or not self.label_ja:
            raise ValueError("location profile_id and label_ja must not be empty")
        if self.evidence_scope not in {
            "simulation_assumption",
            "real_context_synthetic_inputs",
            "mixed_observed_and_synthetic",
        }:
            raise ValueError("location.evidence_scope is not supported")


@dataclass(slots=True)
class RegionalSourceConfig:
    """Observed wide-area source stock used as a counterfactual reference layer.

    Existing regional inflows and withdrawals are embedded in
    ``reference_daily_net_change_l``. Only incremental potable consumptive use
    from simulated data centers is subtracted again.
    """

    name: str = ""
    observed_date: str = ""
    initial_storage_l: float = 0.0
    capacity_l: float = 0.0
    reference_daily_net_change_l: float = 0.0
    evidence_url: str = ""

    @property
    def enabled(self) -> bool:
        return self.capacity_l > 0

    def validate(self) -> None:
        if self.capacity_l < 0 or self.initial_storage_l < 0:
            raise ValueError("regional source storage values must be non-negative")
        if self.enabled:
            if not self.name or not self.observed_date or not self.evidence_url:
                raise ValueError("enabled regional source requires name, date, and evidence URL")
            date.fromisoformat(self.observed_date)
            if self.initial_storage_l > self.capacity_l:
                raise ValueError("regional source initial storage must not exceed capacity")


@dataclass(slots=True)
class ObservedReservoirPoint:
    date: str
    storage_l: float
    capacity_l: float
    published_storage_fraction: float

    def validate(self) -> None:
        date.fromisoformat(self.date)
        if self.storage_l < 0 or self.capacity_l <= 0:
            raise ValueError("observed reservoir storage must be non-negative and capacity positive")
        if self.storage_l > self.capacity_l:
            raise ValueError("observed reservoir storage must not exceed capacity")
        if not 0 <= self.published_storage_fraction <= 1:
            raise ValueError(
                "observed reservoir storage fraction must be between zero and one"
            )


@dataclass(slots=True)
class ObservedContextConfig:
    """Dated public observations kept outside the allocation water balance.

    The rainfall series is local weather context, not a potable-water inflow.
    The reservoir series is a wide-area reference stock, not an Inzai-only
    distribution reservoir.  Both flags make that boundary machine-readable.
    """

    precipitation_station: str = ""
    precipitation_reference_start_date: str = ""
    precipitation_source_url: str = ""
    precipitation_data_file: str = ""
    precipitation_sha256: str = ""
    precipitation_used_as_inflow: bool = False
    daily_precipitation_mm: list[float] = field(default_factory=list)
    reservoir_name: str = ""
    reservoir_reference_start_date: str = ""
    reservoir_source_url: str = ""
    reservoir_data_file: str = ""
    reservoir_sha256: str = ""
    reservoir_interpolation: str = "none"
    reservoir_used_for_allocation: bool = False
    reservoir_points: list[ObservedReservoirPoint] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.daily_precipitation_mm or self.reservoir_points)

    def validate(self, simulation_days: int) -> None:
        if self.precipitation_used_as_inflow:
            raise ValueError(
                "observed precipitation is context-only; a hydrologic conversion is not implemented"
            )
        if self.reservoir_used_for_allocation:
            raise ValueError(
                "observed wide-area reservoir stock cannot be used as local allocatable water"
            )
        if self.daily_precipitation_mm:
            if len(self.daily_precipitation_mm) != simulation_days:
                raise ValueError("observed precipitation must cover every simulated day")
            if any(value < 0 for value in self.daily_precipitation_mm):
                raise ValueError("observed precipitation values must be non-negative")
            if not (
                self.precipitation_station
                and self.precipitation_reference_start_date
                and self.precipitation_source_url
                and self.precipitation_sha256
            ):
                raise ValueError("observed precipitation requires station, date, source, and hash")
            date.fromisoformat(self.precipitation_reference_start_date)
        if self.reservoir_points:
            if not (
                self.reservoir_name
                and self.reservoir_reference_start_date
                and self.reservoir_source_url
                and self.reservoir_sha256
            ):
                raise ValueError("observed reservoir context requires name, date, source, and hash")
            if self.reservoir_interpolation != "linear_between_observations":
                raise ValueError("observed reservoir interpolation must be linear_between_observations")
            for point in self.reservoir_points:
                point.validate()
            point_dates = [date.fromisoformat(point.date) for point in self.reservoir_points]
            if point_dates != sorted(set(point_dates)):
                raise ValueError("observed reservoir dates must be unique and increasing")
            reference_start = date.fromisoformat(self.reservoir_reference_start_date)
            reference_end = reference_start + timedelta(days=simulation_days - 1)
            if point_dates[0] > reference_start or point_dates[-1] < reference_end:
                raise ValueError("observed reservoir points must bracket the full reference period")

    def values_for_day(self, day_index: int) -> dict[str, float | bool]:
        precipitation = (
            self.daily_precipitation_mm[day_index]
            if 0 <= day_index < len(self.daily_precipitation_mm)
            else 0.0
        )
        result: dict[str, float | bool] = {
            "precipitation_mm": precipitation,
            "reservoir_storage_l": 0.0,
            "reservoir_capacity_l": 0.0,
            "reservoir_storage_fraction": 0.0,
            "reservoir_interpolated": False,
        }
        if not self.reservoir_points:
            return result
        target = date.fromisoformat(self.reservoir_reference_start_date) + timedelta(
            days=day_index
        )
        points = self.reservoir_points
        for point in points:
            if date.fromisoformat(point.date) == target:
                result.update(
                    reservoir_storage_l=point.storage_l,
                    reservoir_capacity_l=point.capacity_l,
                    reservoir_storage_fraction=point.published_storage_fraction,
                )
                return result
        for before, after in zip(points, points[1:]):
            before_date = date.fromisoformat(before.date)
            after_date = date.fromisoformat(after.date)
            if before_date < target < after_date:
                weight = (target - before_date).days / (after_date - before_date).days
                result.update(
                    reservoir_storage_l=(
                        before.storage_l + (after.storage_l - before.storage_l) * weight
                    ),
                    reservoir_capacity_l=(
                        before.capacity_l + (after.capacity_l - before.capacity_l) * weight
                    ),
                    reservoir_storage_fraction=(
                        before.published_storage_fraction
                        + (
                            after.published_storage_fraction
                            - before.published_storage_fraction
                        )
                        * weight
                    ),
                    reservoir_interpolated=True,
                )
                return result
        return result


@dataclass(slots=True)
class ReservoirAddition:
    name: str
    commission_day: int
    added_capacity_l: float
    commissioning_fill_l: float = 0.0

    def validate(self) -> None:
        if not self.name:
            raise ValueError("reservoir addition name must not be empty")
        if self.commission_day < 1:
            raise ValueError(f"{self.name}: commission_day must be >= 1")
        if self.added_capacity_l <= 0:
            raise ValueError(f"{self.name}: added capacity must be positive")
        if not 0 <= self.commissioning_fill_l <= self.added_capacity_l:
            raise ValueError(f"{self.name}: commissioning fill must be within added capacity")


@dataclass(slots=True)
class AgentConfig:
    enabled: bool = True
    action_effects_enabled: bool = True
    restriction_trigger_storage_fraction: float = 0.25
    lift_trigger_storage_fraction: float = 0.55
    restriction_multiplier: float = 0.75
    history_limit: int = 8
    decision_interval_days: int = 1
    decision_start_day: int = 1
    decision_end_day: int = 0
    event_decisions_enabled: bool = True
    max_decision_rounds: int = 0

    def validate(self) -> None:
        for name in (
            "restriction_trigger_storage_fraction",
            "lift_trigger_storage_fraction",
            "restriction_multiplier",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"agents.{name} must be between 0 and 1")
        if self.restriction_trigger_storage_fraction >= self.lift_trigger_storage_fraction:
            raise ValueError("agent restriction trigger must be below lift trigger")
        if self.history_limit < 0:
            raise ValueError("agents.history_limit must be non-negative")
        if self.decision_interval_days < 1:
            raise ValueError("agents.decision_interval_days must be >= 1")
        if self.decision_start_day < 1:
            raise ValueError("agents.decision_start_day must be >= 1")
        if self.decision_end_day < 0:
            raise ValueError("agents.decision_end_day must be >= 0")
        if self.decision_end_day and self.decision_end_day < self.decision_start_day:
            raise ValueError("agents.decision_end_day must be 0 or >= decision_start_day")
        if self.max_decision_rounds < 0:
            raise ValueError("agents.max_decision_rounds must be >= 0")


@dataclass(slots=True)
class SimulationConfig:
    name: str
    start_date: str
    days: int
    seed: int
    population: int
    per_capita_potable_demand_l_per_day: float
    initial_storage_l: float
    storage_capacity_l: float
    source_l_per_day: float
    monthly_demand_multipliers: list[float] = field(default_factory=lambda: [1.0] * 12)
    source_mode: str = "direct_inflow"
    service_headroom_fraction: float = 0.0
    source_schedule_l: list[float] = field(default_factory=list)
    monthly_source_multipliers: list[float] = field(default_factory=lambda: [1.0] * 12)
    source_scenario_multiplier: float = 1.0
    allocation_policy: str = "resident_first"
    drought_source_multiplier: float = 0.55
    sensitivity_reclaimed_shares: list[float] = field(
        default_factory=lambda: [0.0, 0.02, 0.25, 0.50]
    )
    comparison_added_reservoir_capacity_l: float = 0.0
    comparison_added_reservoir_commission_day: int = 1
    comparison_added_reservoir_fill_l: float = 0.0
    comparison_dc_onsite_storage_capacity_l: float = 0.0
    comparison_dc_onsite_initial_storage_l: float = 0.0
    comparison_dc_onsite_max_refill_l_per_day: float = 0.0
    comparison_large_campus_it_load_mw: float = 0.0
    comparison_drought_regional_net_change_l_per_day: float = 0.0
    location: LocationConfig = field(default_factory=LocationConfig)
    regional_source: RegionalSourceConfig = field(default_factory=RegionalSourceConfig)
    observed_context: ObservedContextConfig = field(default_factory=ObservedContextConfig)
    reservoir_additions: list[ReservoirAddition] = field(default_factory=list)
    facilities: list[FacilityConfig] = field(default_factory=list)
    agents: AgentConfig = field(default_factory=AgentConfig)

    def validate(self) -> None:
        if not self.name:
            raise ValueError("simulation.name must not be empty")
        date.fromisoformat(self.start_date)
        if self.days < 1:
            raise ValueError("simulation.days must be >= 1")
        if self.population < 0 or self.per_capita_potable_demand_l_per_day < 0:
            raise ValueError("population and per-capita demand must be non-negative")
        if len(self.monthly_demand_multipliers) != 12:
            raise ValueError("monthly_demand_multipliers must have 12 values")
        if any(value < 0 for value in self.monthly_demand_multipliers):
            raise ValueError("monthly demand multipliers must be non-negative")
        if self.storage_capacity_l <= 0:
            raise ValueError("storage capacity must be positive")
        if not 0 <= self.initial_storage_l <= self.storage_capacity_l:
            raise ValueError("initial storage must be within [0, capacity]")
        if self.source_l_per_day < 0 or self.source_scenario_multiplier < 0:
            raise ValueError("source water values must be non-negative")
        if self.source_mode not in {"direct_inflow", "calibrated_service"}:
            raise ValueError("source.mode must be direct_inflow or calibrated_service")
        if self.service_headroom_fraction < 0:
            raise ValueError("source.service_headroom_fraction must be non-negative")
        if self.source_schedule_l and len(self.source_schedule_l) < self.days:
            raise ValueError("source_schedule_l must cover every simulated day")
        if any(value < 0 for value in self.source_schedule_l):
            raise ValueError("source_schedule_l values must be non-negative")
        if len(self.monthly_source_multipliers) != 12:
            raise ValueError("monthly_source_multipliers must have 12 values")
        if any(value < 0 for value in self.monthly_source_multipliers):
            raise ValueError("monthly source multipliers must be non-negative")
        if self.allocation_policy not in {"resident_first", "proportional"}:
            raise ValueError("allocation_policy must be resident_first or proportional")
        if self.drought_source_multiplier < 0:
            raise ValueError("comparison drought multiplier must be non-negative")
        if any(not 0 <= share <= 1 for share in self.sensitivity_reclaimed_shares):
            raise ValueError("reclaimed-water sensitivity shares must be between 0 and 1")
        if self.comparison_added_reservoir_capacity_l < 0:
            raise ValueError("comparison added reservoir capacity must be non-negative")
        if self.comparison_added_reservoir_commission_day < 1:
            raise ValueError("comparison reservoir commission day must be >= 1")
        if not 0 <= self.comparison_added_reservoir_fill_l <= self.comparison_added_reservoir_capacity_l:
            raise ValueError("comparison reservoir fill must be within added capacity")
        if self.comparison_dc_onsite_storage_capacity_l < 0:
            raise ValueError("comparison DC onsite capacity must be non-negative")
        if not 0 <= self.comparison_dc_onsite_initial_storage_l <= self.comparison_dc_onsite_storage_capacity_l:
            raise ValueError("comparison DC onsite initial storage must be within capacity")
        if self.comparison_dc_onsite_max_refill_l_per_day < 0:
            raise ValueError("comparison DC onsite refill must be non-negative")
        if self.comparison_large_campus_it_load_mw < 0:
            raise ValueError("comparison large-campus IT load must be non-negative")
        self.location.validate()
        self.regional_source.validate()
        self.observed_context.validate(self.days)
        for addition in self.reservoir_additions:
            addition.validate()
        for facility in self.facilities:
            facility.validate()
        self.agents.validate()

    def clone(self, *, name: str | None = None) -> "SimulationConfig":
        result = copy.deepcopy(self)
        if name is not None:
            result.name = name
        return result


def _facility_from_dict(raw: dict[str, Any]) -> FacilityConfig:
    return FacilityConfig(**raw)


def _reservoir_addition_from_dict(raw: dict[str, Any]) -> ReservoirAddition:
    return ReservoirAddition(**raw)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_data_path(config_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def _observed_context_from_dict(
    raw: dict[str, Any], config_path: Path, simulation_days: int
) -> ObservedContextConfig:
    if not raw:
        return ObservedContextConfig()

    precipitation_path_raw = str(raw.get("precipitation_csv", ""))
    reservoir_path_raw = str(raw.get("reservoir_csv", ""))
    daily_precipitation: list[float] = []
    precipitation_sha256 = ""
    if precipitation_path_raw:
        precipitation_path = _resolve_data_path(config_path, precipitation_path_raw)
        with precipitation_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        reference_start = date.fromisoformat(
            str(raw["precipitation_reference_start_date"])
        )
        expected_dates = [
            (reference_start + timedelta(days=index)).isoformat()
            for index in range(simulation_days)
        ]
        actual_dates = [str(row["date"]) for row in rows]
        if actual_dates != expected_dates:
            raise ValueError(
                "observed precipitation CSV dates must exactly match the reference period"
            )
        daily_precipitation = [float(row["precipitation_mm"]) for row in rows]
        precipitation_sha256 = _sha256(precipitation_path)

    reservoir_points: list[ObservedReservoirPoint] = []
    reservoir_sha256 = ""
    if reservoir_path_raw:
        reservoir_path = _resolve_data_path(config_path, reservoir_path_raw)
        with reservoir_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        reservoir_points = [
            ObservedReservoirPoint(
                date=str(row["date"]),
                capacity_l=float(row["capacity_1000m3"]) * 1_000_000.0,
                storage_l=float(row["storage_1000m3"]) * 1_000_000.0,
                published_storage_fraction=float(row["published_storage_percent"])
                / 100.0,
            )
            for row in rows
        ]
        reservoir_sha256 = _sha256(reservoir_path)

    return ObservedContextConfig(
        precipitation_station=str(raw.get("precipitation_station", "")),
        precipitation_reference_start_date=str(
            raw.get("precipitation_reference_start_date", "")
        ),
        precipitation_source_url=str(raw.get("precipitation_source_url", "")),
        precipitation_data_file=precipitation_path_raw,
        precipitation_sha256=precipitation_sha256,
        precipitation_used_as_inflow=bool(
            raw.get("precipitation_used_as_inflow", False)
        ),
        daily_precipitation_mm=daily_precipitation,
        reservoir_name=str(raw.get("reservoir_name", "")),
        reservoir_reference_start_date=str(
            raw.get("reservoir_reference_start_date", "")
        ),
        reservoir_source_url=str(raw.get("reservoir_source_url", "")),
        reservoir_data_file=reservoir_path_raw,
        reservoir_sha256=reservoir_sha256,
        reservoir_interpolation=str(raw.get("reservoir_interpolation", "none")),
        reservoir_used_for_allocation=bool(
            raw.get("reservoir_used_for_allocation", False)
        ),
        reservoir_points=reservoir_points,
    )


def load_config(path: str | Path) -> SimulationConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    simulation = raw.get("simulation", {})
    community = raw.get("community", {})
    reservoir = raw.get("reservoir", {})
    source = raw.get("source", {})
    allocation = raw.get("allocation", {})
    comparison = raw.get("comparison", {})
    datacenters = raw.get("datacenters", {})
    agent_raw = raw.get("agents", {})
    location_raw = raw.get("location", {})
    regional_source_raw = raw.get("regional_source", {})
    observed_context_raw = raw.get("observed_context", {})

    config = SimulationConfig(
        name=simulation["name"],
        start_date=simulation["start_date"],
        days=int(simulation["days"]),
        seed=int(simulation.get("seed", 0)),
        population=int(community["population"]),
        per_capita_potable_demand_l_per_day=float(
            community["per_capita_potable_demand_l_per_day"]
        ),
        monthly_demand_multipliers=[
            float(value) for value in community.get("monthly_demand_multipliers", [1.0] * 12)
        ],
        initial_storage_l=float(reservoir["initial_storage_l"]),
        storage_capacity_l=float(reservoir["storage_capacity_l"]),
        source_l_per_day=float(source["source_l_per_day"]),
        source_mode=str(source.get("mode", "direct_inflow")),
        service_headroom_fraction=float(source.get("service_headroom_fraction", 0.0)),
        source_schedule_l=[float(value) for value in source.get("source_schedule_l", [])],
        monthly_source_multipliers=[
            float(value) for value in source.get("monthly_source_multipliers", [1.0] * 12)
        ],
        source_scenario_multiplier=float(source.get("scenario_multiplier", 1.0)),
        allocation_policy=allocation.get("policy", "resident_first"),
        drought_source_multiplier=float(comparison.get("drought_source_multiplier", 0.55)),
        sensitivity_reclaimed_shares=[
            float(value)
            for value in comparison.get("reclaimed_water_shares", [0.0, 0.02, 0.25, 0.50])
        ],
        comparison_added_reservoir_capacity_l=float(
            comparison.get("added_reservoir_capacity_l", 0.0)
        ),
        comparison_added_reservoir_commission_day=int(
            comparison.get("added_reservoir_commission_day", 1)
        ),
        comparison_added_reservoir_fill_l=float(
            comparison.get("added_reservoir_fill_l", 0.0)
        ),
        comparison_dc_onsite_storage_capacity_l=float(
            comparison.get("dc_onsite_storage_capacity_l", 0.0)
        ),
        comparison_dc_onsite_initial_storage_l=float(
            comparison.get("dc_onsite_initial_storage_l", 0.0)
        ),
        comparison_dc_onsite_max_refill_l_per_day=float(
            comparison.get("dc_onsite_max_refill_l_per_day", 0.0)
        ),
        comparison_large_campus_it_load_mw=float(
            comparison.get("large_campus_it_load_mw", 0.0)
        ),
        comparison_drought_regional_net_change_l_per_day=float(
            comparison.get("drought_regional_net_change_l_per_day", 0.0)
        ),
        location=LocationConfig(**location_raw),
        regional_source=RegionalSourceConfig(**regional_source_raw),
        observed_context=_observed_context_from_dict(
            observed_context_raw, config_path, int(simulation["days"])
        ),
        reservoir_additions=[
            _reservoir_addition_from_dict(item) for item in reservoir.get("additions", [])
        ],
        facilities=[_facility_from_dict(item) for item in datacenters.get("facilities", [])],
        agents=AgentConfig(**agent_raw),
    )
    config.validate()
    return config
