# Layer 1 - UK statement normalisation

`normalize_statements.py` reads heterogeneous UK-style bank statement exports and creates one traceable transaction register.

Implemented in the first version:
- CSV and XLS/XLSX ingestion;
- automatic header-row detection (including preamble rows);
- signed `Amount`, `Money In / Money Out`, `Paid In / Paid Out`, and `Debit / Credit` amount layouts;
- UK day-first date parsing;
- account/sort-code extraction from columns or preamble;
- best-effort counterparty extraction from transaction narrative;
- own-account transfer identification from `case_config.xlsx`;
- `Source_File` + physical `Source_Row` traceability;
- row, inflow, outflow and closing-balance reconciliation against `control_totals.csv`.

Example:

```bash
python normalize_statements.py \
  --input ../synthetic/data \
  --config ../synthetic/data/case_config.xlsx \
  --control ../synthetic/data/control_totals.csv \
  --output ../output/normalized_transactions.xlsx \
  --strict
```

The output workbook contains `Transactions` and `Integrity`. A CSV copy of the transaction register and a separate integrity CSV are written alongside it.
