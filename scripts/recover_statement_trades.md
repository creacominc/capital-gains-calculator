# Recover older statement trades

Run `.venv/bin/python scripts/recover_statement_trades.py` for a review, or add
`--apply` to append verified-format transactions to `data/interest/pre-residency-costs.csv`.
Regenerate RAW input afterwards with `scripts/convert_pre_residency.py`.

The parser reads redacted statements under `data/input/statements`. It accepts
explicit trade and settlement dates, BUY/SELL labels, ticker, share quantity,
price and cash. It handles fractional quantities wrapped onto the following
line. It refuses inconsistent numeric columns, positions, journals, open orders
and CUSIP fixed-income trades. Date scope is before 14 May 2024 (migration).

Both supplemental trades and original broker exports are matched before adding
anything. Repeated statement copies are removed while multiple identical
executions in the same statement are retained. An existing supplemental record
using the settlement date is corrected to the explicit trade date. Original
broker data is never rewritten. A different ticker with identical date, quantity
and cash is held for rename review, rather than imported twice.

`data/cleanup-audit/statement-recovery` holds candidates, matched records, date
corrections and rows requiring review. These are transaction recovery results,
not evidence that every security's history or UK tax classification is complete.
Reconcile statement position checkpoints and corporate actions separately.

# Provisional report

`.venv/bin/python scripts/run_period_workpaper.py` calculates complete histories
through 30 days after 5 April for securities with a parsed disposal from 1 December
2025 through 5 April 2026. It includes option lifecycles through that cutoff and
period dividend/interest receipts. All parsed period disposals must be retained;
unrelated history failures are not allowed to block this workpaper. Outputs are
under `data/out/period-report`, explicitly labelled provisional. FIG relief is
not applied; income classifications, ignored receipts and broker exclusions
remain review items. This command is not the full-archive launcher.
