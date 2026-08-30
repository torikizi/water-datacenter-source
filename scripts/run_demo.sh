#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

OUTPUT_DIR=${1:-outputs/mock-demo}
PROVIDER=${2:-mock}

PYTHONPATH=src python3 -m water_negotiation_lab simulate \
  --config examples/mvp.toml \
  --out "$OUTPUT_DIR" \
  --provider "$PROVIDER"

