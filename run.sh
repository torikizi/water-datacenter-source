#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_ROOT"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  exec python3 -m water_negotiation_lab quickstart "$@"
