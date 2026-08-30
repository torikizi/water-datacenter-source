from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agents import AgentCoordinator, next_restriction_multiplier
from .config import SimulationConfig
from .engine import simulate_day
from .providers import LLMProvider
from .reporting import summarize, write_chart, write_json, write_jsonl


def _storage_fraction(row: dict[str, Any]) -> float:
    capacity = float(row["storage_capacity_l"])
    return float(row["storage_end_l"]) / capacity if capacity > 0 else 0.0


def _decision_reason(
    config: SimulationConfig,
    row: dict[str, Any],
    previous_row: dict[str, Any] | None,
    completed_rounds: int,
) -> str | None:
    agents = config.agents
    day = int(row["day"])
    end_day = agents.decision_end_day or config.days
    if day < agents.decision_start_day or day > end_day:
        return None
    if agents.max_decision_rounds and completed_rounds >= agents.max_decision_rounds:
        return None

    scheduled = (day - agents.decision_start_day) % agents.decision_interval_days == 0
    if agents.event_decisions_enabled and previous_row is not None:
        previous_fraction = _storage_fraction(previous_row)
        current_fraction = _storage_fraction(row)
        if (
            previous_fraction >= agents.restriction_trigger_storage_fraction
            and current_fraction < agents.restriction_trigger_storage_fraction
        ):
            return "storage_restriction_threshold_crossed"
        if (
            previous_fraction < agents.lift_trigger_storage_fraction
            and current_fraction >= agents.lift_trigger_storage_fraction
        ):
            return "storage_lift_threshold_crossed"
        if (
            float(previous_row["resident_shortage_l"]) <= 0
            and float(row["resident_shortage_l"]) > 0
        ):
            return "resident_shortage_started"
        if (
            float(previous_row["datacenter_water_shortage_l"]) <= 0
            and float(row["datacenter_water_shortage_l"]) > 0
        ):
            return "datacenter_shortage_started"
    return "scheduled_review" if scheduled else None


def run_simulation(
    config: SimulationConfig,
    output_dir: str | Path,
    provider: LLMProvider | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    config.validate()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    coordinator = (
        AgentCoordinator(provider, config.agents, config.seed)
        if provider is not None and config.agents.enabled
        else None
    )
    storage = config.initial_storage_l
    regional_source_storage = (
        config.regional_source.initial_storage_l if config.regional_source.enabled else None
    )
    restriction_multiplier = 1.0
    onsite_storage = {
        facility.name: facility.onsite_potable_initial_storage_l
        for facility in config.facilities
    }
    cumulative_resident_shortage = 0.0
    cumulative_dc_shortage = 0.0
    cumulative_potable_consumptive_use = 0.0
    agent_actions_changed_water_policy = False
    agent_policy_changes: list[dict[str, Any]] = []
    decision_rounds = 0
    rows: list[dict[str, Any]] = []
    agent_events: list[dict[str, Any]] = []
    previous_row: dict[str, Any] | None = None

    for day_index in range(config.days):
        row = simulate_day(
            config,
            day_index,
            storage,
            restriction_multiplier,
            onsite_storage,
            regional_source_storage,
        )
        cumulative_resident_shortage += float(row["resident_shortage_l"])
        cumulative_dc_shortage += float(row["datacenter_water_shortage_l"])
        cumulative_potable_consumptive_use += float(
            row["datacenter_potable_consumptive_use_l"]
        )
        row["cumulative_resident_shortage_l"] = cumulative_resident_shortage
        row["cumulative_datacenter_shortage_l"] = cumulative_dc_shortage
        row["cumulative_datacenter_potable_consumptive_use_l"] = (
            cumulative_potable_consumptive_use
        )
        observed_reference = float(
            row["observed_reservoir_reference_storage_l"]
        )
        row["observed_reservoir_counterfactual_with_dc_l"] = max(
            0.0, observed_reference - cumulative_potable_consumptive_use
        )
        row["observed_reservoir_counterfactual_delta_l"] = (
            row["observed_reservoir_counterfactual_with_dc_l"]
            - observed_reference
        )
        reason = (
            _decision_reason(config, row, previous_row, decision_rounds)
            if coordinator is not None
            else None
        )
        row["agent_council_convened"] = reason is not None
        row["agent_council_reason"] = reason
        row["agent_decision_round"] = decision_rounds + 1 if reason is not None else None
        storage = float(row["storage_end_l"])
        if config.regional_source.enabled:
            regional_source_storage = float(row["regional_source_storage_end_l"])
        onsite_storage = {
            str(item["name"]): float(item["onsite_storage_end_l"])
            for item in row["facilities"]
        }
        if coordinator is not None and reason is not None:
            decision_rounds += 1
            events = coordinator.decide(
                row,
                decision_round=decision_rounds,
                decision_reason=reason,
            )
            agent_events.extend(events)
            next_multiplier = next_restriction_multiplier(
                restriction_multiplier, events, config.agents, row
            )
            if abs(next_multiplier - restriction_multiplier) > 1e-12:
                agent_actions_changed_water_policy = True
                agent_policy_changes.append(
                    {
                        "decision_round": decision_rounds,
                        "day": row["day"],
                        "date": row["date"],
                        "from_multiplier": restriction_multiplier,
                        "to_multiplier": next_multiplier,
                        "effective_day": min(config.days, int(row["day"]) + 1),
                    }
                )
            restriction_multiplier = next_multiplier
        row["datacenter_potable_restriction_multiplier_next_day"] = restriction_multiplier
        rows.append(row)
        previous_row = row

    configuration = asdict(config)
    summary = summarize(rows, configuration)
    summary["llm_agent_event_count"] = len(agent_events)
    summary["llm_agent_valid_event_count"] = sum(
        bool(event["valid"]) for event in agent_events
    )
    summary["llm_agent_strict_valid_event_count"] = sum(
        bool(event.get("strict_valid", event["valid"])) for event in agent_events
    )
    summary["llm_agent_normalized_event_count"] = sum(
        bool(event["valid"] and event.get("normalizations")) for event in agent_events
    )
    summary["llm_agent_fallback_event_count"] = sum(
        not bool(event["valid"]) for event in agent_events
    )
    summary["llm_agent_decision_round_count"] = decision_rounds
    summary["agent_decision_days"] = [
        int(row["day"]) for row in rows if row["agent_council_convened"]
    ]
    summary["agent_actions_changed_water_policy"] = agent_actions_changed_water_policy
    summary["agent_policy_changes"] = agent_policy_changes
    write_jsonl(out / "water_balance.jsonl", rows)
    write_jsonl(out / "llm_messages.jsonl", agent_events)
    write_json(out / "summary.json", summary)
    series = [(config.name, rows)]
    write_chart(
        out / "scenario_metrics.svg",
        f"Water metrics — {config.name}",
        [
            ("Potable storage", "storage_end_l", series),
            ("Resident supply", "resident_supply_l", series),
            ("Resident shortage", "resident_shortage_l", series),
            ("Data-center potable withdrawal", "datacenter_potable_withdrawal_l", series),
        ],
    )
    return rows, agent_events, summary
