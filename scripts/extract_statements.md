# Redacted statement extraction

Run `./pdfextract.sh` from any working directory (use its absolute path when outside the repository).
With no arguments it reads immediate `invest*` folders, case-insensitively, under
`/Users/haroldt/Documents/Accounting/accountLog/Schwab_Ameritrade.Investment`.
It includes filenames ending in `Statement.pdf` and `Brokerage Statement*_*.pdf`,
ignoring extension case. This includes older `2021_8_Statement.pdf` names.

Outputs go to ignored `data/input/statements/<source-folder>__<filename>.txt`.
A rerun refreshes matching files. Original PDFs are read only. Page markers are
preserved. Names (including reversed lettering) and account-number patterns are
redacted in memory before writing. The manifest records page counts, errors and
source SHA-256 hashes. This is identifier redaction, not full anonymisation:
addresses, financial transactions and other personal details can remain.

Optional arguments:

```bash
./pdfextract.sh '/path/to/statement.pdf'
./pdfextract.sh --source-dir '/path/to/archive' --output-dir data/input/statements --workers 4
```

Scanned pages require OCR and may produce no text; failed files are reported.
Extraction does not import transactions. Reconcile against broker exports before
adding missing trades. Statement activity can use settlement dates, and open
orders are not completed trades.
