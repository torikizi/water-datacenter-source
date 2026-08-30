from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path

from water_negotiation_lab.config import load_config
from water_negotiation_lab.game_view import _compact_dataset
from water_negotiation_lab.providers import (
    MockProvider,
    _AuthorizationSafeRedirectHandler,
)
from water_negotiation_lab.runner import run_simulation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_transcript_renderer():
    path = PROJECT_ROOT / "scripts" / "render_agent_transcript.py"
    spec = importlib.util.spec_from_file_location("render_agent_transcript", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load transcript renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InterfaceAndSecurityTests(unittest.TestCase):
    def test_agent_run_must_match_visible_agent_off_physics(self) -> None:
        game_config = load_config(PROJECT_ROOT / "configs" / "inzai_chiba_new_town.toml")
        agent_config = load_config(PROJECT_ROOT / "examples" / "ds4_agent_summer.toml")
        agent_config.facilities[0].wue_l_per_kwh = 9.0
        with tempfile.TemporaryDirectory() as directory:
            agent_run = Path(directory) / "agent-run"
            run_simulation(agent_config, agent_run, MockProvider())
            with self.assertRaisesRegex(ValueError, "physical configuration.*facilities"):
                _compact_dataset(game_config, agent_run)

    def test_cross_origin_redirect_strips_authorization(self) -> None:
        handler = _AuthorizationSafeRedirectHandler()
        request = urllib.request.Request(
            "https://ds4.example/v1/chat/completions",
            data=b"{}",
            headers={
                "Authorization": "Bearer secret",
                "Proxy-Authorization": "Basic secret",
                "X-Trace": "kept",
            },
            method="POST",
        )
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.example/collect",
        )
        self.assertIsNotNone(redirected)
        assert redirected is not None
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertIsNone(redirected.get_header("Proxy-Authorization"))
        self.assertIn(("X-trace", "kept"), redirected.header_items())

    def test_same_origin_redirect_keeps_authorization(self) -> None:
        handler = _AuthorizationSafeRedirectHandler()
        request = urllib.request.Request(
            "https://ds4.example/v1/chat/completions",
            data=b"{}",
            headers={"Authorization": "Bearer secret"},
            method="POST",
        )
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://ds4.example/v1/chat/final",
        )
        self.assertIsNotNone(redirected)
        assert redirected is not None
        self.assertEqual(redirected.get_header("Authorization"), "Bearer secret")

    def test_unknown_transcript_role_label_is_html_escaped(self) -> None:
        renderer = _load_transcript_renderer()
        event = {
            "day": 1,
            "date": "2026-08-26",
            "decision_round": 1,
            "decision_reason": "scheduled_review",
            "role": {
                "role": "outside_observer",
                "label_ja": "<img src=x onerror=boom()>",
            },
            "parsed_response": {
                "action": "monitor",
                "message": "ok",
                "reason": "ok",
            },
            "provider_metadata": {"mock": True},
            "valid": True,
        }
        html = renderer.render([event])
        self.assertIn("<h2>${esc(label)}</h2>", html)
        self.assertNotIn("<h2>${label}</h2>", html)

    def test_run_game_rejects_incomplete_agent_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(
                [
                    str(PROJECT_ROOT / "scripts" / "run_game.sh"),
                    str(root / "game.html"),
                    str(root / "missing-agent-run"),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("requires a complete audited agent run", result.stderr)
            self.assertFalse((root / "game.html").exists())

    def test_submission_build_custom_output_keeps_audited_agent_game(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "fresh-submission"
            audit_source = root / "summer-agent-audit"
            agent_config = load_config(
                PROJECT_ROOT / "examples" / "ds4_agent_summer.toml"
            )
            run_simulation(agent_config, audit_source, MockProvider())
            result = subprocess.run(
                [
                    str(PROJECT_ROOT / "scripts" / "build_submission_artifacts.sh"),
                    str(output),
                    str(audit_source),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            game = (output / "inzai-water-game.html").read_text(encoding="utf-8")
            self.assertIn('"default":"agent_decision_season"', game)
            self.assertIn('"agentMode":true', game)
            copied_audit = output / audit_source.name
            for name in ("water_balance.jsonl", "llm_messages.jsonl", "summary.json"):
                self.assertEqual(
                    (copied_audit / name).read_bytes(),
                    (
                        audit_source / name
                    ).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
