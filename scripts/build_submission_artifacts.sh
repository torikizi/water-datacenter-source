#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

ARTIFACT_ROOT=${1:-submission_artifacts}

./scripts/run_demo.sh "$ARTIFACT_ROOT/mock_agent_demo" mock
./scripts/run_agent_season.sh "$ARTIFACT_ROOT/mock_agent_summer_demo" mock
./scripts/run_comparison.sh "$ARTIFACT_ROOT/inzai_comparison"

if [ "$#" -ge 2 ]; then
  AGENT_AUDIT_SOURCE=$2
elif [ -f submission_artifacts/ds4_agent_summer_demo_v3/water_balance.jsonl ] && \
     [ -f submission_artifacts/ds4_agent_summer_demo_v3/llm_messages.jsonl ] && \
     [ -f submission_artifacts/ds4_agent_summer_demo_v3/summary.json ]; then
  AGENT_AUDIT_SOURCE=submission_artifacts/ds4_agent_summer_demo_v3
else
  AGENT_AUDIT_SOURCE="$ARTIFACT_ROOT/mock_agent_summer_demo"
  printf '%s\n' "Summer DS4 audit not found; building a clearly labeled MockProvider preview." >&2
fi

missing=0
for required_file in water_balance.jsonl llm_messages.jsonl summary.json; do
  if [ ! -f "$AGENT_AUDIT_SOURCE/$required_file" ]; then
    printf '%s\n' "Missing audited agent artifact: $AGENT_AUDIT_SOURCE/$required_file" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  printf '%s\n' "Submission build requires a complete audited agent run." >&2
  exit 2
fi

mkdir -p "$ARTIFACT_ROOT"
AUDIT_SOURCE_ABS=$(CDPATH= cd -- "$AGENT_AUDIT_SOURCE" && pwd)
ARTIFACT_ROOT_ABS=$(CDPATH= cd -- "$ARTIFACT_ROOT" && pwd)
AGENT_AUDIT_TARGET="$ARTIFACT_ROOT_ABS/$(basename "$AGENT_AUDIT_SOURCE")"
if [ "$AUDIT_SOURCE_ABS" != "$AGENT_AUDIT_TARGET" ]; then
  mkdir -p "$AGENT_AUDIT_TARGET"
  for artifact_file in \
    water_balance.jsonl llm_messages.jsonl summary.json \
    scenario_metrics.svg; do
    if [ -f "$AUDIT_SOURCE_ABS/$artifact_file" ]; then
      cp "$AUDIT_SOURCE_ABS/$artifact_file" "$AGENT_AUDIT_TARGET/$artifact_file"
    fi
  done
fi

python3 scripts/render_agent_transcript.py \
  "$AGENT_AUDIT_TARGET/llm_messages.jsonl" \
  "$AGENT_AUDIT_TARGET/agent-council.html" \
  --water-balance "$AGENT_AUDIT_TARGET/water_balance.jsonl"

./scripts/run_game.sh "$ARTIFACT_ROOT/inzai-water-game.html" "$AGENT_AUDIT_TARGET"

printf '%s\n' "Submission artifacts generated under $ARTIFACT_ROOT"
