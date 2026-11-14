"""Check wrapped fractional rows and refuse non-trades or ambiguous cash."""
from scripts.recover_statement_trades import extract


def test_wrapped_purchase_uses_explicit_quantity_and_cash(tmp_path):
    path=tmp_path/'statement.txt'
    path.write_text('--- Page 4 ---\n   01/14/22 01/19/22 Cash Buy - Securities Purchased MOTOROLA SOLUTIONS INC MSI 247.9369 (3.16) 80.36\n COM 0.013\n')
    rows=list(extract(path))
    assert rows[0][1] is None
    row=rows[0][0]
    assert (row['date'],row['settlement_date'],row['base_quantity'],row['out_quantity'])==('2022-01-14','2022-01-19','0.013','3.16')
    assert row['page']=='4'


def test_sale_preserves_net_proceeds_and_ignores_positions(tmp_path):
    path=tmp_path/'statement.txt'
    path.write_text('MSI 8.013 231.94 1858.54\n 01/04/22 01/06/22 Cash Sell - Securities Sold AMKOR TECHNOLOGY INC AMKR 20- 25.5001 510.00 (1127.93)\n COM\n')
    rows=list(extract(path))
    assert len(rows)==1
    assert rows[0][0]['transaction_type']=='SELL'
    assert rows[0][0]['out_quantity']=='510.00'


def test_inconsistent_cash_is_for_review_not_import(tmp_path):
    path=tmp_path/'statement.txt'
    path.write_text('01/04/22 01/06/22 Cash Buy - Securities Purchased EXAMPLE CO EXM 2 25.00 (900.00) 10.00\n')
    row,error=list(extract(path))[0]
    assert row is None
    assert error['reason']=='Quantity/price/cash disagreement'
