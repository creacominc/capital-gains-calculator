# Clean and merge local Schwab exports

From the repository root:

```bash
.venv/bin/python scripts/clean_schwab_exports.py
```

This workflow is for the two accounts the user confirmed have identical joint
owners. It is not a general merger for unrelated beneficial owners.

The script reads `data/input/schwab/*_Transaction*.csv` and leaves originals
unchanged. It writes one `data/input/schwab-clean/merged-schwab-transactions.csv`,
sorted newest first as required by Schwab's importer. It preserves every genuine
trade, including identical trades in different accounts; there is no deduplication.
Unique redemption and cash-merger pairs are grouped within their source file,
date, symbol and description and ordered as required by the parser. Row provenance
records the resulting order. Previously generated per-file copies in this output
folder are retired, leaving only the merged export.

`CD Interest` becomes `Bond Interest`, retaining its USD amount and all other
cells. Missing TDA symbols are recovered only when the description contains
one explicit parenthesized ticker. Where migrated TDA quantities, unit prices
and cash disagree, an effective unit price is calculated from the exported
quantity, cash and fees. This preserves reported shares and cash but does not
recover any lost precision in the original share quantity; these changes are
marked REVIEW in the conversion audit. Matched cancelled reinvestments and their
reversed purchases are removed using the original values, preferring the stated
as-of date where it identifies a unique original. Journaled entries and other
unsupported actions are omitted and audited.
No other unknown financial meaning is guessed. Supported `Reinvest Dividend`
rows are retained even though cgt-calc can ignore them.

Only unique, same-posted-date, same-symbol, opposite-quantity share journals
across the source files, with no cash amount, are matched as internal transfers.
They have no net effect on combined holdings. Cash/sweep journals, ambiguous
pairs, multi-day transfers, corporate actions and other unmatched journal rows
remain flagged for review. They are excluded under the requested cleanup policy;
that does not establish that their exclusion is correct for the final calculation.

Audit files under `data/cleanup-audit/`:

- `excluded-rows.csv`: every omitted row, source line, original cells and reason.
- `converted-rows.csv`: complete original and output rows for each conversion.
- `journal-reconciliation.csv`: matched partners or unresolved review status.
- `merged-row-sources.csv`: source filename and line for every merged row.
- `summary.json`: source SHA-256 hashes, counts, transfer statuses and parser errors.

The script regenerates outputs and audits on each run. Archive earlier audits if
needed. All financial files remain within the ignored `data/` folder.

Options: `--input-dir`, `--output-dir`, `--audit-dir`. These must be separate,
non-nested folders. The output folder must contain only generated exports.

The script validates the merged export with the actual Schwab file loader. It
returns status 1 and writes `PARSER_BLOCKED` when parsing fails. It does not delete
supported transactions simply to bypass a validation failure. The BIP/TRI reinvestment cancellation errors are resolved. The option matcher
now reserves a uniquely matched later assignment's same-day settlement rather
than treating it as a second candidate for an earlier weekly assignment.
The full calculation still fails because acquisition history is missing, first
for 10,000 units of 62957HAC9 redeemed on 15 November 2022. No acquisition cost
has been assumed and no final PDF has been produced. See calculation.log and
missing-acquisitions.csv in the audit folder. The latter is an inventory audit
of currently retained records: excluded corporate actions or unmatched transfers
can also cause shortfalls, so it is not proof that every flagged purchase was
absent from the original records.

Once parser blockers are resolved, the report command is:

```bash
mkdir -p data/out
.venv/bin/cgt-calc \
  --from 2025-12-01 \
  --to 2026-04-05 \
  --schwab-file data/input/schwab-clean/merged-schwab-transactions.csv \
  --raw-file data/input/raw/pre-residency-raw.csv \
  --no-balance-check \
  --exchange-rates-file data/out/exchange_rates.csv \
  --isin-translation-file data/out/isin_translation.csv \
  --spin-offs-file data/out/spin_offs.csv \
  --output data/out/schwab-2025-12-01-to-2026-04-05.pdf
```

`--no-balance-check` is used because the supplemental historical trades contain
no corresponding funding deposits and the cleanup omits cash journals. It does
not disable share-holding or acquisition-cost checks.

Parser acceptance does not establish complete holdings, income, acquisition costs
or a final tax liability. Review unresolved journals and excluded financial events
and check for duplication with the separate RAW history before tax use. The
combined report still needs allocation between the joint owners.

Verification:

```bash
.venv/bin/python -m unittest tests.general.test_clean_schwab_exports -q
```

For the same command with all runtime caches under `data/out`, run:

```bash
bash scripts/run_joint_schwab_report.sh
```

The repository's stock CLI and test helpers still default to `out/` when explicit
paths are omitted. Previously generated test PDFs were moved to `data/out/tests/`.

The report script regenerates the RAW file before each calculation and shows
output in the terminal while saving a unique London-timestamped log under
`data/out/log/`. It returns a nonzero exit status when either step fails.
