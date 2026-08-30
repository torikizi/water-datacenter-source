#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

OUTPUT_DIR=${1:-outputs/agent-summer}
PROVIDER=${2:-mock}

PYTHONPATH=src python3 -m water_negotiation_lab simulate \
  --config examples/ds4_agent_summer.toml \
  --out "$OUTPUT_DIR" \
  --provider "$PROVIDER"

python3 scripts/render_agent_transcript.py \
  "$OUTPUT_DIR/llm_messages.jsonl" \
  "$OUTPUT_DIR/agent-council.html" \
  --water-balance "$OUTPUT_DIR/water_balance.jsonl"
