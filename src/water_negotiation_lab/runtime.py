from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, MutableMapping

from .config import SimulationConfig
from .providers import DS4ChatProvider, LLMProvider, MockProvider, ProviderRequestError
from .runner import run_simulation


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUPPORTED_ENV_KEYS = {
    "DS4_API_KEY",
    "DS4_BASE_URL",
    "DS4_MODEL",
    "DS4_TIMEOUT_SECONDS",
    "WATER_LAB_PROVIDER",
}


def _dotenv_value(raw: str, *, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote = value[0]
        escaped = False
        end = None
        for index, character in enumerate(value[1:], start=1):
            if quote == '"' and character == "\\" and not escaped:
                escaped = True
                continue
            if character == quote and not escaped:
                end = index
                break
            escaped = False
        if end is None:
            raise ValueError(f".env line {line_number}: unterminated quoted value")
        trailing = value[end + 1 :].strip()
        if trailing and not trailing.startswith("#"):
            raise ValueError(f".env line {line_number}: unexpected text after value")
        inner = value[1:end]
        if quote == '"':
            inner = (
                inner.replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return inner
    comment = re.search(r"\s+#", value)
    if comment:
        value = value[: comment.start()].rstrip()
    return value


def load_dotenv(
    path: str | Path,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> set[str]:
    """Load the supported local settings without executing the file as shell code.

    Existing process environment variables take precedence. Unsupported keys are
    ignored so a repository-local .env cannot silently change unrelated process
    behavior.
    """

    target = Path(path)
    if not target.is_file():
        return set()
    environment = os.environ if environ is None else environ
    discovered: set[str] = set()
    for line_number, raw_line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f".env line {line_number}: expected KEY=VALUE")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY.fullmatch(key):
            raise ValueError(f".env line {line_number}: invalid key")
        if key not in _SUPPORTED_ENV_KEYS:
            continue
        discovered.add(key)
        environment.setdefault(key, _dotenv_value(raw_value, line_number=line_number))
    return discovered


@dataclass(slots=True)
class ProviderSettings:
    requested: str
    ds4_configured: bool
    base_url: str
    model: str
    timeout_seconds: float
    api_key: str | None


def provider_settings(
    requested: str | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> ProviderSettings:
    environment = os.environ if environ is None else environ
    selected = (requested or environment.get("WATER_LAB_PROVIDER", "auto")).strip().lower()
    if selected not in {"auto", "mock", "ds4", "none"}:
        raise ValueError("provider must be auto, mock, ds4, or none")
    base_url_value = environment.get("DS4_BASE_URL", "").strip()
    base_url = base_url_value or "http://127.0.0.1:8000/v1"
    uses_ds4 = selected == "ds4" or (selected == "auto" and bool(base_url_value))
    parsed_url = urllib.parse.urlsplit(base_url)
    if uses_ds4:
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "DS4_BASE_URL must be an http(s) URL without credentials, query, or fragment"
            )
    model = environment.get("DS4_MODEL", "deepseek-v4-flash").strip()
    if not model:
        model = "deepseek-v4-flash"
    timeout_text = environment.get("DS4_TIMEOUT_SECONDS", "120").strip() or "120"
    if uses_ds4:
        try:
            timeout_seconds = float(timeout_text)
        except ValueError as exc:
            raise ValueError("DS4_TIMEOUT_SECONDS must be a number") from exc
        if timeout_seconds <= 0:
            raise ValueError("DS4_TIMEOUT_SECONDS must be greater than zero")
    else:
        timeout_seconds = 120.0
    api_key = environment.get("DS4_API_KEY") or None
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if (
        uses_ds4
        and api_key
        and parsed_url.scheme == "http"
        and parsed_url.hostname not in loopback_hosts
    ):
        raise ValueError("DS4_API_KEY cannot be sent over non-loopback plain HTTP")
    return ProviderSettings(
        requested=selected,
        ds4_configured=bool(base_url_value),
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
        api_key=api_key,
    )


def configured_provider(settings: ProviderSettings) -> LLMProvider | None:
    if settings.requested == "none":
        return None
    if settings.requested == "mock":
        return MockProvider()
    if settings.requested == "auto" and not settings.ds4_configured:
        return MockProvider(
            metadata={
                "selection_mode": "auto",
                "selection_reason": "ds4_not_configured",
                "fallback_used": False,
            }
        )
    return DS4ChatProvider(
        base_url=settings.base_url,
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
        api_key=settings.api_key,
        metadata={"selection_mode": settings.requested},
    )


def _fallback_reason(error: ProviderRequestError) -> str:
    text = str(error).replace("\n", " ").strip()
    return text[:400] if text else type(error).__name__


def run_with_optional_fallback(
    config: SimulationConfig,
    output_dir: str | Path,
    settings: ProviderSettings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Run once with the selected provider, restarting fully on DS4 I/O failure."""

    provider = configured_provider(settings)
    try:
        rows, events, summary = run_simulation(config, output_dir, provider=provider)
    except ProviderRequestError as error:
        if settings.requested != "auto" or not settings.ds4_configured:
            raise
        reason = _fallback_reason(error)
        fallback = MockProvider(
            metadata={
                "selection_mode": "auto",
                "selection_reason": "ds4_request_failed",
                "fallback_used": True,
                "fallback_from": "ds4",
                "fallback_reason": reason,
                "requested_model": settings.model,
            }
        )
        rows, events, summary = run_simulation(config, output_dir, provider=fallback)
        status = {
            "provider": "mock",
            "provider_label": "MockProvider (DS4 fallback)",
            "ds4_configured": True,
            "fallback_used": True,
            "fallback_reason": reason,
            "model": settings.model,
        }
        return rows, events, summary, status

    if settings.requested == "auto" and not settings.ds4_configured:
        provider_name = "mock"
        provider_label = "MockProvider (DS4 not configured)"
    elif settings.requested == "mock":
        provider_name = "mock"
        provider_label = "MockProvider"
    elif settings.requested == "none":
        provider_name = "none"
        provider_label = "LLM disabled"
    else:
        provider_name = "ds4"
        provider_label = f"DS4 / {settings.model}"
    status = {
        "provider": provider_name,
        "provider_label": provider_label,
        "ds4_configured": settings.ds4_configured,
        "fallback_used": False,
        "model": settings.model if provider_name == "ds4" else None,
    }
    return rows, events, summary, status
