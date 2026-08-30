#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

OUTPUT_FILE=${1:-outputs/inzai-water-game.html}
if [ "$#" -ge 2 ]; then
  AGENT_RUN_DIR=$2
elif [ -f submission_artifacts/ds4_agent_summer_demo_v3/water_balance.jsonl ] && \
     [ -f submission_artifacts/ds4_agent_summer_demo_v3/llm_messages.jsonl ] && \
     [ -f submission_artifacts/ds4_agent_summer_demo_v3/summary.json ]; then
  AGENT_RUN_DIR=submission_artifacts/ds4_agent_summer_demo_v3
else
  AGENT_RUN_DIR=submission_artifacts/mock_agent_summer_demo
  printf '%s\n' "Summer DS4 audit not found; using the clearly labeled MockProvider run." >&2
fi

missing=0
for required_file in water_balance.jsonl llm_messages.jsonl summary.json; do
  if [ ! -f "$AGENT_RUN_DIR/$required_file" ]; then
    printf '%s\n' "Missing audited agent artifact: $AGENT_RUN_DIR/$required_file" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  printf '%s\n' "Game generation requires a complete audited agent run." >&2
  exit 2
fi

PYTHONPATH=src python3 -m water_negotiation_lab game \
  --config configs/inzai_chiba_new_town.toml \
  --agent-run "$AGENT_RUN_DIR" \
  --out "$OUTPUT_FILE"
