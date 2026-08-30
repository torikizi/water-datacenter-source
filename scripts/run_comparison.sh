#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

OUTPUT_DIR=${1:-outputs/inzai-comparison}

PYTHONPATH=src python3 -m water_negotiation_lab compare \
  --config configs/inzai_chiba_new_town.toml \
  --out "$OUTPUT_DIR"

python3 scripts/export_submission_table.py \
  "$OUTPUT_DIR/comparison_summary.json" \
  "$OUTPUT_DIR/scenario_comparison.csv"

