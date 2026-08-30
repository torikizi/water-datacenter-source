from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .comparison import run_comparison
from .config import load_config
from .game_view import write_game_view
from .providers import DS4ChatProvider, MockProvider
from .runner import run_simulation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="water-lab",
        description="Deterministic water balance with optional decision-only local LLM agents",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    simulate = subparsers.add_parser("simulate", help="run one configured scenario")
    simulate.add_argument("--config", required=True, type=Path)
    simulate.add_argument("--out", required=True, type=Path)
    simulate.add_argument("--provider", choices=("none", "mock", "ds4"), default="mock")
    simulate.add_argument("--ds4-base-url", default="http://127.0.0.1:8000/v1")
    simulate.add_argument("--ds4-model", default="deepseek-v4-flash")
    simulate.add_argument("--ds4-timeout", type=float, default=120.0)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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

    provider = None
    if args.provider == "mock":
        provider = MockProvider()
    elif args.provider == "ds4":
        provider = DS4ChatProvider(
            base_url=args.ds4_base_url,
            model=args.ds4_model,
            timeout_seconds=args.ds4_timeout,
            api_key=os.environ.get("DS4_API_KEY"),
        )
    _, _, summary = run_simulation(config, args.out, provider=provider)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
