"""Recover explicit older-statement equity trades with provenance and duplicate checks.

Default writes review files only. --apply appends unmatched older-statement trades.
Never treats positions, open orders, journals or corporate actions as purchases.
"""
import argparse
from collections import Counter
import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
START = re.compile(r'^\s*(\d{2}/\d{2}/\d{2})\s+(\d{2}/\d{2}/\d{2})\s+Cash\s+(Buy - Securities Purchased|Sell - Securities Sold)\s+(.*)')
TAIL = re.compile(r'^(.*?)\b([A-Z][A-Z0-9.]{0,8})\s+([\d$(),.\s-]+)$')
NUMBER = re.compile(r'^\(?-?\d[\d,]*(?:\.\d+)?-?\)?$')


def number(s):
    return abs(Decimal(s.replace(',', '').replace('(', '').replace(')', '').rstrip('-')))


def extract(path):
    lines = path.read_text().splitlines()
    page = ''
    for i, line in enumerate(lines):
        page_match = re.match(r'--- Page (\d+) ---', line)
        if page_match:
            page = page_match[1]
        match = START.match(line)
        if not match:
            continue
        trade, settle, action, body = match.groups()
        tail = TAIL.match(body)
        if not tail:
            yield None, {'source': path.name, 'line': i+1, 'reason': 'Unparsed trade columns', 'text': line.strip()}
            continue
        description, symbol, numbers = tail.groups()
        if len(symbol) == 9 and any(c.isdigit() for c in symbol):
            continue  # Fixed-income principal/pricing needs its own parser.
        tokens = numbers.replace('$', '').split()
        if not all(NUMBER.match(t) for t in tokens) or len(tokens) not in (3, 4):
            yield None, {'source': path.name, 'line': i+1, 'reason': 'Unexpected numeric columns', 'text': line.strip()}
            continue
        if len(tokens) == 4:
            quantity, price, amount, balance = tokens
        else:
            price, amount, balance = tokens
            quantity = None
            # Fractional quantities wrap onto the immediately following description line.
            if i+1 < len(lines) and not re.match(r'^\s*\d{2}/', lines[i+1]):
                continuation = lines[i+1].strip().split()
                if continuation and NUMBER.match(continuation[-1]):
                    quantity = continuation[-1]
            if quantity is None:
                yield None, {'source': path.name, 'line': i+1, 'reason': 'Missing wrapped quantity', 'text': line.strip()}
                continue
        q, p, cash = number(quantity), number(price), number(amount)
        buy = action.startswith('Buy')
        if q <= 0 or p < 0 or cash <= 0 or buy != amount.startswith('('):
            yield None, {'source': path.name, 'line': i+1, 'reason': 'Inconsistent trade signs', 'text': line.strip()}
            continue
        # Displayed fractional quantities and prices can be rounded. Preserve cash, do not derive it.
        tolerance = max(Decimal('.05'), p*Decimal('.00051') + q*Decimal('.0051'))
        if abs(q*p-cash) > tolerance:
            yield None, {'source': path.name, 'line': i+1, 'reason': 'Quantity/price/cash disagreement', 'text': line.strip()}
            continue
        yield {'date': dt.datetime.strptime(trade, '%m/%d/%y').date().isoformat(),
               'settlement_date': dt.datetime.strptime(settle, '%m/%d/%y').date().isoformat(),
               'transaction_type': 'BUY' if buy else 'SELL', 'base_asset': symbol,
               'base_quantity': str(q), 'out_asset': 'USD', 'out_quantity': str(cash),
               'statement_price': str(p), 'source': path.name, 'page': page, 'line': i+1}, None


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--only-failing', action='store_true', help='Limit additions to currently failing ticker histories')
    args=ap.parse_args()
    out=ROOT/'data/cleanup-audit/statement-recovery';out.mkdir(parents=True,exist_ok=True)
    candidates=[];rejected=[]
    for path in sorted((ROOT/'data/input/statements').glob('*.txt')):
        for row, error in extract(path):
            if error:rejected.append(error)
            elif row['date'] < '2024-05-14':candidates.append(row)
    # Adjacent statements repeat a trade on settlement. Keep the largest per-source
    # multiplicity, rather than dropping genuine identical executions within a source.
    counts=Counter(); unique=[]
    for row in candidates:
        key=tuple(row[k] for k in ('date','transaction_type','base_asset','base_quantity','out_quantity'))
        sourcekey=(key,row['source']);counts[sourcekey]+=1
        if counts[sourcekey] <= max((v for (k,src),v in counts.items() if k==key and src!=row['source']),default=0):continue
        unique.append(row)
    candidates=unique
    if args.only_failing:
        with (ROOT/'data/out/diagnostic/symbol-checks.csv').open() as f:
            failing={r['symbol'] for r in csv.DictReader(f) if r['status']=='FAIL'}
        candidates=[r for r in candidates if r['base_asset'] in failing]
    p=ROOT/'data/interest/pre-residency-costs.csv'
    with p.open(newline='') as f:r=csv.DictReader(f);header=r.fieldnames;existing=list(r)
    # Include broker trades when matching overlap; never add a statement copy again.
    for source in sorted((ROOT/'data/input/schwab').glob('*.csv')):
        with source.open(encoding='utf-8-sig',newline='') as f:
            for broker in csv.DictReader(f):
                action=broker.get('Action','')
                if action not in ('Buy','Sell','Reinvest Shares'):continue
                try:
                    date=dt.datetime.strptime(broker['Date'].split(' as of')[0],'%m/%d/%Y').date().isoformat()
                    q=number(broker['Quantity'].replace('$',''))
                    cash=number(broker['Amount'].replace('$',''))
                except (ValueError,KeyError,ArithmeticError):continue
                existing.append({'date':date,'transaction_type':'SELL' if action=='Sell' else 'BUY','base_asset':broker['Symbol'],'base_quantity':str(q),'out_asset':'USD','out_quantity':str(cash),'broker_source':source.name,'aliases':re.findall(r'\(([A-Z][A-Z0-9.]{0,8})\)',broker.get('Description',''))})
    # Consume existing records once, preserving multiple genuine executions.
    used=set(); additions=[];matched=[];corrections=[]
    for row in sorted(candidates,key=lambda r:(r['date'],r['source'],r['line'])):
        found=None
        for n,old in enumerate(existing):
            if n in used:continue
            if all(old[k]==row[k] for k in ('transaction_type','out_asset')) and (old['base_asset']==row['base_asset'] or row['base_asset'] in old.get('aliases',[])) and Decimal(old['base_quantity'])==Decimal(row['base_quantity']) and Decimal(old['out_quantity'])==Decimal(row['out_quantity']):
                if old['date'] in (row['date'],row['settlement_date']):found=n;break
        if found is not None:
            used.add(found);matched.append({**row,'status':'ALREADY_PRESENT','existing_date':existing[found]['date']})
            if 'broker_source' not in existing[found] and existing[found]['date']!=row['date']:
                corrections.append({**row,'previous_date':existing[found]['date']})
                if args.apply:existing[found]['date']=row['date']
        else:
            potential=[old for old in existing if old['date'] in (row['date'],row['settlement_date']) and old['transaction_type']==row['transaction_type'] and Decimal(old['base_quantity'])==Decimal(row['base_quantity']) and Decimal(old['out_quantity'])==Decimal(row['out_quantity'])]
            if potential:
                rejected.append({'source':row['source'],'line':row['line'],'reason':'Possible existing trade under another ticker; review rename','text':str(row)})
                continue
            # Same trade appears in the following statement when it settles; dedupe source copies.
            additions.append(row)
    for name,rows in [('candidates.csv',additions),('matched.csv',matched),('unparsed.csv',rejected),('date-corrections.csv',corrections)]:
        if rows:
            with (out/name).open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    if args.apply:
        with p.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=header);w.writeheader();w.writerows({k:r[k] for k in header} for r in existing if 'broker_source' not in r)
            w.writerows({k:r[k] for k in header} for r in additions)
    print(f'{len(candidates)} explicit older-statement trades; {len(matched)} already present; {len(additions)} additions; {len(rejected)} unparsed rows across all years. Applied={args.apply}')


if __name__=='__main__':main()
