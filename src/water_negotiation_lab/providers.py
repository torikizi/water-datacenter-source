from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ProviderResponse:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], *, seed: int) -> ProviderResponse: ...


class ProviderRequestError(RuntimeError):
    """A provider transport or response-envelope failure safe to fall back from."""


class MockProvider:
    """Deterministic rule-backed provider used for tests and offline runs."""

    def __init__(self, *, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = dict(metadata or {})

    def complete(self, messages: list[dict[str, str]], *, seed: int) -> ProviderResponse:
        request = json.loads(messages[-1]["content"])
        state = request["numeric_state"]
        role = request["role"]
        resident_shortage = float(state["resident_shortage_l"])
        storage_fraction = float(state["storage_fraction"])
        dc_shortage = float(state["datacenter_water_shortage_l"])
        restriction = float(state["datacenter_potable_restriction_multiplier"])
        trigger = float(request["fixed_policy_thresholds"]["restriction_trigger"])
        lift = float(request["fixed_policy_thresholds"]["lift_trigger"])

        if role == "resident_representative":
            if resident_shortage > 0 or storage_fraction < trigger:
                result = {
                    "action": "request_restriction",
                    "message": "住民向け上水を守るため冷却用上水の制限を求めます",
                    "reason": "住民不足または低貯水の判定条件に達したため",
                }
            else:
                result = {
                    "action": "monitor",
                    "message": "住民供給と貯水量の推移を監視します",
                    "reason": "現時点で住民不足が記録されていないため",
                }
        elif role == "municipality":
            if (resident_shortage > 0 or storage_fraction < trigger) and restriction >= 1.0:
                result = {
                    "action": "enact_dc_restriction",
                    "message": "固定ルールに基づく冷却用上水制限を発動します",
                    "reason": "決定済みの不足・貯水トリガーに該当したため",
                }
            elif restriction < 1.0 and storage_fraction >= lift:
                result = {
                    "action": "lift_dc_restriction",
                    "message": "固定ルールに基づき冷却用上水制限を解除します",
                    "reason": "解除用の貯水トリガーを満たしたため",
                }
            else:
                result = {
                    "action": "maintain_policy",
                    "message": "現在の供給政策を維持します",
                    "reason": "政策変更トリガーに該当しないため",
                }
        elif role == "water_utility":
            if resident_shortage > 0 or storage_fraction < trigger:
                result = {
                    "action": "warn_shortage",
                    "message": "供給余力の低下を関係者へ通知します",
                    "reason": "計算済みの不足または低貯水を確認したため",
                }
            else:
                result = {
                    "action": "report_stable",
                    "message": "本日の計算上の供給状況を共有します",
                    "reason": "住民不足が記録されていないため",
                }
        else:
            if dc_shortage > 0:
                result = {
                    "action": "propose_reclaimed_water",
                    "message": "再生水への切替可能性を協議したいです",
                    "reason": "計算上の施設用水不足が発生したため",
                }
            else:
                result = {
                    "action": "acknowledge",
                    "message": "現在の供給条件を確認しました",
                    "reason": "計算上の施設用水不足がないため",
                }
        return ProviderResponse(
            text=json.dumps(result, ensure_ascii=False),
            metadata={
                **self.metadata,
                "provider_type": "mock",
                "mock": True,
            },
        )


def _url_origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urllib.parse.urlsplit(url)
    default_port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
    return parsed.scheme.lower(), parsed.hostname, parsed.port or default_port


class _AuthorizationSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep credentials on the configured DS4 origin during redirects."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(
            request, fp, code, message, headers, new_url
        )
        if redirected is not None and _url_origin(request.full_url) != _url_origin(new_url):
            redirected.remove_header("Authorization")
            redirected.remove_header("Proxy-Authorization")
        return redirected


class DS4ChatProvider:
    """Small dependency-free client for ds4-server's OpenAI-compatible chat API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000/v1",
        model: str = "deepseek-v4-flash",
        max_tokens: int = 300,
        timeout_seconds: float = 120.0,
        api_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key
        self.metadata = dict(metadata or {})
        self._opener = urllib.request.build_opener(_AuthorizationSafeRedirectHandler())

    def complete(self, messages: list[dict[str, str]], *, seed: int) -> ProviderResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "seed": seed,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ProviderRequestError(f"DS4 request failed: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError(
                "DS4 response did not contain chat completion content"
            ) from exc
        if not isinstance(content, str):
            raise ProviderRequestError("DS4 response content was not a string")
        return ProviderResponse(
            text=content,
            metadata={
                **self.metadata,
                "provider_type": "ds4",
                "model": body.get("model", self.model),
                "usage": body.get("usage", {}),
                "finish_reason": body.get("choices", [{}])[0].get("finish_reason"),
            },
        )
