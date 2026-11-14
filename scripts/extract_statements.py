"""Extract statement text, redacting personal identifiers before writing to disk."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import re

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path('/Users/haroldt/Documents/Accounting/accountLog/Schwab_Ameritrade.Investment')
# Match names even where PDF extraction separates individual letters or reverses text.
NAMES = ('HAROLD', 'REBECCA', 'TOMLINSON', 'DLORAH', 'ACCEBER', 'NOSNILMOT')
NAME_RE = re.compile('|'.join(r'\s*'.join(word) for word in NAMES), re.I)
ACCOUNT_RE = re.compile(r'(?<!\d)(?:\d{3}-\d{6}|\d{4}-\d{4})(?!\d)')
LABEL_RE = re.compile(r'account\s*(?:number|#)', re.I)


def redact(text):
    accounts = set(ACCOUNT_RE.findall(text))
    lines = text.splitlines()
    # Discover unformatted account numbers on a labelled line or its next line.
    for i, line in enumerate(lines):
        if LABEL_RE.search(line):
            for candidate in lines[i:i + 2]:
                accounts.update(re.findall(r'(?<![\d.])\d{8,12}(?![\d.])', candidate))
    for account in sorted(accounts, key=len, reverse=True):
        text = text.replace(account, '[ACCOUNT REDACTED]')
    text = ACCOUNT_RE.sub('[ACCOUNT REDACTED]', text)
    text = NAME_RE.sub('[NAME REDACTED]', text)
    if NAME_RE.search(text) or ACCOUNT_RE.search(text):
        raise ValueError('Identifier redaction check failed')
    return text


def extract(job):
    source, output = map(Path, job)
    try:
        with pdfplumber.open(source) as pdf:
            pages = [page.extract_text(layout=True) or '' for page in pdf.pages]
        if not pages or not any(page.strip() for page in pages):
            raise ValueError('No extractable text; OCR required')
        text = redact('\n'.join(f'\n--- Page {n} ---\n{page}' for n, page in enumerate(pages, 1)))
        output.write_text(text, encoding='utf-8')
        return {'file': output.name, 'pages': len(pages), 'status': 'OK',
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    except Exception as error:
        return {'file': output.name, 'status': 'ERROR', 'error': str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?', type=Path, help='Optional single PDF; default is all matching statements')
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/input/statements')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.file:
        sources = [args.file.expanduser()]
    else:
        sources = sorted(p for d in args.source_dir.iterdir()
                         if d.is_dir() and d.name.lower().startswith('invest')
                         for p in d.iterdir() if p.is_file() and p.suffix.lower() == '.pdf'
                         and (p.name.lower().endswith('statement.pdf')
                              or p.name.lower().startswith('brokerage statement') and '_' in p.stem))
    if not sources:
        parser.error('No matching PDFs found')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(str(p), str(args.output_dir / f'{p.parent.name}__{p.stem}.txt')) for p in sources]
    if len({j[1] for j in jobs}) != len(jobs):
        parser.error('Output filename collision')
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(extract, jobs):
            results.append(result)
            print(f'{result["status"]}: {result["file"]}', flush=True)
    (args.output_dir / 'extraction-manifest.json').write_text(json.dumps(results, indent=2))
    failed = sum(r['status'] != 'OK' for r in results)
    print(f'{len(results) - failed}/{len(results)} extracted; {failed} failed. Existing matching outputs refreshed.')
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
