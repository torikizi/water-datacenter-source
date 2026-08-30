from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from water_negotiation_lab.comparison import build_comparison_scenarios, run_comparison
from water_negotiation_lab.config import (
    FacilityConfig,
    ObservedContextConfig,
    load_config,
)
from water_negotiation_lab.game_view import _compact_dataset, _run_rows, write_game_view
from water_negotiation_lab.providers import MockProvider
from water_negotiation_lab.runner import run_simulation


class ConfigAndComparisonTests(unittest.TestCase):
    def test_example_configuration_loads(self) -> None:
        config = load_config(Path("examples/mvp.toml"))
        self.assertEqual(config.facilities[0].reclaimed_water_share, 0.0)
        self.assertEqual(config.facilities[0].potable_water_share, 1.0)
        self.assertEqual(config.sensitivity_reclaimed_shares, [0.0, 0.02, 0.25, 0.5])

    def test_invalid_source_shares_are_rejected(self) -> None:
        facility = FacilityConfig(
            name="invalid", start_day=1, it_load_mw=1, utilization=1, wue_l_per_kwh=1,
            potable_water_share=1.0, reclaimed_water_share=0.25,
        )
        with self.assertRaises(ValueError):
            facility.validate()

    def test_source_shares_must_each_be_between_zero_and_one(self) -> None:
        for potable, reclaimed in ((-0.2, 1.2), (1.2, -0.2)):
            with self.subTest(potable=potable, reclaimed=reclaimed):
                facility = FacilityConfig(
                    name="invalid",
                    start_day=1,
                    it_load_mw=1,
                    utilization=1,
                    wue_l_per_kwh=1,
                    potable_water_share=potable,
                    reclaimed_water_share=reclaimed,
                )
                with self.assertRaises(ValueError):
                    facility.validate()

    def test_comparison_includes_counterfactual_and_sensitivity(self) -> None:
        config = load_config(Path("examples/mvp.toml"))
        config.days = 3
        with tempfile.TemporaryDirectory() as directory:
            result = run_comparison(config, directory)
            artifacts = {path.name for path in Path(directory).iterdir() if path.is_file()}
        scenarios = {item["scenario"] for item in result["scenarios"]}
        self.assertIn("normal_no_datacenter", scenarios)
        self.assertIn("drought_no_datacenter", scenarios)
        self.assertIn("normal_dc_concentrated_facilities", scenarios)
        self.assertIn("normal_reclaimed_sensitivity_25pct", scenarios)
        self.assertIn("normal_reclaimed_sensitivity_02pct", scenarios)
        self.assertTrue(
            {
                "comparison_summary.json",
                "storage_comparison.svg",
                "resident_supply_shortage.svg",
                "datacenter_potable_use.svg",
            }
            <= artifacts
        )

    def test_game_view_embeds_python_simulation_data(self) -> None:
        config = load_config(Path("examples/mvp.toml"))
        config.days = 3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.html"
            write_game_view(config, path, fragment=True)
            content = path.read_text(encoding="utf-8")
        self.assertIn('id="water-city-game-v1"', content)
        self.assertIn("normal_reclaimed_sensitivity_02pct", content)
        self.assertIn('aria-live="polite"', content)
        self.assertIn('id="wg-chart-v1"', content)
        self.assertIn('id="wg-load-slider-v1"', content)
        self.assertIn('id="wg-agent-grid-v1"', content)
        self.assertIn("比較する2ケース", content)
        self.assertIn('"startDate":"', content)
        self.assertIn('"population":', content)
        self.assertNotIn("__SIMULATION_DATA__", content)
        self.assertNotIn("<!doctype", content.lower())

    def test_game_view_embeds_audited_agent_run(self) -> None:
        config = load_config(Path("configs/inzai_chiba_new_town.toml"))
        agent_config = load_config(Path("examples/ds4_agent_summer.toml"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_run = root / "agent-run"
            run_simulation(agent_config, agent_run, MockProvider())
            path = root / "agent-game.html"
            write_game_view(
                config,
                path,
                fragment=True,
                agent_run_dir=agent_run,
            )
            content = path.read_text(encoding="utf-8")
        self.assertIn('"id":"agent_decision_season"', content)
        self.assertIn('"agentMode":true', content)
        self.assertIn('"agentEvents":[', content)
        self.assertIn("夏季〜初秋モック", content)
        self.assertIn("関係者の論点", content)
        self.assertIn("計算値連動・ルール表示", content)
        self.assertIn("データセンターあり（IT負荷を変更）", content)
        self.assertIn('id="wg-load-select-v1"', content)
        self.assertIn("DEMO.loadScenarios.length+'段階から選択'", content)
        self.assertIn("不足なし最大", content)
        self.assertIn('"maxNoShortageMw":124.6', content)
        self.assertIn("+'は不足0 L ／ '+", content)
        self.assertNotIn("SCHEMA REJECTED → NO_ACTION", content)

    def test_two_case_demo_precomputes_load_threshold_and_cooling(self) -> None:
        config = load_config(Path("configs/inzai_chiba_new_town.toml"))
        data = _compact_dataset(config)
        demo = data["demo"]

        self.assertEqual(
            [item["id"] for item in demo["cases"]],
            ["without_dc", "with_dc"],
        )
        self.assertEqual(demo["calculationAuthority"], "python_precomputed")
        self.assertAlmostEqual(demo["threshold"]["displayMw"], 124.7)
        self.assertAlmostEqual(demo["threshold"]["maxNoShortageMw"], 124.6)
        self.assertEqual(len(demo["loadScenarios"]), 18)
        self.assertEqual(
            demo["threshold"]["firstShortageDayAtDisplayMw"],
            102,
        )

        no_dc = demo["noDatacenter"]
        self.assertEqual(no_dc["metrics"]["cumulativeResidentShortageL"], 0)
        self.assertTrue(all(value == 0 for value in no_dc["dailyCoolingRequirementL"]))

        load_124_6 = next(
            item for item in demo["loadScenarios"] if item["itLoadMw"] == 124.6
        )
        self.assertEqual(load_124_6["metrics"]["cumulativeResidentShortageL"], 0)
        self.assertEqual(load_124_6["metrics"]["firstResidentShortageDay"], None)

        load_124_7 = next(
            item for item in demo["loadScenarios"] if item["itLoadMw"] == 124.7
        )
        self.assertEqual(load_124_7["metrics"]["firstResidentShortageDay"], 102)
        self.assertGreater(load_124_7["metrics"]["cumulativeResidentShortageL"], 0)

        load_250 = next(
            item for item in demo["loadScenarios"] if item["itLoadMw"] == 250.0
        )
        self.assertEqual(load_250["dailyItEnergyKwh"][30], 4_800_000)
        self.assertEqual(load_250["dailyCoolingRequirementL"][30], 5_280_000)
        self.assertEqual(load_250["metrics"]["firstResidentShortageDay"], 39)
        self.assertEqual(
            load_250["metrics"]["cumulativeResidentShortageL"],
            153_528_623,
        )
        self.assertEqual(
            load_250["metrics"]["residentSupplyReductionVsNoDcL"],
            load_250["metrics"]["cumulativeResidentShortageL"],
        )

    def test_game_view_escapes_agent_text_inside_script(self) -> None:
        config = load_config(Path("configs/inzai_chiba_new_town.toml"))
        agent_config = load_config(Path("examples/ds4_agent_summer.toml"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_run = root / "agent-run"
            run_simulation(agent_config, agent_run, MockProvider())
            log_path = agent_run / "llm_messages.jsonl"
            events = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            events[0]["parsed_response"]["message"] = "</script><script>boom()</script>"
            log_path.write_text(
                "\n".join(
                    json.dumps(event, ensure_ascii=False)
                    for event in events
                )
                + "\n",
                encoding="utf-8",
            )
            path = root / "agent-game.html"
            write_game_view(config, path, fragment=True, agent_run_dir=agent_run)
            content = path.read_text(encoding="utf-8")
        self.assertNotIn("</script><script>boom()", content)
        self.assertIn("<\\/script><script>boom()", content)

    def test_real_ds4_game_separates_policy_change_from_zero_physical_effect(self) -> None:
        config = load_config(Path("configs/inzai_chiba_new_town_legacy_365.toml"))
        data = _compact_dataset(
            config,
            Path("submission_artifacts/ds4_agent_season_demo"),
        )
        scenario = next(item for item in data["scenarios"] if item["agentMode"])
        rows = {row[0]: row for row in scenario["rows"]}
        self.assertEqual(len(scenario["agentEvents"]), 8)
        self.assertEqual(
            sum(len(event["decisions"]) for event in scenario["agentEvents"]),
            32,
        )
        self.assertEqual(
            sum(
                decision["valid"]
                for event in scenario["agentEvents"]
                for decision in event["decisions"]
            ),
            24,
        )
        self.assertEqual(rows[36][28:30], [1.0, 0.75])
        self.assertEqual(rows[37][28], 0.75)
        self.assertEqual(rows[37][33:36], [0, 0, 0])
        self.assertEqual(data["schemaVersion"], 3)
        self.assertEqual(len(data["rowColumns"]), 45)
        self.assertEqual(len(data["rowColumns"]), len(rows[36]))

    def test_improved_ds4_game_embeds_early_policy_effect(self) -> None:
        config = load_config(Path("configs/inzai_chiba_new_town_legacy_365.toml"))
        data = _compact_dataset(
            config,
            Path("submission_artifacts/ds4_agent_season_demo_v2"),
        )
        scenario = next(item for item in data["scenarios"] if item["agentMode"])
        rows = {row[0]: row for row in scenario["rows"]}
        self.assertIn("改善後監査", scenario["label"])
        self.assertEqual(rows[33][28:30], [1.0, 0.75])
        self.assertEqual(rows[34][28], 0.75)
        self.assertEqual(rows[34][33:36], [1_320_000, -1_320_000, 1_320_000])
        decisions = [
            decision
            for event in scenario["agentEvents"]
            for decision in event["decisions"]
        ]
        legacy = [decision for decision in decisions if decision["legacyLongResponse"]]
        self.assertEqual(len(legacy), 3)
        self.assertTrue(all(decision["action"] == "request_information" for decision in legacy))
        self.assertTrue(all(len(decision["message"]) <= 240 for decision in legacy))
        self.assertEqual(
            scenario["agentAudit"],
            "29 STRICT · 3 LEGACY LONG / RAW SAVED",
        )

    def test_inzai_profile_includes_storage_comparisons(self) -> None:
        config = load_config(Path("configs/inzai_chiba_new_town.toml"))
        self.assertEqual(config.location.profile_id, "inzai_chiba_new_town_prefectural_water")
        names = {name for name, _, _ in build_comparison_scenarios(config)}
        self.assertIn("normal_dc_added_distribution_reservoir", names)
        self.assertIn("normal_dc_with_onsite_storage", names)
        self.assertIn("normal_dc_large_campus_resident_first", names)
        self.assertIn("normal_dc_large_campus_proportional", names)
        self.assertEqual(config.source_mode, "calibrated_service")
        self.assertEqual(config.population, 70_760)
        self.assertEqual(config.per_capita_potable_demand_l_per_day, 280.0)
        self.assertEqual(config.start_date, "2026-06-01")
        self.assertEqual(config.days, 102)
        self.assertEqual(config.initial_storage_l, 48_000_000.0)
        self.assertEqual(config.initial_storage_l, config.storage_capacity_l)
        self.assertEqual(
            date.fromisoformat(config.start_date) + timedelta(days=config.days - 1),
            date(2026, 9, 10),
        )
        self.assertFalse(config.regional_source.enabled)
        self.assertEqual([item.start_day for item in config.facilities], [31, 46, 62])
        self.assertEqual(config.comparison_added_reservoir_commission_day, 62)

        scenarios = {name: item for name, _, item in build_comparison_scenarios(config)}
        no_dc = _run_rows(scenarios["normal_no_datacenter"])
        concentrated = _run_rows(scenarios["normal_dc_concentrated_facilities"])
        added_reservoir = _run_rows(
            scenarios["normal_dc_added_distribution_reservoir"]
        )
        large_proportional = _run_rows(
            scenarios["normal_dc_large_campus_proportional"]
        )
        self.assertTrue(all(len(_run_rows(item)) == 102 for item in scenarios.values()))
        self.assertEqual(concentrated[-1]["facilities"][-1]["name"], "inzai_synthetic_dc_gamma")
        self.assertEqual(len(concentrated[-1]["facilities"]), 3)
        self.assertGreater(
            sum(row["reservoir_commissioning_fill_l"] for row in added_reservoir),
            0.0,
        )
        self.assertEqual(sum(row["resident_shortage_l"] for row in no_dc), 0.0)
        self.assertEqual(no_dc[0]["storage_start_l"], 48_000_000.0)
        self.assertEqual(no_dc[0]["storage_capacity_l"], 48_000_000.0)
        self.assertAlmostEqual(no_dc[0]["spill_l"], no_dc[0]["source_inflow_l"])
        self.assertGreater(
            sum(row["resident_shortage_l"] for row in large_proportional), 0.0
        )

        game_data = _compact_dataset(config)
        game_scenarios = {item["id"]: item for item in game_data["scenarios"]}
        single_rows = {
            row[0]: row
            for row in game_scenarios[
                "normal_dc_no_reclaimed_resident_first"
            ]["rows"]
        }
        reservoir_rows = game_scenarios[
            "normal_dc_added_distribution_reservoir"
        ]["rows"]
        self.assertLess(single_rows[31][25], 0)
        self.assertGreater(reservoir_rows[-1][25], 0)

    def test_inzai_observed_context_covers_reference_summer_and_interpolates(self) -> None:
        config = load_config(Path("configs/inzai_chiba_new_town.toml"))
        context = config.observed_context

        self.assertTrue(context.enabled)
        self.assertEqual(len(context.daily_precipitation_mm), 102)
        self.assertAlmostEqual(sum(context.daily_precipitation_mm), 394.5)
        self.assertEqual(context.precipitation_reference_start_date, "2025-06-01")
        self.assertEqual(
            date.fromisoformat(context.precipitation_reference_start_date)
            + timedelta(days=len(context.daily_precipitation_mm) - 1),
            date(2025, 9, 10),
        )
        self.assertEqual(context.daily_precipitation_mm[0], 2.0)
        self.assertEqual(context.daily_precipitation_mm[-1], 9.0)
        self.assertFalse(context.precipitation_used_as_inflow)
        self.assertFalse(context.reservoir_used_for_allocation)

        day_1 = context.values_for_day(0)
        day_6 = context.values_for_day(5)
        day_102 = context.values_for_day(101)
        self.assertEqual(day_1["precipitation_mm"], 2.0)
        self.assertEqual(day_1["reservoir_storage_l"], 498_240_000_000.0)
        self.assertEqual(day_1["reservoir_capacity_l"], 551_630_000_000.0)
        self.assertAlmostEqual(day_1["reservoir_storage_fraction"], 0.903)
        self.assertFalse(day_1["reservoir_interpolated"])

        # 6 June is exactly halfway between the published 1 and 11 June values.
        self.assertEqual(day_6["reservoir_storage_l"], 481_535_000_000.0)
        self.assertEqual(day_6["reservoir_capacity_l"], 551_630_000_000.0)
        self.assertAlmostEqual(day_6["reservoir_storage_fraction"], 0.873)
        self.assertTrue(day_6["reservoir_interpolated"])

        # 10 September is 9/10 of the way from the 1 to 11 September values.
        self.assertEqual(day_102["precipitation_mm"], 9.0)
        self.assertAlmostEqual(
            day_102["reservoir_storage_l"], 157_307_000_000.0
        )
        self.assertEqual(
            day_102["reservoir_capacity_l"], 368_490_000_000.0
        )
        self.assertAlmostEqual(
            day_102["reservoir_storage_fraction"], 0.4271
        )
        self.assertTrue(day_102["reservoir_interpolated"])

        game_data = _compact_dataset(config)
        self.assertEqual(game_data["schemaVersion"], 3)
        self.assertEqual(len(game_data["rowColumns"]), 45)
        self.assertEqual(
            game_data["rowColumns"][-9:],
            [
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
        )
        self.assertTrue(
            all(
                len(row) == len(game_data["rowColumns"])
                for scenario in game_data["scenarios"]
                for row in scenario["rows"]
            )
        )
        observed_payload = game_data["observedContext"]
        self.assertAlmostEqual(observed_payload["precipitation"]["totalMm"], 394.5)
        self.assertFalse(observed_payload["precipitation"]["usedAsInflow"])
        self.assertFalse(observed_payload["reservoir"]["usedForAllocation"])
        self.assertEqual(len(observed_payload["reservoir"]["points"]), 10)
        self.assertEqual(
            observed_payload["reservoir"]["points"][-1]["date"], "2025-09-01"
        )

        no_dc_rows = next(
            item["rows"]
            for item in game_data["scenarios"]
            if item["id"] == "normal_no_datacenter"
        )
        column = {name: index for index, name in enumerate(game_data["rowColumns"])}
        self.assertEqual(no_dc_rows[0][column["observed_precipitation_mm"]], 2.0)
        self.assertEqual(no_dc_rows[-1][column["observed_precipitation_mm"]], 9.0)
        self.assertEqual(
            no_dc_rows[0][column["observed_reservoir_reference_storage_l"]],
            498_240_000_000.0,
        )
        self.assertAlmostEqual(
            no_dc_rows[-1][column["observed_reservoir_reference_storage_l"]],
            157_307_000_000.0,
        )

    def test_observed_context_is_not_used_by_the_physical_water_balance(self) -> None:
        with_context = load_config(Path("configs/inzai_chiba_new_town.toml"))
        without_context = with_context.clone()
        without_context.observed_context = ObservedContextConfig()

        observed_rows = _run_rows(with_context)
        control_rows = _run_rows(without_context)
        self.assertEqual(len(observed_rows), 102)
        self.assertEqual(observed_rows[0]["date"], "2026-06-01")
        self.assertEqual(observed_rows[-1]["date"], "2026-09-10")
        self.assertEqual(
            observed_rows[0]["observed_context_reference_date"], "2025-06-01"
        )
        self.assertEqual(
            observed_rows[-1]["observed_context_reference_date"], "2025-09-10"
        )

        physical_fields = (
            "storage_start_l",
            "source_inflow_l",
            "reservoir_commissioning_fill_l",
            "spill_l",
            "resident_supply_l",
            "resident_shortage_l",
            "datacenter_potable_withdrawal_l",
            "storage_end_l",
            "water_balance_error_l",
        )
        for observed, control in zip(observed_rows, control_rows, strict=True):
            with self.subTest(day=observed["day"]):
                self.assertFalse(observed["observed_precipitation_used_as_inflow"])
                self.assertFalse(observed["observed_reservoir_used_for_allocation"])
                for field in physical_fields:
                    self.assertEqual(observed[field], control[field])

                balance_inputs = (
                    observed["storage_start_l"]
                    + observed["source_inflow_l"]
                    + observed["reservoir_commissioning_fill_l"]
                )
                balance_outputs = (
                    observed["spill_l"]
                    + observed["resident_supply_l"]
                    + observed["datacenter_potable_withdrawal_l"]
                    + observed["storage_end_l"]
                )
                self.assertAlmostEqual(balance_inputs, balance_outputs, places=6)
                self.assertAlmostEqual(observed["water_balance_error_l"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
