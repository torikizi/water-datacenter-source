#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

OUTPUT_ROOT=${1:-outputs/summer-preview}

./scripts/run_comparison.sh "$OUTPUT_ROOT/inzai_comparison"
./scripts/run_agent_season.sh "$OUTPUT_ROOT/mock_agent_summer" mock
./scripts/run_game.sh \
  "$OUTPUT_ROOT/inzai-water-game.html" \
  "$OUTPUT_ROOT/mock_agent_summer"

printf '%s\n' "Summer preview generated under $OUTPUT_ROOT"
