"""Regenerate cgt-calc RAW history from the supplemental ShareCalc files."""

import csv
from decimal import Decimal, localcontext
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    rows = []
    with (root / "data/interest/pre-residency-costs.csv").open(newline="") as handle:
        for item in csv.DictReader(handle):
            if item["transaction_type"] not in ("BUY", "SELL"):
                raise ValueError(f"Unsupported transaction: {item}")
            quantity = Decimal(item["base_quantity"])
            total = Decimal(item["out_quantity"])
            if quantity <= 0 or total < 0:
                raise ValueError(f"Invalid quantity or cash: {item}")
            with localcontext() as context:
                context.prec = 28
                price = total / quantity
                assert abs(price * quantity - total) < Decimal("0.000000000001")
            rows.append([item["date"], item["transaction_type"], item["base_asset"],
                         str(quantity), str(price), "0", item["out_asset"]])
    with (root / "data/interest/pre-residency-splits.csv").open(newline="") as handle:
        for item in csv.DictReader(handle):
            if not item["transaction_type"]:
                continue
            if item["transaction_type"] != "SPLIT":
                raise ValueError(f"Unsupported reorganisation: {item}")
            # These source quantities are the additional shares, not the
            # pre-split balance. Verified against preceding purchase history.
            rows.append([item["date"], "STOCK_SPLIT", item["base_asset"],
                         item["base_quantity"], "0", "0", "USD"])
    rows.sort(key=lambda row: row[0])
    target = root / "data/input/raw/pre-residency-raw.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "action", "symbol", "quantity", "price", "fees", "currency"])
        writer.writerows(rows)
    print(f"Generated {target}: {len(rows)} rows")


if __name__ == "__main__":
    main()
