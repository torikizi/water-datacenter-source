from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from water_negotiation_lab.config import (
    AgentConfig,
    FacilityConfig,
    RegionalSourceConfig,
    ReservoirAddition,
    SimulationConfig,
)
from water_negotiation_lab.agents import next_restriction_multiplier
from water_negotiation_lab.engine import allocate_water, facility_demand, simulate_day
from water_negotiation_lab.providers import MockProvider
from water_negotiation_lab.runner import run_simulation


def sample_config(*, policy: str = "resident_first", days: int = 12) -> SimulationConfig:
    return SimulationConfig(
        name="test_synthetic",
        start_date="2026-01-01",
        days=days,
        seed=7,
        population=100,
        per_capita_potable_demand_l_per_day=200.0,
        initial_storage_l=100_000.0,
        storage_capacity_l=120_000.0,
        source_l_per_day=15_000.0,
        allocation_policy=policy,
        facilities=[
            FacilityConfig(
                name="test_dc",
                start_day=2,
                it_load_mw=0.1,
                utilization=0.5,
                wue_l_per_kwh=1.5,
                potable_water_share=1.0,
                reclaimed_water_share=0.0,
                evaporation_share=0.7,
                blowdown_share=0.2,
                regional_return_share=0.05,
                recoverable_wastewater_share=0.05,
            )
        ],
        agents=AgentConfig(enabled=True, action_effects_enabled=True),
    )


