"""The export cleaner must preserve source data and make exclusions auditable."""

import csv
import hashlib
import json
from pathlib import Path

import tempfile
import unittest
from contextlib import contextmanager


@contextmanager
def raises_value_error():
    try:
        yield
    except ValueError:
        return
    raise AssertionError("Expected ValueError")

from scripts.clean_schwab_exports import MERGED_NAME, clean_exports, reconcile_journals


HEADER = ["Date", "Action", "Symbol", "Description", "Quantity", "Price",
          "Fees & Comm", "Amount"]


def test_cleaner_preserves_originals_and_logs_every_removed_row(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    export = source / "Account_Transactions.csv"
    rows = [
        ["01/02/2025", "Buy", "ABC", "A, B", "2", "$10", "", "-$20"],
        ["01/01/2025", "Journaled Shares", "ABC", "Transfer", "2", "", "", ""],
        ["01/01/2025", "Future Unknown Action", "", "Unknown", "", "", "", "$3"],
    ]
    with export.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    original = export.read_bytes()
    output, audit = tmp_path / "output", tmp_path / "audit"
    report = clean_exports(source, output, audit)
    assert export.read_bytes() == original
    assert report["files"][0]["source_sha256"] == hashlib.sha256(original).hexdigest()
    with (output / MERGED_NAME).open(newline="") as handle:
        cleaned = list(csv.DictReader(handle))
        assert cleaned == [dict(zip(HEADER, rows[0]))]
    with (audit / "excluded-rows.csv").open(newline="") as handle:
        excluded = list(csv.DictReader(handle))
    assert [int(row["source_row"]) for row in excluded] == [3, 4]
    assert [json.loads(row["original_row_json"]) for row in excluded] == rows[1:]
    assert report["status"] == "PARSES_WITH_EXCLUSIONS"
    # Repeat runs regenerate the same copies without appending duplicates.
    clean_exports(source, output, audit)
    assert export.read_bytes() == original
    assert report == json.loads((audit / "summary.json").read_text())


def test_cleaner_refuses_outputs_inside_source(tmp_path: Path) -> None:
    with raises_value_error():
        clean_exports(tmp_path, tmp_path / "clean", tmp_path / "audit")


def test_cleaner_reports_supported_but_unpaired_action(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with (source / "Account_Transactions.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerow(["01/02/2025", "Full Redemption Adj", "ABC", "Test",
                         "", "", "", "$20"])
    report = clean_exports(source, tmp_path / "output", tmp_path / "audit")
    assert report["status"] == "PARSER_BLOCKED"
    assert report["parser_errors"]
    assert report["files"][0]["kept_rows"] == 1


class CleanerTests(unittest.TestCase):
    def test_tda_symbols_prices_and_cancelled_reinvestments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            rows = [
                ["07/03/2023", "Reinvest Shares", "BIP", "TDA TRAN - Bought 0.004 (BIP)", "0.004", "$37.035", "", "-$0.16"],
                ["07/07/2023 as of 07/03/2023", "Cancel Buy", "BIP", "TDA TRAN - Bought 0.004 (BIP)", "0.004", "$37.035", "", "$0.16"],
                ["07/07/2023", "Reinvest Shares", "BIP", "TDA TRAN - Bought 0.004 (BIP)", "0.004", "$37.035", "", "-$0.16"],
                ["07/07/2023", "Qual Div Reinvest", "", "TDA TRAN - QUALIFIED DIVIDEND (BIP)", "", "", "", "$0.16"],
            ]
            with (source / "Buy_Transactions.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(HEADER)
                writer.writerows(rows)
            report = clean_exports(source, root / "output", root / "audit")
            assert report["parser_errors"] == []
            assert report["merged_rows"] == 2
            with (root / "output" / MERGED_NAME).open(newline="") as handle:
                cleaned = list(csv.DictReader(handle))
            reinvest = next(r for r in cleaned if r["Action"] == "Reinvest Shares")
            assert reinvest["Price"] == "4E+1"
            assert reinvest["Quantity"] == "0.004" and reinvest["Amount"] == "-$0.16"
            assert all(r["Symbol"] == "BIP" for r in cleaned)
            with (root / "audit/excluded-rows.csv").open(newline="") as handle:
                excluded = list(csv.DictReader(handle))
            assert {r["source_row"] for r in excluded} == {"2", "3"}
            assert json.loads(next(r for r in excluded if r["source_row"] == "2")["original_row_json"]) == rows[0]

    def test_journal_pairs_require_unique_opposite_movement(self):
        def journal(source, line, quantity):
            return {"source_file": source, "source_row": line,
                    "Action": "Journaled Shares", "Symbol": "ABC",
                    "Date": "01/02/2025", "Quantity": quantity, "Amount": ""}

        pair = [journal("buy.csv", 2, "-10"), journal("hold.csv", 3, "10")]
        result = reconcile_journals(pair)
        assert [r["status"] for r in result] == ["MATCHED_INTERNAL_TRANSFER"] * 2
        assert result[0]["matched_source_file"] == "hold.csv"
        assert "status" not in pair[0]
        ambiguous = pair + [journal("hold.csv", 4, "10.0")]
        assert all(r["status"] == "UNMATCHED_REVIEW"
                   for r in reconcile_journals(ambiguous))
        same_account = [journal("buy.csv", 2, "-10"), journal("buy.csv", 3, "10")]
        assert all(r["status"] == "UNMATCHED_REVIEW"
                   for r in reconcile_journals(same_account))

    def test_merge_preserves_identical_trades_from_different_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            for name in ("Buy_Transactions.csv", "Hold_Transactions.csv"):
                with (source / name).open("w", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(HEADER)
                    writer.writerow(["01/02/2025", "Buy", "ABC", "Trade", "1", "$10", "", "-$10"])
            report = clean_exports(source, root / "output", root / "audit")
            assert report["merged_rows"] == 2
            assert report["parser_errors"] == []
            assert [p.name for p in (root / "output").iterdir()] == [MERGED_NAME]
            with (root / "audit/merged-row-sources.csv").open(newline="") as handle:
                provenance = list(csv.DictReader(handle))
            assert {r["source_file"] for r in provenance} == {
                "Buy_Transactions.csv", "Hold_Transactions.csv"}

    def test_merge_sorts_dates_and_orders_redemption_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            rows = [
                ["01/01/2025", "Buy", "ABC", "Trade", "2", "$10", "", "-$20"],
                ["01/02/2025", "Full Redemption", "ABC", "Redemption", "-2", "", "", ""],
                ["01/02/2025", "Full Redemption Adj", "ABC", "Redemption", "", "", "", "$20"],
            ]
            with (source / "Buy_Transactions.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(HEADER)
                writer.writerows(rows)
            report = clean_exports(source, root / "output", root / "audit")
            assert report["parser_errors"] == []
            with (root / "output" / MERGED_NAME).open(newline="") as handle:
                cleaned = list(csv.DictReader(handle))
            assert [r["Action"] for r in cleaned] == [
                "Full Redemption Adj", "Full Redemption", "Buy"]

    def test_cd_interest_is_retained_as_interest(self):
        from collections import OrderedDict
        from decimal import Decimal
        from cgt_calc.model import ActionType
        from cgt_calc.parsers.schwab import SchwabTransaction

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            export = source / "Account_Transactions.csv"
            row = ["12/15/2025", "CD Interest", "06051XG97", "Bank CD",
                   "", "", "", "$37.50"]
            with export.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(HEADER)
                writer.writerow(row)
            original = export.read_bytes()
            report = clean_exports(source, root / "output", root / "audit")
            assert export.read_bytes() == original
            assert report["files"][0]["converted_actions"] == {"CD Interest": 1}
            assert report["files"][0]["excluded_actions"] == {}
            assert report["status"] == "PARSES_WITH_EXCLUSIONS"
            with (root / "output" / MERGED_NAME).open(newline="") as handle:
                cleaned = list(csv.DictReader(handle))[0]
            assert cleaned["Action"] == "Bond Interest"
            assert [cleaned[key] for key in HEADER if key != "Action"] == [
                value for key, value in zip(HEADER, row) if key != "Action"
            ]
            transaction = SchwabTransaction(OrderedDict(cleaned), export)
            assert transaction.action == ActionType.INTEREST
            assert transaction.amount == Decimal("37.50")
            with (root / "audit/converted-rows.csv").open(newline="") as handle:
                audit = list(csv.DictReader(handle))
            assert len(audit) == 1
            assert json.loads(audit[0]["original_row_json"]) == row

    def test_preservation_and_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            test_cleaner_preserves_originals_and_logs_every_removed_row(Path(folder))

    def test_path_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            test_cleaner_refuses_outputs_inside_source(Path(folder))

    def test_pairing_error(self):
        with tempfile.TemporaryDirectory() as folder:
            test_cleaner_reports_supported_but_unpaired_action(Path(folder))


if __name__ == "__main__":
    unittest.main()
