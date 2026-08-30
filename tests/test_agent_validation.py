from __future__ import annotations

import json
import unittest

from water_negotiation_lab.agents import (
    DECISION_TEXT_MAX_CHARS,
    DECISION_TEXT_NORMALIZATION_LIMIT_CHARS,
    AgentCoordinator,
    parse_decision_response,
)
from water_negotiation_lab.config import AgentConfig
from water_negotiation_lab.providers import ProviderResponse


RESIDENT_ACTIONS = (
    "request_restriction",
    "request_information",
    "monitor",
    "no_action",
)


def response(message: str, *, reason: str = "提示状態を確認したため") -> str:
    return json.dumps(
        {
            "action": "request_information",
            "message": message,
            "reason": reason,
        },
        ensure_ascii=False,
    )


class RecordingProvider:
    def __init__(self, resident_text: str) -> None:
        self.resident_text = resident_text
        self.requests: list[dict[str, object]] = []

    def complete(
        self, messages: list[dict[str, str]], *, seed: int
    ) -> ProviderResponse:
        request = json.loads(messages[-1]["content"])
        self.requests.append(request)
        role = request["role"]
        if role == "resident_representative":
            text = self.resident_text
        elif role == "water_utility":
            text = json.dumps(
                {
                    "action": "report_stable",
                    "message": "供給状況を共有します",
                    "reason": "提示状態を確認したため",
                },
                ensure_ascii=False,
            )
        elif role == "datacenter_operator":
            text = json.dumps(
                {
                    "action": "acknowledge",
                    "message": "供給条件を確認しました",
                    "reason": "提示状態を確認したため",
                },
                ensure_ascii=False,
            )
        else:
            text = json.dumps(
                {
                    "action": "maintain_policy",
                    "message": "現在の政策を維持します",
                    "reason": "変更条件に該当しないため",
                },
                ensure_ascii=False,
            )
        return ProviderResponse(text=text, metadata={"finish_reason": "stop"})


def sample_row() -> dict[str, object]:
    return {
        "scenario": "validation_test",
        "day": 1,
        "date": "2026-01-01",
        "storage_end_l": 50.0,
        "storage_capacity_l": 100.0,
        "source_inflow_l": 20.0,
        "resident_demand_l": 10.0,
        "resident_supply_l": 10.0,
        "resident_shortage_l": 0.0,
        "datacenter_direct_water_requirement_l": 5.0,
        "datacenter_potable_requirement_l": 5.0,
        "datacenter_potable_withdrawal_l": 5.0,
        "datacenter_reclaimed_withdrawal_l": 0.0,
        "datacenter_onsite_storage_end_l": 0.0,
        "datacenter_onsite_storage_capacity_l": 0.0,
        "datacenter_water_shortage_l": 0.0,
        "datacenter_potable_restriction_multiplier": 1.0,
    }


class AgentValidationTests(unittest.TestCase):
    def test_240_characters_remain_strictly_valid(self) -> None:
        message = "水" * DECISION_TEXT_MAX_CHARS
        parsed, errors, normalizations = parse_decision_response(
            response(message), RESIDENT_ACTIONS
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalizations, [])
        self.assertEqual(parsed["message"], message)

    def test_241_characters_are_shortened_without_changing_action(self) -> None:
        message = "水" * (DECISION_TEXT_MAX_CHARS + 1)
        parsed, errors, normalizations = parse_decision_response(
            response(message), RESIDENT_ACTIONS
        )
        self.assertEqual(errors, [])
        self.assertEqual(parsed["action"], "request_information")
        self.assertEqual(len(parsed["message"]), DECISION_TEXT_MAX_CHARS)
        self.assertTrue(parsed["message"].endswith("…"))
        self.assertEqual(normalizations[0]["original_length"], 241)
        self.assertEqual(normalizations[0]["normalized_length"], 240)

    def test_abnormal_length_is_not_silently_normalized(self) -> None:
        message = "水" * (DECISION_TEXT_NORMALIZATION_LIMIT_CHARS + 1)
        _, errors, normalizations = parse_decision_response(
            response(message), RESIDENT_ACTIONS
        )
        self.assertIn("normalization safety limit", errors[0])
        self.assertEqual(normalizations, [])

    def test_structural_and_action_errors_remain_fatal(self) -> None:
        extra = json.dumps(
            {
                "action": "request_information",
                "message": "確認します",
                "reason": "必要なため",
                "role": "resident_representative",
            },
            ensure_ascii=False,
        )
        _, extra_errors, _ = parse_decision_response(extra, RESIDENT_ACTIONS)
        self.assertIn("only action, message, and reason", extra_errors[0])

        invalid_action = response("確認します").replace(
            "request_information", "enact_dc_restriction"
        )
        _, action_errors, _ = parse_decision_response(
            invalid_action, RESIDENT_ACTIONS
        )
        self.assertIn("action is not allowed", action_errors[0])

    def test_duplicate_keys_and_invalid_unicode_are_rejected(self) -> None:
        duplicate = (
            '{"action":"monitor","action":"no_action",'
            '"message":"ok","reason":"ok"}'
        )
        _, duplicate_errors, _ = parse_decision_response(duplicate, RESIDENT_ACTIONS)
        self.assertIn("duplicate JSON key", duplicate_errors[0])

        lone_surrogate = (
            '{"action":"monitor","message":"\\ud800","reason":"ok"}'
        )
        _, unicode_errors, _ = parse_decision_response(
            lone_surrogate, RESIDENT_ACTIONS
        )
        self.assertIn("valid Unicode", unicode_errors[0])

    def test_raw_response_is_preserved_and_normalized_text_enters_history(self) -> None:
        raw = response("水" * 311)
        provider = RecordingProvider(raw)
        coordinator = AgentCoordinator(provider, AgentConfig(history_limit=12), seed=7)
        events = coordinator.decide(
            sample_row(), decision_round=1, decision_reason="scheduled_review"
        )

        resident = events[0]
        self.assertTrue(resident["valid"])
        self.assertFalse(resident["strict_valid"])
        self.assertEqual(resident["raw_response"], raw)
        self.assertEqual(resident["validation_errors"], [])
        self.assertEqual(len(resident["parsed_response"]["message"]), 240)
        self.assertEqual(resident["normalizations"][0]["original_length"], 311)

        water_utility_request = provider.requests[1]
        resident_history = water_utility_request["past_messages"][-1]
        self.assertEqual(resident_history["action"], "request_information")
        self.assertEqual(len(resident_history["message"]), 240)


if __name__ == "__main__":
    unittest.main()