class WaterEngineTests(unittest.TestCase):
    def test_it_energy_and_wue_formula(self) -> None:
        facility = FacilityConfig(
            name="formula",
            start_day=1,
            it_load_mw=10.0,
            utilization=0.5,
            wue_l_per_kwh=1.5,
        )
        demand = facility_demand(facility, 1, 1.0)
        self.assertEqual(demand.it_energy_kwh, 10.0 * 1000.0 * 24.0 * 0.5)
        self.assertEqual(
            demand.direct_water_requirement_l, demand.it_energy_kwh * 1.5
        )
        self.assertEqual(demand.potable_requirement_l, demand.direct_water_requirement_l)

    def test_resident_first_and_proportional_allocation(self) -> None:
        self.assertEqual(allocate_water(100.0, 80.0, 40.0, "resident_first"), (80.0, 20.0))
        resident, dc = allocate_water(100.0, 80.0, 40.0, "proportional")
        self.assertAlmostEqual(resident, 80.0 * 100.0 / 120.0)
        self.assertAlmostEqual(dc, 40.0 * 100.0 / 120.0)

    def test_conservation_bounds_and_nonnegative_shortages(self) -> None:
        config = sample_config()
        storage = config.initial_storage_l
        for index in range(config.days):
            row = simulate_day(config, index, storage)
            self.assertAlmostEqual(row["water_balance_error_l"], 0.0, places=7)
            self.assertGreaterEqual(row["storage_end_l"], 0.0)
            self.assertLessEqual(row["storage_end_l"], config.storage_capacity_l)
            self.assertGreaterEqual(row["resident_shortage_l"], 0.0)
            self.assertGreaterEqual(row["datacenter_water_shortage_l"], 0.0)
            storage = row["storage_end_l"]

    def test_start_of_day_inflow_pulse_spills_before_daily_demand(self) -> None:
        config = sample_config(days=1)
        config.facilities = []
        config.population = 1
        config.per_capita_potable_demand_l_per_day = 100.0
        config.initial_storage_l = 100.0
        config.storage_capacity_l = 100.0
        config.source_l_per_day = 100.0
        row = simulate_day(config, 0, config.initial_storage_l)
        self.assertEqual(row["resident_supply_l"], 100.0)
        self.assertEqual(row["spill_l"], 100.0)
        self.assertEqual(row["storage_end_l"], 0.0)
        self.assertAlmostEqual(row["water_balance_error_l"], 0.0)

    def test_data_center_fates_sum_to_delivered_water(self) -> None:
        config = sample_config()
        row = simulate_day(config, 1, config.initial_storage_l)
        fate_total = sum(
            row[field]
            for field in (
                "datacenter_evaporation_l",
                "datacenter_blowdown_l",
                "datacenter_regional_return_l",
                "datacenter_recoverable_wastewater_l",
            )
        )
        delivered = (
            row["datacenter_potable_withdrawal_l"]
            + row["datacenter_reclaimed_withdrawal_l"]
        )
        self.assertAlmostEqual(fate_total, delivered)
        self.assertLessEqual(
            row["datacenter_potable_consumptive_use_l"],
            row["datacenter_potable_withdrawal_l"],
        )

    def test_mock_agent_run_is_reproducible(self) -> None:
        config = sample_config(days=4)
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            rows_a, events_a, summary_a = run_simulation(config, first, MockProvider())
            rows_b, events_b, summary_b = run_simulation(config, second, MockProvider())
        self.assertEqual(rows_a, rows_b)
        self.assertEqual(events_a, events_b)
        self.assertEqual(summary_a, summary_b)
        self.assertEqual(len(events_a), config.days * 4)

    def test_agent_council_uses_sparse_schedule_and_event_rounds(self) -> None:
        config = sample_config(days=40)
        config.initial_storage_l = config.storage_capacity_l
        config.source_l_per_day = 50_000.0
        config.agents.decision_interval_days = 14
        config.agents.decision_start_day = 1
        config.agents.decision_end_day = 40
        config.agents.event_decisions_enabled = False
        config.agents.max_decision_rounds = 8
        with tempfile.TemporaryDirectory() as directory:
            rows, events, summary = run_simulation(config, directory, MockProvider())
        self.assertEqual(summary["agent_decision_days"], [1, 15, 29])
        self.assertEqual(summary["llm_agent_decision_round_count"], 3)
        self.assertEqual(len(events), 12)
        self.assertEqual(sum(bool(row["agent_council_convened"]) for row in rows), 3)

    def test_municipality_resolves_after_three_stakeholder_inputs(self) -> None:
        config = sample_config(days=1)
        with tempfile.TemporaryDirectory() as directory:
            _, events, _ = run_simulation(config, directory, MockProvider())
        self.assertEqual(
            [event["role"]["role"] for event in events],
            [
                "resident_representative",
                "water_utility",
                "datacenter_operator",
                "municipality",
            ],
        )
        municipality = events[-1]
        request = json.loads(municipality["prompt"][-1]["content"])
        self.assertEqual(municipality["role"]["decision_phase"], "policy_resolution")
        self.assertEqual(
            [message["role"] for message in request["past_messages"]],
            ["resident_representative", "water_utility", "datacenter_operator"],
        )

    def test_threshold_crossing_convenes_unscheduled_council(self) -> None:
        config = sample_config(days=3)
        config.facilities = []
        config.initial_storage_l = 51_000.0
        config.storage_capacity_l = 120_000.0
        config.source_l_per_day = 0.0
        config.agents.decision_interval_days = 30
        config.agents.decision_end_day = 3
        with tempfile.TemporaryDirectory() as directory:
            rows, events, summary = run_simulation(config, directory, MockProvider())
        self.assertEqual(summary["agent_decision_days"], [1, 2, 3])
        self.assertEqual(rows[1]["agent_council_reason"], "storage_restriction_threshold_crossed")
        self.assertEqual(rows[2]["agent_council_reason"], "resident_shortage_started")
        self.assertEqual(len(events), 12)

    def test_policy_change_summary_reports_an_actual_multiplier_change(self) -> None:
        stable = sample_config(days=1)
        stable.initial_storage_l = stable.storage_capacity_l
        stable.source_l_per_day = 50_000.0
        with tempfile.TemporaryDirectory() as directory:
            _, _, stable_summary = run_simulation(stable, directory, MockProvider())
        self.assertFalse(stable_summary["agent_actions_changed_water_policy"])

        stressed = sample_config(days=1)
        stressed.initial_storage_l = 1_000.0
        stressed.source_l_per_day = 0.0
        with tempfile.TemporaryDirectory() as directory:
            _, _, stressed_summary = run_simulation(stressed, directory, MockProvider())
        self.assertTrue(stressed_summary["agent_actions_changed_water_policy"])

    def test_invalid_municipality_response_cannot_change_policy(self) -> None:
        config = sample_config(days=1)
        invalid_event = {
            "role": {"role": "municipality"},
            "valid": False,
            "parsed_response": {"action": "enact_dc_restriction"},
        }
        self.assertEqual(
            next_restriction_multiplier(
                1.0,
                [invalid_event],
                config.agents,
                {
                    "storage_end_l": 0.0,
                    "storage_capacity_l": 100.0,
                    "resident_shortage_l": 1.0,
                },
            ),
            1.0,
        )

    def test_python_enforces_municipal_policy_thresholds(self) -> None:
        config = sample_config(days=1)

        def event(action: str) -> dict[str, object]:
            return {
                "role": {"role": "municipality"},
                "valid": True,
                "parsed_response": {"action": action},
            }

        stable_state = {
            "storage_end_l": 90.0,
            "storage_capacity_l": 100.0,
            "resident_shortage_l": 0.0,
        }
        stressed_state = {
            "storage_end_l": 20.0,
            "storage_capacity_l": 100.0,
            "resident_shortage_l": 0.0,
        }
        self.assertEqual(
            next_restriction_multiplier(
                1.0,
                [event("enact_dc_restriction")],
                config.agents,
                stable_state,
            ),
            1.0,
        )
        self.assertEqual(
            next_restriction_multiplier(
                1.0,
                [event("enact_dc_restriction")],
                config.agents,
                stressed_state,
            ),
            config.agents.restriction_multiplier,
        )
        self.assertEqual(
            next_restriction_multiplier(
                config.agents.restriction_multiplier,
                [event("lift_dc_restriction")],
                config.agents,
                stressed_state,
            ),
            config.agents.restriction_multiplier,
        )
        self.assertEqual(
            next_restriction_multiplier(
                config.agents.restriction_multiplier,
                [event("lift_dc_restriction")],
                config.agents,
                stable_state,
            ),
            1.0,
        )

    def test_run_writes_required_artifacts(self) -> None:
        config = sample_config(days=2)
        with tempfile.TemporaryDirectory() as directory:
            run_simulation(config, directory, None)
            names = {path.name for path in Path(directory).iterdir()}
        self.assertTrue(
            {"water_balance.jsonl", "llm_messages.jsonl", "summary.json", "scenario_metrics.svg"}
            <= names
        )

    def test_reservoir_addition_capacity_and_fill_are_accounted(self) -> None:
        config = sample_config(days=2)
        config.facilities = []
        config.reservoir_additions = [
            ReservoirAddition(
                name="test_addition",
                commission_day=2,
                added_capacity_l=50_000.0,
                commissioning_fill_l=30_000.0,
            )
        ]
        first = simulate_day(config, 0, config.initial_storage_l)
        second = simulate_day(config, 1, first["storage_end_l"])
        self.assertEqual(first["storage_capacity_l"], 120_000.0)
        self.assertEqual(second["storage_capacity_l"], 170_000.0)
        self.assertEqual(second["reservoir_commissioning_fill_l"], 30_000.0)
        self.assertAlmostEqual(second["water_balance_error_l"], 0.0)

    def test_onsite_storage_has_separate_balance(self) -> None:
        config = sample_config(days=1)
        facility = config.facilities[0]
        facility.start_day = 1
        facility.onsite_potable_storage_capacity_l = 2_000.0
        facility.onsite_potable_initial_storage_l = 1_800.0
        facility.onsite_potable_max_refill_l_per_day = 1_000.0
        row = simulate_day(
            config,
            0,
            config.initial_storage_l,
            onsite_storage_start_l={facility.name: 1_800.0},
        )
        self.assertEqual(row["datacenter_potable_to_process_l"], 1_800.0)
        self.assertEqual(row["datacenter_potable_withdrawal_l"], 1_000.0)
        self.assertEqual(row["datacenter_onsite_storage_end_l"], 1_000.0)
        self.assertAlmostEqual(row["datacenter_onsite_storage_balance_error_l"], 0.0)
        self.assertAlmostEqual(row["water_balance_error_l"], 0.0)

    def test_regional_source_subtracts_only_incremental_dc_consumptive_use(self) -> None:
        config = sample_config(days=2)
        config.facilities[0].start_day = 1
        config.regional_source = RegionalSourceConfig(
            name="observed source",
            observed_date="2026-01-01",
            initial_storage_l=1_000_000.0,
            capacity_l=1_200_000.0,
            reference_daily_net_change_l=5_000.0,
            evidence_url="https://example.test/official-source",
        )
        row = simulate_day(
            config,
            0,
            config.initial_storage_l,
            regional_source_storage_start_l=config.regional_source.initial_storage_l,
        )
        self.assertAlmostEqual(
            row["regional_source_storage_end_l"],
            1_000_000.0
            + 5_000.0
            - row["datacenter_potable_consumptive_use_l"],
        )
        self.assertAlmostEqual(row["regional_source_balance_error_l"], 0.0)

    def test_calibrated_service_baseline_covers_community_demand(self) -> None:
        config = sample_config(days=1)
        config.facilities = []
        config.source_mode = "calibrated_service"
        config.service_headroom_fraction = 0.1
        row = simulate_day(config, 0, config.initial_storage_l)
        self.assertAlmostEqual(row["source_inflow_l"], row["resident_demand_l"] * 1.1)
        self.assertEqual(row["resident_shortage_l"], 0.0)


if __name__ == "__main__":
    unittest.main()
