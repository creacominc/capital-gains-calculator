#!/bin/bash
# Regenerate supplemental history and log the joint-account report run.
set -euo pipefail
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
mkdir -p data/out/log
report_log=$(mktemp "data/out/log/cgt-calc-$(TZ=Europe/London date +%Y-%m-%d_%H-%M-%S)-XXXXXX.log")
report_pdf="data/out/$(basename "$report_log" .log).pdf"
printf 'Log: %s\n' "$report_log"
printf 'PDF: %s\n' "$report_pdf"
run_report() {
  .venv/bin/python scripts/convert_pre_residency.py || return $?
  .venv/bin/cgt-calc \
  --from 2025-12-01 \
  --to 2026-04-05 \
  --schwab-file data/input/schwab-clean/merged-schwab-transactions.csv \
  --raw-file data/input/raw/pre-residency-raw.csv \
  --no-balance-check \
  --exchange-rates-file data/out/exchange_rates.csv \
  --isin-translation-file data/out/isin_translation.csv \
  --spin-offs-file data/out/spin_offs.csv \
  --output "$report_pdf"
}
# pipefail preserves a conversion/calculation failure even when tee succeeds.
run_report 2>&1 | tee "$report_log"
