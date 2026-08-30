#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

OUTPUT_DIR=${1:-submission_artifacts/ds4_agent_summer_demo_v3}

PYTHONPATH=src python3 -m water_negotiation_lab simulate \
  --config examples/ds4_agent_summer.toml \
  --out "$OUTPUT_DIR" \
  --provider ds4

python3 scripts/render_agent_transcript.py \
  "$OUTPUT_DIR/llm_messages.jsonl" \
  "$OUTPUT_DIR/agent-council.html" \
  --water-balance "$OUTPUT_DIR/water_balance.jsonl"
