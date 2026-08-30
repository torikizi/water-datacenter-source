#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

OUTPUT_DIR=${1:-submission_artifacts/ds4_agent_demo}

PYTHONPATH=src python3 -m water_negotiation_lab simulate \
  --config examples/ds4_smoke.toml \
  --out "$OUTPUT_DIR" \
  --provider ds4 \
  --ds4-base-url http://127.0.0.1:8000/v1 \
  --ds4-model deepseek-v4-flash \
  --ds4-timeout 120

python3 scripts/render_agent_transcript.py \
  "$OUTPUT_DIR/llm_messages.jsonl" \
  "$OUTPUT_DIR/agent-council.html" \
  --water-balance "$OUTPUT_DIR/water_balance.jsonl"
