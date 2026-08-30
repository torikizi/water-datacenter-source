from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from .comparison import run_comparison
from .config import load_config
from .game_view import write_game_view
from .providers import DS4ChatProvider, MockProvider
from .reporting import write_json
from .runner import run_simulation
from .runtime import load_dotenv, provider_settings, run_with_optional_fallback


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="water-lab",
        description="Deterministic water balance with optional decision-only local LLM agents",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    simulate = subparsers.add_parser("simulate", help="run one configured scenario")
    simulate.add_argument("--config", required=True, type=Path)
    simulate.add_argument("--out", required=True, type=Path)
    simulate.add_argument(
        "--provider", choices=("none", "mock", "ds4", "auto"), default="mock"
    )
    simulate.add_argument("--ds4-base-url")
    simulate.add_argument("--ds4-model")
    simulate.add_argument("--ds4-timeout", type=float)

    compare = subparsers.add_parser("compare", help="run the fixed MVP comparison matrix")
    compare.add_argument("--config", required=True, type=Path)
    compare.add_argument("--out", required=True, type=Path)

    game = subparsers.add_parser("game", help="generate a game-like interactive simulation view")
    game.add_argument("--config", required=True, type=Path)
    game.add_argument("--out", required=True, type=Path)
    game.add_argument(
        "--agent-run",
        type=Path,
        help="optional audited agent-run directory to embed in the game",
    )
    game.add_argument("--fragment", action="store_true", help=argparse.SUPPRESS)

    quickstart = subparsers.add_parser(
        "quickstart",
        help="run the summer simulation and build the interactive view",
    )
    quickstart.add_argument("--out", type=Path, default=Path("outputs/quickstart"))
    quickstart.add_argument(
        "--provider",
        choices=("auto", "mock", "ds4"),
        help="default: WATER_LAB_PROVIDER or auto",
    )
    quickstart.add_argument("--no-browser", action="store_true")
    return parser


def _settings_from_args(args: argparse.Namespace):
    environment = dict(os.environ)
    if getattr(args, "ds4_base_url", None) is not None:
        environment["DS4_BASE_URL"] = args.ds4_base_url
    if getattr(args, "ds4_model", None) is not None:
        environment["DS4_MODEL"] = args.ds4_model
    if getattr(args, "ds4_timeout", None) is not None:
        environment["DS4_TIMEOUT_SECONDS"] = str(args.ds4_timeout)
    return provider_settings(getattr(args, "provider", None), environ=environment)


def _run_quickstart(args: argparse.Namespace) -> int:
    output_root = args.out
    agent_output = output_root / "agent-run"
    game_output = output_root / "inzai-water-game.html"
    status_output = output_root / "run-status.json"
    settings = _settings_from_args(args)
    if settings.requested == "none":
        raise ValueError("quickstart requires auto, mock, or ds4 so the 4-agent log exists")

    if settings.requested == "auto" and settings.ds4_configured:
        print(
            f"[water-lab] DS4 configured: {settings.model} at {settings.base_url}",
            flush=True,
        )
        print(
            "[water-lab] If DS4 is unavailable, the complete run will restart with MockProvider.",
            flush=True,
        )
    elif settings.requested == "auto":
        print(
            "[water-lab] DS4 is not configured; using deterministic MockProvider.",
            flush=True,
        )
    else:
        print(
            f"[water-lab] Provider requested explicitly: {settings.requested}",
            flush=True,
        )

    agent_config = load_config(PROJECT_ROOT / "examples" / "ds4_agent_summer.toml")
    _, events, summary, runtime_status = run_with_optional_fallback(
        agent_config,
        agent_output,
        settings,
    )
    game_config = load_config(PROJECT_ROOT / "configs" / "inzai_chiba_new_town.toml")
    write_game_view(game_config, game_output, agent_run_dir=agent_output)

    runtime_status.update(
        {
            "scenario": summary["scenario"],
            "decision_count": len(events),
            "valid_decision_count": sum(bool(event["valid"]) for event in events),
            "game_view": str(game_output),
            "agent_run": str(agent_output),
            "api_key_logged": False,
        }
    )
    summary["runtime_provider"] = runtime_status
    write_json(agent_output / "summary.json", summary)
    browser_opened = False
    if not args.no_browser:
        browser_opened = bool(webbrowser.open(game_output.resolve().as_uri()))
    runtime_status["browser_opened"] = browser_opened
    write_json(status_output, runtime_status)

    print(f"[water-lab] LLM: {runtime_status['provider_label']}")
    if runtime_status["fallback_used"]:
        print(
            f"[water-lab] DS4 fallback reason: {runtime_status['fallback_reason']}",
            file=sys.stderr,
        )
    print(f"[water-lab] Interactive view: {game_output.resolve()}")
    if not args.no_browser and not browser_opened:
        print("[water-lab] Browser could not be opened automatically; open the HTML path above.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        load_dotenv(PROJECT_ROOT / ".env")
    except ValueError as exc:
        parser.error(str(exc))
    args = parser.parse_args(argv)
    if args.command == "quickstart":
        try:
            return _run_quickstart(args)
        except ValueError as exc:
            parser.error(str(exc))

    config = load_config(args.config)
    if args.command == "game":
        write_game_view(
            config,
            args.out,
            fragment=args.fragment,
            agent_run_dir=args.agent_run,
        )
        print(json.dumps({"game_view": str(args.out), "fragment": args.fragment}, ensure_ascii=False))
        return 0
    if args.command == "compare":
        result = run_comparison(config, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.provider == "auto":
        settings = _settings_from_args(args)
        _, _, summary, status = run_with_optional_fallback(config, args.out, settings)
        summary["runtime_provider"] = status
        write_json(args.out / "summary.json", summary)
    else:
        provider = None
        if args.provider == "mock":
            provider = MockProvider()
        elif args.provider == "ds4":
            settings = _settings_from_args(args)
            provider = DS4ChatProvider(
                base_url=settings.base_url,
                model=settings.model,
                timeout_seconds=settings.timeout_seconds,
                api_key=settings.api_key,
            )
        _, _, summary = run_simulation(config, args.out, provider=provider)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
