# Synthetic case: Northbridge Trading Ltd (fictional)

`generate_case.py` builds a fully synthetic insolvency case for testing the Insolvency Transaction Review Toolkit. No real company, person or bank data is used.

Output (`./data`):
- `Lloyds_41283719_Oct24-Mar26.csv` - Lloyds Commercial layout: single signed `Amount`, `Type` codes (BGC/FPO/DD/SO/TFR/DEB), `Balance`
- `NatWest_77120931_statement.xlsx` - NatWest layout: 4 preamble rows, `Money In` / `Money Out`, `Balance`
- `Barclays_90311276_export.csv` - Barclays layout: leading account line, `Paid In` / `Paid Out`, `Balance`
- `case_config.xlsx` - Case (procedure, dates, `Analysis_Anchor`, `Analysis_Lookback_Months`, optional override, `Review_Output_Mode`), Known_Parties, Thresholds
- `expected_flags.csv` - ground truth: 21 planted review items with rule id and reason
- `control_totals.csv` - rows, totals and closing balance per file for integrity checks

Case story: trading history from Oct 2024 to the appointment on 14 Mar 2026; receipts decline from Sep 2025, insolvency date 10 Mar 2026. Ordinary background generation stops at the appointment date, and only the two explicitly planted R12 items occur afterwards. Planted items cover rules R01, R03, R04, R05, R06, R09, R10, R12.

For the synthetic case, `New counterparty window (days)` is set to **180**. It is
only a configurable analytical recency parameter for R06; it is not presented as
a statutory insolvency look-back period.

The Jane Smith R01 test uses `JANE SMITH` in the narrative deliberately: `J SMITH` would be ambiguous because both John Smith and Jane Smith are in `Known_Parties`; the normaliser must not guess between them.

Reproducible: `python generate_case.py --out ./data --seed 42`.
