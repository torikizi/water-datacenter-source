from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from water_negotiation_lab.config import load_config
from water_negotiation_lab.providers import (
    DS4ChatProvider,
    MockProvider,
    ProviderRequestError,
)
from water_negotiation_lab.runtime import (
    configured_provider,
    load_dotenv,
    provider_settings,
    run_with_optional_fallback,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _UnavailableProvider:
    def complete(self, messages, *, seed):
        raise ProviderRequestError("DS4 request failed: connection refused")


class _FakeHTTPResponse:
    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


class _RecordingOpener:
    def __init__(self) -> None:
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return _FakeHTTPResponse(
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"monitor","message":"確認","reason":"監視"}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 12},
            }
        )


class RuntimeTests(unittest.TestCase):
    def test_dotenv_loads_only_supported_keys_without_executing_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "must-not-exist"
            dotenv = root / ".env"
            dotenv.write_text(
                "\n".join(
                    [
                        "# local settings",
                        "export DS4_BASE_URL='http://127.0.0.1:8000/v1'",
                        "DS4_MODEL=deepseek-v4-flash # comment",
                        f"DS4_API_KEY=$(touch {marker})",
                        "UNRELATED_SETTING=ignored",
                    ]
                ),
                encoding="utf-8",
            )
            environment = {"DS4_MODEL": "process-wins"}
            discovered = load_dotenv(dotenv, environ=environment)

            self.assertEqual(
                discovered, {"DS4_BASE_URL", "DS4_MODEL", "DS4_API_KEY"}
            )
            self.assertEqual(environment["DS4_MODEL"], "process-wins")
            self.assertEqual(environment["DS4_API_KEY"], f"$(touch {marker})")
            self.assertNotIn("UNRELATED_SETTING", environment)
            self.assertFalse(marker.exists())

    def test_auto_without_ds4_configuration_selects_mock_immediately(self) -> None:
        settings = provider_settings("auto", environ={})
        provider = configured_provider(settings)
        self.assertIsInstance(provider, MockProvider)
        response = provider.complete(
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "role": "water_utility",
                            "numeric_state": {
                                "resident_shortage_l": 0,
                                "storage_fraction": 1,
                                "datacenter_water_shortage_l": 0,
                                "datacenter_potable_restriction_multiplier": 1,
                            },
                            "fixed_policy_thresholds": {
                                "restriction_trigger": 0.25,
                                "lift_trigger": 0.55,
                            },
                        }
                    ),
                }
            ],
            seed=1,
        )
        self.assertEqual(response.metadata["selection_reason"], "ds4_not_configured")
        self.assertFalse(response.metadata["fallback_used"])

    def test_ds4_provider_sends_the_configured_openai_compatible_request(self) -> None:
        provider = DS4ChatProvider(
            base_url="http://127.0.0.1:8000/v1",
            model="deepseek-v4-flash",
            timeout_seconds=7.5,
            api_key="local-secret",
        )
        opener = _RecordingOpener()
        provider._opener = opener
        response = provider.complete(
            [{"role": "user", "content": "decide"}], seed=42
        )

        self.assertIsNotNone(opener.request)
        request = opener.request
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer local-secret")
        self.assertEqual(opener.timeout, 7.5)
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_tokens"], 300)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertFalse(payload["stream"])
        self.assertEqual(response.metadata["provider_type"], "ds4")

    def test_auto_ds4_failure_restarts_as_pure_mock_run(self) -> None:
        config = load_config(PROJECT_ROOT / "examples" / "mvp.toml")
        config.days = 1
        settings = provider_settings(
            "auto",
            environ={
                "DS4_BASE_URL": "http://127.0.0.1:1/v1",
                "DS4_MODEL": "deepseek-v4-flash",
                "DS4_TIMEOUT_SECONDS": "0.1",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "water_negotiation_lab.runtime.configured_provider",
                return_value=_UnavailableProvider(),
            ):
                _, events, _, status = run_with_optional_fallback(
                    config, directory, settings
                )
            saved = [
                json.loads(line)
                for line in (Path(directory) / "llm_messages.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        self.assertTrue(status["fallback_used"])
        self.assertEqual(status["provider"], "mock")
        self.assertEqual(len(events), 4)
        self.assertEqual(len(saved), 4)
        self.assertTrue(
            all(event["provider_metadata"]["provider_type"] == "mock" for event in saved)
        )
        self.assertTrue(
            all(event["provider_metadata"]["fallback_used"] for event in saved)
        )

    def test_api_key_over_remote_plain_http_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-loopback plain HTTP"):
            provider_settings(
                "auto",
                environ={
                    "DS4_BASE_URL": "http://example.com/v1",
                    "DS4_API_KEY": "secret",
                },
            )

    def test_explicit_mock_ignores_unused_invalid_ds4_settings(self) -> None:
        settings = provider_settings(
            "mock",
            environ={
                "DS4_BASE_URL": "not-a-url",
                "DS4_TIMEOUT_SECONDS": "not-a-number",
            },
        )
        self.assertEqual(settings.requested, "mock")
        self.assertIsInstance(configured_provider(settings), MockProvider)

    def test_root_launcher_builds_fresh_game_with_one_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quickstart"
            result = subprocess.run(
                [
                    str(PROJECT_ROOT / "run.sh"),
                    "--provider",
                    "mock",
                    "--no-browser",
                    "--out",
                    str(output),
                ],
                cwd=Path(directory),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "inzai-water-game.html").is_file())
            self.assertTrue((output / "agent-run" / "llm_messages.jsonl").is_file())
            status = json.loads((output / "run-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["provider"], "mock")
            self.assertFalse(status["fallback_used"])
            self.assertIn("Interactive view:", result.stdout)


if __name__ == "__main__":
    unittest.main()
