#!/bin/sh
# Run the joint-account period report with generated data under data/out.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
mkdir -p data/out
exec .venv/bin/cgt-calc \
  --from 2025-12-01 \
  --to 2026-04-05 \
  --schwab-file data/input/schwab-clean/merged-schwab-transactions.csv \
  --raw-file data/input/raw/pre-residency-raw.csv \
  --no-balance-check \
  --exchange-rates-file data/out/exchange_rates.csv \
  --isin-translation-file data/out/isin_translation.csv \
  --spin-offs-file data/out/spin_offs.csv \
  --output data/out/schwab-2025-12-01-to-2026-04-05.pdf
