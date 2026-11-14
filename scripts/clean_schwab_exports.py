"""Filter unsupported Schwab actions, preserving originals and an exclusion audit.

Run from the repository with .venv/bin/python scripts/clean_schwab_exports.py.
Outputs are for reconciliation: omitted rows may affect income and holdings.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys

# Allow direct execution without relying on a globally installed cgt-calc.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.schwab import RequiredTransactionsColumn, SchwabParser, action_from_str
from cgt_calc.util import approx_equal


MERGED_NAME = "merged-schwab-transactions.csv"


def transaction_date(value: str):
    """Use the posted date, matching the Schwab importer, not an as-of annotation."""
    return datetime.strptime(value.split(" as of ")[0], "%m/%d/%Y").date()


def reconcile_journals(excluded: list[dict]) -> list[dict]:
    """Match only unique opposite share movements on the same day across files.

    No price is used as a cost basis. Ambiguous pairs and multi-day or split
    transfers remain for review rather than being guessed at.
    """
    journals = []
    for entry in excluded:
        if not entry["Action"].strip().casefold().startswith("journaled"):
            continue
        record = dict(entry)
        record.update(status="UNMATCHED_REVIEW", matched_source_file="", matched_source_row="")
        if not record["Symbol"] or record["Symbol"] == "MMDA1":
            record["status"] = "CASH_OR_SWEEP_REVIEW"
        journals.append(record)
    for entry in journals:
        if entry["status"] != "UNMATCHED_REVIEW" or entry["Amount"].strip():
            continue
        try:
            quantity = Decimal(entry["Quantity"].replace(",", ""))
            if not quantity.is_finite() or quantity == 0:
                continue
            def opposite(other):
                return (other["source_file"] != entry["source_file"]
                        and other["Symbol"] == entry["Symbol"]
                        and transaction_date(other["Date"]) == transaction_date(entry["Date"])
                        and not other["Amount"].strip()
                        and Decimal(other["Quantity"].replace(",", "")) == -quantity)
            candidates = [other for other in journals
                          if other["status"] == "UNMATCHED_REVIEW" and opposite(other)]
            # A unique reverse direction is also required; repeated identical
            # transfers cannot be assigned a counterpart without guessing.
            same_side = [other for other in journals
                         if other["source_file"] == entry["source_file"]
                         and other["Symbol"] == entry["Symbol"]
                         and transaction_date(other["Date"]) == transaction_date(entry["Date"])
                         and Decimal(other["Quantity"].replace(",", "")) == quantity]
            if len(candidates) != 1 or len(same_side) != 1:
                continue
            other = candidates[0]
            for left, right in ((entry, other), (other, entry)):
                left.update(status="MATCHED_INTERNAL_TRANSFER",
                            matched_source_file=right["source_file"],
                            matched_source_row=right["source_row"])
        except (InvalidOperation, ValueError):
            continue
    return journals


def merge_rows(prepared):
    """Keep every trade; put unique corporate-action pairs in parser order."""
    canonical = [column.value for column in RequiredTransactionsColumn]
    blocks = []
    for source, header, rows, source_lines in prepared:
        records = [dict(zip(header, row)) for row in rows]
        consumed = set()
        for i, record in enumerate(records):
            if i in consumed:
                continue
            block = [i]
            pair = {"Full Redemption": ("Full Redemption Adj", "Full Redemption"),
                    "Full Redemption Adj": ("Full Redemption Adj", "Full Redemption"),
                    "Cash Merger": ("Cash Merger", "Cash Merger Adj"),
                    "Cash Merger Adj": ("Cash Merger", "Cash Merger Adj")}.get(record["Action"])
            if pair:
                candidates = [j for j, other in enumerate(records)
                              if j not in consumed and other["Action"] in pair
                              and all(other[key] == record[key]
                                      for key in ("Date", "Symbol", "Description"))]
                if len(candidates) == 2 and {records[j]["Action"] for j in candidates} == set(pair):
                    block = sorted(candidates, key=lambda j: pair.index(records[j]["Action"]))
            consumed.update(block)
            blocks.append((transaction_date(record["Date"]), source,
                           [(records[j], source_lines[j]) for j in block]))
    # Stable sort preserves source order within a date except for the explicitly
    # paired rows. Blocks stay adjacent even when both accounts have that event.
    blocks.sort(key=lambda block: block[0], reverse=True)
    merged, provenance = [], []
    for _, source, records in blocks:
        for record, source_line in records:
            merged.append([record[column] for column in canonical])
            provenance.append({"merged_row": len(merged) + 1,
                               "source_file": source.name, "source_row": source_line})
    return canonical, merged, provenance


def cancel_reinvestments(source, header, rows, lines, excluded, originals):
    """Remove proven cancelled reinvestments the native Buy matcher cannot see."""
    records = [dict(zip(header, originals[line])) for line in lines]
    removed = set()
    for i, cancel in enumerate(records):
        if cancel["Action"] != "Cancel Buy":
            continue
        candidates = []
        for j, buy in enumerate(records):
            if j in removed or buy["Action"] != "Reinvest Shares":
                continue
            if any(buy[key] != cancel[key] for key in ("Symbol", "Quantity", "Price", "Fees & Comm")):
                continue
            delta = (transaction_date(cancel["Date"]) - transaction_date(buy["Date"])).days
            def amount(row):
                return Decimal(row["Amount"].replace("$", "").replace(",", ""))
            if 0 <= delta <= 5 and amount(buy) == -amount(cancel):
                candidates.append(j)
        if " as of " in cancel["Date"]:
            effective = transaction_date(cancel["Date"].split(" as of ")[1])
            dated = [j for j in candidates if transaction_date(records[j]["Date"]) == effective]
            if dated:
                candidates = dated
        if len(candidates) != 1:
            continue
        j = candidates[0]
        for index, partner in ((i, j), (j, i)):
            excluded.append({"source_file": source.name, "source_row": lines[index],
                             "reason": f"Matched cancelled reinvestment; counterpart source row {lines[partner]}",
                             "original_row_json": json.dumps(originals[lines[index]]),
                             **{key: records[index][key] for key in header[:8]}})
            removed.add(index)
    return ([row for i, row in enumerate(rows) if i not in removed],
            [line for i, line in enumerate(lines) if i not in removed])


def clean_exports(input_dir: Path, output_dir: Path, audit_dir: Path, *, splits_file: Path | None = None) -> dict:
    """Write filtered copies and log every exclusion; never modify the inputs."""
    overrides = {}
    if splits_file is None:
        splits_file = Path(__file__).resolve().parents[1] / "data/interest/pre-residency-splits.csv"
    if splits_file.exists():
        with splits_file.open(newline="") as handle:
            for item in csv.DictReader(handle):
                if item.get("extra", "").startswith("schwab_override"):
                    key = (item["base_asset"], item["date"])
                    if key in overrides:
                        raise ValueError(f"Duplicate supplemental split override: {key}")
                    quantity = Decimal(item["base_quantity"])
                    if not quantity.is_finite() or quantity <= 0:
                        raise ValueError(f"Invalid supplemental split quantity: {key}")
                    overrides[key] = quantity
    replaced = Counter()
    paths = [p.resolve() for p in (input_dir, output_dir, audit_dir)]
    if len(set(paths)) != 3 or any(
        a in b.parents or b in a.parents
        for i, a in enumerate(paths) for b in paths[i + 1:]
    ):
        raise ValueError("Input, output and audit folders must be separate, non-nested folders")
    files = sorted(input_dir.glob("*_Transaction*.csv"))
    if not files:
        raise ValueError(f"No *_Transaction*.csv files in {input_dir}")
    # Refuse stale/foreign outputs so --schwab-dir cannot accidentally read them.
    expected = {p.name for p in files} | {MERGED_NAME}
    if output_dir.exists() and any(p.name not in expected for p in output_dir.iterdir()):
        raise ValueError("Output folder contains unexpected files; use a dedicated folder")
    prepared = []
    excluded = []
    converted = []
    summaries = []
    for source in files:
        with source.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            header = next(reader)
            if (set(header[:8]) != {column.value for column in RequiredTransactionsColumn}
                    or len(header) not in (8, 9) or (len(header) == 9 and header[8] != "")):
                raise ValueError(f"Unexpected Schwab header in {source}")
            kept = []
            source_lines = []
            originals = {}
            counts = Counter()
            conversion_counts = Counter()
            total = 0
            for row in reader:
                line = reader.line_num
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) != len(header):
                    raise ValueError(f"Malformed CSV row in {source}:{line}")
                total += 1
                originals[line] = row.copy()
                action = row[header.index("Action")]
                if not row[header.index("Symbol")] and row[header.index("Description")].startswith("TDA TRAN"):
                    symbols = re.findall(r"\(([A-Z][A-Z0-9./-]{0,11})\)", row[header.index("Description")])
                    if len(set(symbols)) == 1:
                        original = row.copy()
                        row[header.index("Symbol")] = symbols[0]
                        converted.append({
                            "source_file": source.name, "source_row": line,
                            "original_action": action, "output_action": action,
                            "reason": "Missing symbol recovered from explicit TDA description",
                            "original_row_json": json.dumps(original), "output_row_json": json.dumps(row),
                        })
                        conversion_counts["TDA symbol from description"] += 1
                # Prices displayed on no-cash splits are informational, not cost.
                if action == "Stock Split" and row[header.index("Price")].strip():
                    cash_and_fees = [Decimal(row[header.index(column)].replace("$", "").replace(",", "") or "0")
                                     for column in ("Amount", "Fees & Comm")]
                    if all(value == 0 for value in cash_and_fees):
                        original = row.copy()
                        row[header.index("Price")] = ""
                        converted.append({
                            "source_file": source.name, "source_row": line,
                            "original_action": action, "output_action": action,
                            "reason": "Informational price removed from no-cash stock split; shares unchanged",
                            "original_row_json": json.dumps(original), "output_row_json": json.dumps(row),
                        })
                        conversion_counts["No-cash split informational price"] += 1
                # Fractional reinvestments and migrated TDA trades can have rounded share
                # quantities inconsistent with their stated unit price. Keep
                # the exported shares and actual cash; never invent precision
                # in the share count. All such effective prices are audited.
                migrated_tda = row[header.index("Description")].startswith("TDA TRAN")
                reinvestment = action == "Reinvest Shares"
                if action in ("Buy", "Sell", "Reinvest Shares") and (migrated_tda or reinvestment):
                    values = dict(zip(header, row))
                    def number(value):
                        return Decimal(value.replace("$", "").replace(",", "") or "0")
                    quantity = number(values["Quantity"])
                    price = number(values["Price"])
                    amount = number(values["Amount"])
                    fees = number(values["Fees & Comm"])
                    complete = all(values[column].strip() for column in ("Quantity", "Price", "Amount"))
                    valid = all(value.is_finite() for value in (quantity, price, amount, fees))
                    if complete and valid and quantity > 0 and price >= 0 and fees >= 0 and len(values["Symbol"]) != 9:
                        gross = -amount - fees if action in ("Buy", "Reinvest Shares") else amount + fees
                        if gross >= 0 and not approx_equal(quantity * price, gross):
                            original = row.copy()
                            row[header.index("Price")] = str(gross / quantity)
                            converted.append({
                                "source_file": source.name, "source_row": line,
                                "original_action": action, "output_action": action,
                                "reason": (
                                    "REVIEW: effective price from exported cash and quantity; migrated TDA values disagree"
                                    if migrated_tda else
                                    "REVIEW: reinvestment effective price from exported cash less fees and quantity; shares and cash unchanged"
                                ),
                                "original_row_json": json.dumps(original), "output_row_json": json.dumps(row),
                            })
                            conversion_counts["TDA effective unit price" if migrated_tda else "Reinvestment effective unit price"] += 1
                interest_action = {"CD Interest": "Bond Interest", "Bank Interest": "Credit Interest"}.get(action)
                if interest_action is not None:
                    original = row.copy()
                    row[header.index("Action")] = interest_action
                    converted.append({
                        "source_file": source.name, "source_row": line,
                        "original_action": action, "output_action": interest_action,
                        "reason": f"{action} mapped to supported interest action; date, description and cash unchanged",
                        "original_row_json": json.dumps(original),
                        "output_row_json": json.dumps(row),
                    })
                    conversion_counts[action] += 1
                    action = interest_action
                reason = None
                split_key = (row[header.index("Symbol")], transaction_date(row[header.index("Date")]).isoformat())
                if action == "Stock Split" and split_key in overrides:
                    values = dict(zip(header, row))
                    if any(Decimal(values[column].replace("$", "").replace(",", "") or "0")
                           for column in ("Fees & Comm", "Amount")):
                        raise ValueError(f"Split override cannot discard cash or fees: {source}:{line}")
                    replaced[split_key] += Decimal(values["Quantity"].replace(",", ""))
                    reason = "Replaced by zero-price supplemental RAW split; original display price is not a cash payment"
                elif action.strip().casefold().startswith("journaled"):
                    reason = "Journaled entry excluded by requested cleanup policy"
                else:
                    try:
                        action_from_str(action, source)
                    except ParsingError as error:
                        reason = str(error)
                if reason:
                    counts[action] += 1
                    excluded.append({"source_file": source.name, "source_row": line,
                                     "reason": reason, "original_row_json": json.dumps(originals[line]),
                                     **dict(zip(header[:8], originals[line][:8]))})
                else:
                    kept.append(row)
                    source_lines.append(line)
        kept, source_lines = cancel_reinvestments(source, header, kept, source_lines, excluded, originals)
        counts = Counter(entry["Action"] for entry in excluded if entry["source_file"] == source.name)
        prepared.append((source, header, kept, source_lines))
        summaries.append({"file": source.name,
                          "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                          "input_rows": total, "kept_rows": len(kept),
                          "converted_actions": dict(sorted(conversion_counts.items())),
                          "excluded_actions": dict(sorted(counts.items()))})
    for key, quantity in replaced.items():
        if quantity != overrides[key]:
            raise ValueError(f"Supplemental split {key} states {overrides[key]} additional shares but export states {quantity}")
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    header, merged, provenance = merge_rows(prepared)
    target = output_dir / MERGED_NAME
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(merged)
    # Retire only copies generated by the previous version of this script.
    for source in files:
        old_copy = output_dir / source.name
        if old_copy.exists():
            old_copy.unlink()
    with (audit_dir/"merged-row-sources.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["merged_row", "source_file", "source_row"])
        writer.writeheader()
        writer.writerows(provenance)
    journals = reconcile_journals(excluded)
    with (audit_dir/"journal-reconciliation.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "status", "matched_source_file", "matched_source_row", "source_file", "source_row",
            "reason", "original_row_json", *[c.value for c in RequiredTransactionsColumn],
        ])
        writer.writeheader()
        writer.writerows(journals)
    with (audit_dir/"excluded-rows.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_file", "source_row", "reason",
                               "original_row_json", *[c.value for c in RequiredTransactionsColumn]])
        writer.writeheader()
        writer.writerows(excluded)
    with (audit_dir/"converted-rows.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "source_file", "source_row", "original_action", "output_action",
            "reason", "original_row_json", "output_row_json",
        ])
        writer.writeheader()
        writer.writerows(converted)
    errors = []
    # The user confirmed these are accounts with identical joint ownership.
    # Validate exactly the single merged file supplied to --schwab-file.
    try:
        SchwabParser.load_from_file(target)
    except (ParsingError, ValueError) as error:
        errors.append({"file": MERGED_NAME, "error": str(error)})
    report = {"status": "PARSER_BLOCKED" if errors else "PARSES_WITH_EXCLUSIONS",
              "notice": "Excluded rows may affect income, costs and holdings. Reconcile before tax use. "
                        "Parser acceptance does not verify calculation completeness. "
                        "Supported actions ignored by cgt-calc, such as Reinvest Dividend, are retained.",
              "merged_file": str(target), "merged_rows": len(merged),
              "journal_reconciliation": dict(Counter(row["status"] for row in journals)),
              "files": summaries, "parser_errors": errors}
    (audit_dir/"summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/input/schwab"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/input/schwab-clean"))
    parser.add_argument("--audit-dir", type=Path, default=Path("data/cleanup-audit"))
    args = parser.parse_args()
    report = clean_exports(args.input_dir, args.output_dir, args.audit_dir)
    for entry in report["files"]:
        removed = entry["input_rows"] - entry["kept_rows"]
        print(f"{entry['file']}: kept {entry['kept_rows']}, excluded {removed}")
    print(report["status"])
    print(f"Merged export: {report['merged_file']} ({report['merged_rows']} rows)")
    print(f"Journal reconciliation: {report['journal_reconciliation']}")
    for error in report["parser_errors"]:
        print(f"{error['file']}: {error['error']}")
    print(f"Audit: {args.audit_dir}/excluded-rows.csv, converted-rows.csv and summary.json")
    print(report["notice"])
    return 1 if report["parser_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
