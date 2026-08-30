from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .config import AgentConfig
from .providers import LLMProvider


DECISION_TEXT_MAX_CHARS = 240
DECISION_TEXT_NORMALIZATION_LIMIT_CHARS = 1024


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    role: str
    label_ja: str
    decision_phase: str
    objective: str
    allowed_actions: tuple[str, ...]


ROLES = (
    RoleDefinition(
        "resident_representative",
        "住民代表",
        "stakeholder_input",
        "住民の飲用可能な上水供給と情報の透明性を守る",
        ("request_restriction", "request_information", "monitor", "no_action"),
    ),
    RoleDefinition(
        "water_utility",
        "水道事業者",
        "stakeholder_input",
        "計算済みの供給能力と不足を正確に関係者へ伝える",
        ("warn_shortage", "report_stable", "request_conservation", "no_action"),
    ),
    RoleDefinition(
        "datacenter_operator",
        "データセンター事業者",
        "stakeholder_input",
        "施設運用を考慮しつつ代替水源や制限条件を交渉する",
        ("propose_reclaimed_water", "request_supply", "acknowledge", "no_action"),
    ),
    RoleDefinition(
        "municipality",
        "自治体",
        "policy_resolution",
        "同じ会議の3者の発言を踏まえ、固定された権限・閾値の範囲で公益と供給継続性を調整する",
        ("enact_dc_restriction", "lift_dc_restriction", "maintain_policy", "no_action"),
    ),
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError(f"duplicate JSON key: {key}")
        parsed[key] = value
    return parsed


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1])
    parsed = json.loads(candidate, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(parsed, dict):
        raise ValueError("response must be a JSON object")
    return parsed


def _validate_decision_structure(
    parsed: dict[str, Any], allowed_actions: tuple[str, ...] | list[str]
) -> list[str]:
    errors: list[str] = []
    if set(parsed) != {"action", "message", "reason"}:
        errors.append("response must contain only action, message, and reason")
    if parsed.get("action") not in allowed_actions:
        errors.append("action is not allowed for this role")
    for field in ("message", "reason"):
        if not isinstance(parsed.get(field), str) or not parsed.get(field, "").strip():
            errors.append(f"{field} must be a non-empty string")
        else:
            try:
                parsed[field].encode("utf-8")
            except UnicodeEncodeError:
                errors.append(f"{field} must contain valid Unicode text")
    return errors


def _normalize_decision_text(
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    normalized = dict(parsed)
    normalizations: list[dict[str, Any]] = []
    errors: list[str] = []
    for field in ("message", "reason"):
        value = normalized[field].strip()
        original_length = len(value)
        if original_length > DECISION_TEXT_NORMALIZATION_LIMIT_CHARS:
            errors.append(
                f"{field} exceeds the {DECISION_TEXT_NORMALIZATION_LIMIT_CHARS}-character "
                "normalization safety limit"
            )
            continue
        if original_length > DECISION_TEXT_MAX_CHARS:
            value = value[: DECISION_TEXT_MAX_CHARS - 1].rstrip() + "…"
            normalizations.append(
                {
                    "field": field,
                    "rule": "truncate_with_ellipsis",
                    "original_length": original_length,
                    "normalized_length": len(value),
                }
            )
        normalized[field] = value
    return normalized, normalizations, errors


def parse_decision_response(
    text: str,
    allowed_actions: tuple[str, ...] | list[str],
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Parse an agent response while preserving strict action safety.

    Structurally invalid JSON, missing or extra fields, invalid actions, and
    unusable text remain fatal.  Only otherwise-valid overlong presentation
    text is deterministically shortened; callers must retain ``text`` as the
    unmodified audit record.
    """
    try:
        parsed = _extract_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return {}, [str(exc)], []
    errors = _validate_decision_structure(parsed, allowed_actions)
    if errors:
        return parsed, errors, []
    normalized, normalizations, normalization_errors = _normalize_decision_text(parsed)
    return normalized, normalization_errors, normalizations


class AgentCoordinator:
    def __init__(self, provider: LLMProvider, config: AgentConfig, seed: int) -> None:
        self.provider = provider
        self.config = config
        self.seed = seed
        self.history: list[dict[str, Any]] = []

    def _numeric_state(self, row: dict[str, Any]) -> dict[str, float | int | bool]:
        capacity = float(row["storage_capacity_l"])
        return {
            "day": int(row["day"]),
            "storage_end_l": float(row["storage_end_l"]),
            "storage_capacity_l": capacity,
            "storage_fraction": float(row["storage_end_l"]) / capacity,
            "source_inflow_l": float(row["source_inflow_l"]),
            "resident_demand_l": float(row["resident_demand_l"]),
            "resident_supply_l": float(row["resident_supply_l"]),
            "resident_shortage_l": float(row["resident_shortage_l"]),
            "datacenter_direct_water_requirement_l": float(
                row["datacenter_direct_water_requirement_l"]
            ),
            "datacenter_potable_requirement_l": float(
                row["datacenter_potable_requirement_l"]
            ),
            "datacenter_potable_withdrawal_l": float(
                row["datacenter_potable_withdrawal_l"]
            ),
            "datacenter_reclaimed_withdrawal_l": float(
                row["datacenter_reclaimed_withdrawal_l"]
            ),
            "datacenter_onsite_storage_end_l": float(
                row["datacenter_onsite_storage_end_l"]
            ),
            "datacenter_onsite_storage_capacity_l": float(
                row["datacenter_onsite_storage_capacity_l"]
            ),
            "datacenter_water_shortage_l": float(row["datacenter_water_shortage_l"]),
            "datacenter_potable_restriction_multiplier": float(
                row["datacenter_potable_restriction_multiplier"]
            ),
            "observed_precipitation_mm": float(
                row.get("observed_precipitation_mm", 0.0)
            ),
            "observed_precipitation_used_as_inflow": bool(
                row.get("observed_precipitation_used_as_inflow", False)
            ),
            "observed_reservoir_reference_storage_l": float(
                row.get("observed_reservoir_reference_storage_l", 0.0)
            ),
            "observed_reservoir_reference_capacity_l": float(
                row.get("observed_reservoir_reference_capacity_l", 0.0)
            ),
            "observed_reservoir_reference_storage_fraction": float(
                row.get("observed_reservoir_reference_storage_fraction", 0.0)
            ),
            "observed_reservoir_reference_interpolated": bool(
                row.get("observed_reservoir_reference_interpolated", False)
            ),
            "observed_reservoir_used_for_allocation": bool(
                row.get("observed_reservoir_used_for_allocation", False)
            ),
        }

    def decide(
        self,
        row: dict[str, Any],
        *,
        decision_round: int,
        decision_reason: str,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        recent_history = self.history[-self.config.history_limit :] if self.config.history_limit else []
        for role_index, role in enumerate(ROLES):
            request_body = {
                "role": role.role,
                "role_label": role.label_ja,
                "decision_phase": role.decision_phase,
                "objective": role.objective,
                "allowed_actions": list(role.allowed_actions),
                "numeric_state": self._numeric_state(row),
                "past_messages": recent_history,
                "fixed_policy_thresholds": {
                    "restriction_trigger": self.config.restriction_trigger_storage_fraction,
                    "lift_trigger": self.config.lift_trigger_storage_fraction,
                    "restriction_multiplier": self.config.restriction_multiplier,
                },
            }
            messages = [
                {
                    "role": "system",
                    "content": (
                        "あなたは水資源シミュレーション内の意思決定者です。"
                        "入力数値はPythonが確定した事実として引用のみ可能です。"
                        "計算、推定、数値の変更、未提示の事実の生成は禁止です。"
                        "observed_で始まる値は参考観測であり、used_as_inflowまたは"
                        "used_for_allocationがfalseなら供給可能量として扱ってはいけません。"
                        "past_messagesに同じ会議の先行発言があれば判断材料にしてください。"
                        "出力はJSONオブジェクト1個だけにしてください。コードフェンスは禁止です。"
                        "キーはaction、message、reasonの3個だけで、roleなどを追加してはいけません。"
                        "3個の値はすべて空でない文字列にしてください。"
                        "messageは日本語120文字以内、reasonは80文字以内の各1文にしてください。"
                        "重要な数値は最大2項目だけ引用し、状態を列挙しないでください。"
                        "形式例: {\"action\":\"no_action\",\"message\":\"現状を確認しました\","
                        "\"reason\":\"提示された状態を確認したため\"}"
                    ),
                },
                {"role": "user", "content": json.dumps(request_body, ensure_ascii=False)},
            ]
            provider_response = self.provider.complete(
                messages, seed=self.seed + int(row["day"]) * 10 + role_index
            )
            parsed, errors, normalizations = parse_decision_response(
                provider_response.text, role.allowed_actions
            )
            finish_reason = provider_response.metadata.get("finish_reason")
            if finish_reason not in (None, "stop"):
                errors.append(f"provider finish_reason was {finish_reason!r}, not 'stop'")
            valid = not errors
            strict_valid = valid and not normalizations
            if not valid:
                parsed = {
                    "action": "no_action",
                    "message": "応答検証に失敗したため行動しません",
                    "reason": "; ".join(errors),
                }
            event = {
                "schema_version": 1,
                "scenario": row["scenario"],
                "day": row["day"],
                "date": row["date"],
                "decision_round": decision_round,
                "decision_reason": decision_reason,
                "role": asdict(role),
                "prompt": messages,
                "raw_response": provider_response.text,
                "parsed_response": parsed,
                "valid": valid,
                "strict_valid": strict_valid,
                "validation_errors": errors,
                "normalizations": normalizations,
                "provider_metadata": provider_response.metadata,
            }
            events.append(event)
            self.history.append(
                {
                    "day": row["day"],
                    "role": role.role,
                    "action": parsed["action"],
                    "message": parsed["message"],
                }
            )
            recent_history = self.history[-self.config.history_limit :] if self.config.history_limit else []
        return events


def next_restriction_multiplier(
    current: float,
    events: list[dict[str, Any]],
    config: AgentConfig,
    physical_state: dict[str, Any],
) -> float:
    """Apply an authorized municipal action against Python-computed state.

    A schema-valid LLM response is only a proposal.  The fixed policy
    thresholds remain a deterministic Python safety boundary.
    """
    if not config.action_effects_enabled:
        return current
    municipality = next(
        (event for event in events if event["role"]["role"] == "municipality"), None
    )
    if municipality is None or not municipality["valid"]:
        return current
    action = municipality["parsed_response"]["action"]
    capacity = float(physical_state["storage_capacity_l"])
    storage_fraction = float(physical_state["storage_end_l"]) / capacity
    resident_shortage = float(physical_state["resident_shortage_l"])
    if action == "enact_dc_restriction" and (
        resident_shortage > 0
        or storage_fraction < config.restriction_trigger_storage_fraction
    ):
        return config.restriction_multiplier
    if (
        action == "lift_dc_restriction"
        and current < 1.0
        and storage_fraction >= config.lift_trigger_storage_fraction
    ):
        return 1.0
    return current
