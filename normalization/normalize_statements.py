"""
UK bank-statement normalisation for the Insolvency Transaction Review Toolkit.

Layer 1 only: ingest heterogeneous CSV/XLSX statements, detect the header and amount
layout, normalise core transaction fields, extract a best-effort counterparty, preserve
source traceability, and reconcile row/amount totals against an optional control file.

Usage:
    python normalize_statements.py \
        --input ../synthetic/data \
        --config ../synthetic/data/case_config.xlsx \
        --control ../synthetic/data/control_totals.csv \
        --output ../output/normalized_transactions.xlsx \
        --strict
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Header aliases / format detection
# ---------------------------------------------------------------------------
DATE_ALIASES = ["date", "transaction date", "posting date", "post date", "value date"]
DESC_ALIASES = [
    "transaction detail", "transaction details", "transaction description",
    "description", "narrative", "details", "memo",
]
REFERENCE_ALIASES = ["customer reference", "reference", "payment reference"]
TYPE_ALIASES = ["type", "transaction type", "category"]
BALANCE_ALIASES = ["balance", "running balance"]
ACCOUNT_ALIASES = ["account number", "account no", "account"]
ACCOUNT_NAME_ALIASES = ["account name"]
SORT_CODE_ALIASES = ["sort code"]
SIGNED_AMOUNT_ALIASES = ["amount", "transaction amount"]
MONEY_IN_ALIASES = ["money in", "paid in", "credit", "credits"]
MONEY_OUT_ALIASES = ["money out", "paid out", "debit", "debits"]

KNOWN_BANKS = {
    "lloyds": "Lloyds",
    "natwest": "NatWest",
    "barclays": "Barclays",
    "hsbc": "HSBC",
    "santander": "Santander",
    "metro": "Metro Bank",
    "starling": "Starling",
    "tide": "Tide",
    "monzo": "Monzo",
}

OUTPUT_COLUMNS = [
    "Txn_ID", "Date", "Bank", "Account", "Sort_Code", "Account_Name",
    "Type", "Description", "Reference",
    "Money_In", "Money_Out", "Amount_Signed", "Balance",
    "Counterparty_Raw", "Counterparty_Norm", "Counterparty_Sort_Code",
    "Counterparty_Account", "Is_Own_Account_Transfer",
    "Source_File", "Source_Row",
]


@dataclass
class KnownParty:
    name: str
    relationship: str
    sort_code: str = ""
    account_no: str = ""
    notes: str = ""


@dataclass
class CaseConfig:
    company_name: str = ""
    currency: str = "GBP"
    known_parties: list[KnownParty] | None = None

    @property
    def own_accounts(self) -> dict[tuple[str, str], KnownParty]:
        out: dict[tuple[str, str], KnownParty] = {}
        for p in self.known_parties or []:
            if p.relationship.strip().lower() == "own account" and p.account_no:
                out[(norm_sort_code(p.sort_code), digits_only(p.account_no))] = p
        return out


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------
def norm_header(value) -> str:
    value = "" if pd.isna(value) else str(value)
    value = value.replace("\n", " ").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip().lower()


def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\u00a0", " ")).strip()


def digits_only(value) -> str:
    if pd.isna(value):
        return ""
    # Excel often turns an 8-digit account number into 77120931.0
    text = str(value).strip()
    text = re.sub(r"\.0$", "", text)
    return re.sub(r"\D", "", text)


def norm_sort_code(value) -> str:
    d = digits_only(value)
    if len(d) == 6:
        return f"{d[:2]}-{d[2:4]}-{d[4:]}"
    return clean_text(value)


def find_column(columns: Iterable[str], aliases: list[str]) -> str | None:
    cols = list(columns)
    normalized = {c: norm_header(c) for c in cols}
    # exact first
    for a in aliases:
        aa = norm_header(a)
        for c in cols:
            if normalized[c] == aa:
                return c
    # then contained token, but only for aliases longer than 4 chars
    for a in aliases:
        aa = norm_header(a)
        if len(aa) <= 4:
            continue
        for c in cols:
            if aa in normalized[c]:
                return c
    return None


def parse_money(series: pd.Series) -> pd.Series:
    s = series.astype("string").fillna("").str.strip()
    s = s.str.replace("£", "", regex=False).str.replace(",", "", regex=False)
    s = s.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    s = s.str.replace(r"\s+", "", regex=True)
    return pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)


def parse_dates(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce").dt.date
    # UK statements are day-first. pandas mixed handles 01/10/2024 and 21 Oct 2024.
    return pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed").dt.date


def infer_bank(path: Path, preamble: str = "") -> str:
    haystack = f"{path.stem} {preamble}".lower()
    for key, name in KNOWN_BANKS.items():
        if key in haystack:
            return name
    return path.stem.split("_")[0]


def standardize_party_name(value: str) -> str:
    text = clean_text(value).upper()
    if not text:
        return ""
    text = text.replace("&", " AND ")
    text = re.sub(r"[‘’`´]", "'", text)
    text = re.sub(r"[\"“”]", "", text)
    text = re.sub(r"\bLIMITED\b", "LTD", text)
    text = re.sub(r"\bPUBLIC\s+LIMITED\s+COMPANY\b", "PLC", text)
    text = re.sub(r"\bL\.L\.P\.\b", "LLP", text)
    text = re.sub(r"\bL\.T\.D\.\b", "LTD", text)
    text = re.sub(r"\bP\.L\.C\.\b", "PLC", text)
    text = re.sub(r"[^A-Z0-9'&\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_case_config(path: Path | None) -> CaseConfig:
    if not path or not path.exists():
        return CaseConfig(known_parties=[])

    case_df = pd.read_excel(path, sheet_name="Case")
    case_map = {str(r["Field"]): r["Value"] for _, r in case_df.iterrows()}

    kp_df = pd.read_excel(path, sheet_name="Known_Parties").fillna("")
    parties: list[KnownParty] = []
    for _, r in kp_df.iterrows():
        parties.append(KnownParty(
            name=clean_text(r.get("Name", "")),
            relationship=clean_text(r.get("Relationship", "")),
            sort_code=norm_sort_code(r.get("Sort code", "")),
            account_no=digits_only(r.get("Account no.", "")),
            notes=clean_text(r.get("Notes", "")),
        ))

    return CaseConfig(
        company_name=clean_text(case_map.get("Company name", "")),
        currency=clean_text(case_map.get("Currency", "GBP")) or "GBP",
        known_parties=parties,
    )


# ---------------------------------------------------------------------------
# Header detection and raw reading
# ---------------------------------------------------------------------------
def score_header_cells(cells: list[str]) -> int:
    vals = {norm_header(c) for c in cells if clean_text(c)}
    if not vals:
        return 0

    score = 0
    if any(a in vals for a in DATE_ALIASES):
        score += 4
    if any(a in vals for a in DESC_ALIASES):
        score += 3
    if any(a in vals for a in SIGNED_AMOUNT_ALIASES):
        score += 4
    if any(a in vals for a in MONEY_IN_ALIASES) and any(a in vals for a in MONEY_OUT_ALIASES):
        score += 5
    if any(a in vals for a in BALANCE_ALIASES):
        score += 1
    if any(a in vals for a in TYPE_ALIASES):
        score += 1
    return score


def detect_csv_header(path: Path, max_rows: int = 80) -> tuple[int, list[list[str]]]:
    preview: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            preview.append(row)
            if i + 1 >= max_rows:
                break
    if not preview:
        return 0, preview
    scores = [score_header_cells(row) for row in preview]
    best = int(np.argmax(scores))
    return (best if scores[best] >= 7 else 0), preview


def detect_excel_header(path: Path, max_rows: int = 80) -> tuple[int, pd.DataFrame]:
    preview = pd.read_excel(path, sheet_name=0, header=None, nrows=max_rows)
    scores = [score_header_cells([x for x in preview.iloc[i].tolist() if pd.notna(x)]) for i in range(len(preview))]
    if not scores:
        return 0, preview
    best = int(np.argmax(scores))
    return (best if scores[best] >= 7 else 0), preview


def read_raw_statement(path: Path) -> tuple[pd.DataFrame, int, str]:
    ext = path.suffix.lower()
    if ext == ".csv":
        header_idx, preview = detect_csv_header(path)
        preamble = " ".join(" ".join(r) for r in preview[:header_idx])
        raw = pd.read_csv(path, skiprows=header_idx, header=0, dtype=object, encoding="utf-8-sig")
    elif ext in {".xlsx", ".xls"}:
        header_idx, preview_df = detect_excel_header(path)
        preamble = " ".join(clean_text(x) for x in preview_df.iloc[:header_idx].to_numpy().ravel() if pd.notna(x))
        raw = pd.read_excel(path, sheet_name=0, header=header_idx, dtype=object)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")

    raw.columns = [clean_text(c) for c in raw.columns]
    raw = raw.dropna(how="all").copy()
    # Physical source row: header line is header_idx+1, first data row header_idx+2.
    raw["__Source_Row"] = np.arange(len(raw), dtype=int) + header_idx + 2
    return raw, header_idx, preamble


# ---------------------------------------------------------------------------
# Account / counterparty extraction
# ---------------------------------------------------------------------------
ACCOUNT_PAIR_RE = re.compile(r"(?<!\d)(\d{2}-?\d{2}-?\d{2})\s+(\d{8})(?!\d)")


def parse_account_from_preamble_or_filename(preamble: str, path: Path) -> tuple[str, str]:
    m = ACCOUNT_PAIR_RE.search(preamble)
    if m:
        return norm_sort_code(m.group(1)), digits_only(m.group(2))
    # filename usually includes account number; sort code may not be available
    m_acc = re.search(r"(?<!\d)(\d{8})(?!\d)", path.stem)
    return "", (m_acc.group(1) if m_acc else "")


def extract_known_party_from_description(desc: str, config: CaseConfig) -> tuple[str, bool]:
    """Return a canonical known party only for unambiguous strong matches."""
    dnorm = standardize_party_name(desc)
    matches: list[KnownParty] = []
    for p in config.known_parties or []:
        pnorm = standardize_party_name(p.name)
        if not pnorm:
            continue
        if pnorm in dnorm:
            matches.append(p)
            continue
        # Person alias: first initial + surname, but only if it resolves to exactly one known person.
        parts = pnorm.split()
        if len(parts) >= 2 and p.relationship.lower() in {"director", "director's relative"}:
            alias = f"{parts[0][0]} {parts[-1]}"
            if re.search(rf"\b{re.escape(alias)}\b", dnorm):
                matches.append(p)

    unique_names = {m.name for m in matches}
    if len(unique_names) == 1:
        return matches[0].name, True
    return "", False


def extract_counterparty(desc: str, tx_type: str, bank: str, config: CaseConfig) -> tuple[str, str, str, bool]:
    """Best-effort counterparty extraction.

    Returns (counterparty_raw, counterparty_sort_code, counterparty_account, own_transfer).
    It deliberately avoids guessing when an abbreviation can identify multiple known people.
    """
    text = clean_text(desc)
    upper = text.upper()

    # Transfer account pair first, because the description may contain no party name.
    account_match = ACCOUNT_PAIR_RE.search(upper)
    if account_match and (tx_type.upper() == "TFR" or upper.startswith("TFR ")):
        sort_code = norm_sort_code(account_match.group(1))
        account = digits_only(account_match.group(2))
        own = config.own_accounts.get((sort_code, account))
        if own:
            return own.name, sort_code, account, True
        return "Unknown account", sort_code, account, False

    if upper.startswith("CASH") or tx_type.upper() == "CASH":
        return "CASH", "", "", False
    if upper in {"ACCOUNT FEE", "ACCOUNT CHARGES"} or upper.startswith("ACCOUNT FEE") or upper.startswith("ACCOUNT CHARGES"):
        return bank, "", "", False

    # Exact/full known party match is strongest. Initial + surname is accepted only if unique.
    known, matched = extract_known_party_from_description(text, config)
    if matched:
        return known, "", "", False

    work = upper
    # Transaction prefixes.
    work = re.sub(r"^(?:FPO|FPI|BGC|DD|SO|DEB|CR|CHQ)\s+", "", work)
    work = re.sub(r"^CARD\s+PAYMENT\s+", "", work)
    work = re.sub(r"^CARD\s+", "", work)

    # Company legal suffix gives a clean boundary even when purpose text follows.
    m_company = re.match(r"(.+?\b(?:LTD|LIMITED|PLC|LLP)\b)", work)
    if m_company:
        return clean_text(m_company.group(1).title()), "", "", False

    # References/invoice numbers are reliable delimiters in the synthetic banks.
    work = re.split(r"\s+(?:REF|INV)\s*[-:#]?[A-Z0-9-]*", work, maxsplit=1)[0]
    work = re.sub(r"\s+INV\d+\b.*$", "", work)
    # Payroll month suffix.
    work = re.sub(r"\s+SALARY\s+[A-Z]{3}\d{2}\b.*$", "", work)
    # HMRC descriptions.
    if work.startswith("HMRC"):
        return "HMRC", "", "", False
    # Common purpose suffixes when the payee has no legal suffix.
    work = re.sub(r"\s+(?:RENT|REPAYMENT|DIRECTOR LOAN|DIVIDEND|ADVISORY|MGMT FEES|CONSULTANCY Q\d|FINAL SETTLEMENT|INTERCO)\b.*$", "", work)
    work = clean_text(work)
    return (work.title() if work else ""), "", "", False


# ---------------------------------------------------------------------------
# Statement normalisation
# ---------------------------------------------------------------------------
def normalise_statement(path: Path, config: CaseConfig) -> pd.DataFrame:
    raw, header_idx, preamble = read_raw_statement(path)
    if raw.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    cols = [c for c in raw.columns if c != "__Source_Row"]
    date_col = find_column(cols, DATE_ALIASES)
    desc_col = find_column(cols, DESC_ALIASES)
    ref_col = find_column(cols, REFERENCE_ALIASES)
    type_col = find_column(cols, TYPE_ALIASES)
    balance_col = find_column(cols, BALANCE_ALIASES)
    account_col = find_column(cols, ACCOUNT_ALIASES)
    account_name_col = find_column(cols, ACCOUNT_NAME_ALIASES)
    sort_col = find_column(cols, SORT_CODE_ALIASES)
    signed_col = find_column(cols, SIGNED_AMOUNT_ALIASES)
    in_col = find_column(cols, MONEY_IN_ALIASES)
    out_col = find_column(cols, MONEY_OUT_ALIASES)

    if not date_col:
        raise ValueError(f"{path.name}: date column not found")
    if not desc_col:
        # Reference can be the only narrative column in some exports.
        desc_col = ref_col
    if not desc_col:
        raise ValueError(f"{path.name}: description/narrative column not found")

    amount_scheme = ""
    if signed_col:
        amount_scheme = "signed"
    elif in_col and out_col:
        amount_scheme = "split"
    else:
        raise ValueError(f"{path.name}: amount columns not recognised")

    bank = infer_bank(path, preamble)
    pre_sort, pre_acc = parse_account_from_preamble_or_filename(preamble, path)

    dates = parse_dates(raw[date_col])
    descriptions = raw[desc_col].fillna("").astype(str).map(clean_text)
    references = raw[ref_col].fillna("").astype(str).map(clean_text) if ref_col and ref_col != desc_col else pd.Series("", index=raw.index)
    types = raw[type_col].fillna("").astype(str).map(clean_text) if type_col else pd.Series("", index=raw.index)
    balances = parse_money(raw[balance_col]) if balance_col else pd.Series(np.nan, index=raw.index, dtype=float)

    if amount_scheme == "signed":
        signed = parse_money(raw[signed_col])
        money_in = signed.clip(lower=0)
        money_out = (-signed.clip(upper=0))
    else:
        money_in = parse_money(raw[in_col])
        money_out = parse_money(raw[out_col])
        signed = money_in - money_out

    if account_col:
        account_series = raw[account_col].map(digits_only)
    else:
        account_series = pd.Series(pre_acc, index=raw.index)

    if sort_col:
        sort_series = raw[sort_col].map(norm_sort_code)
    else:
        sort_series = pd.Series(pre_sort, index=raw.index)

    if account_name_col:
        account_name = raw[account_name_col].fillna("").astype(str).map(clean_text)
    else:
        # Resolve own account name by sort/account or use case company name.
        names = []
        for sc, ac in zip(sort_series, account_series):
            p = config.own_accounts.get((norm_sort_code(sc), digits_only(ac)))
            names.append(p.name if p else config.company_name)
        account_name = pd.Series(names, index=raw.index)

    out = pd.DataFrame({
        "Date": dates,
        "Bank": bank,
        "Account": account_series,
        "Sort_Code": sort_series,
        "Account_Name": account_name,
        "Type": types,
        "Description": descriptions,
        "Reference": references,
        "Money_In": money_in.round(2),
        "Money_Out": money_out.round(2),
        "Amount_Signed": signed.round(2),
        "Balance": balances.round(2),
        "Source_File": path.name,
        "Source_Row": raw["__Source_Row"].astype(int),
    })

    # Remove non-transaction lines that happened to survive header detection.
    out = out[out["Date"].notna()].copy()
    out = out[(out["Money_In"].abs() > 0) | (out["Money_Out"].abs() > 0) | out["Description"].ne("")].copy()

    cps = [extract_counterparty(d, t, bank, config) for d, t in zip(out["Description"], out["Type"])]
    out["Counterparty_Raw"] = [x[0] for x in cps]
    out["Counterparty_Norm"] = out["Counterparty_Raw"].map(standardize_party_name)
    out["Counterparty_Sort_Code"] = [x[1] for x in cps]
    out["Counterparty_Account"] = [x[2] for x in cps]
    out["Is_Own_Account_Transfer"] = [bool(x[3]) for x in cps]

    # Stable transaction id based on source location (reproducible and traceable).
    bank_key = re.sub(r"[^A-Za-z0-9]+", "", bank).upper() or "BANK"
    out["Txn_ID"] = [f"{bank_key}-{path.stem}-{int(r)}" for r in out["Source_Row"]]

    return out[OUTPUT_COLUMNS]


# ---------------------------------------------------------------------------
# Integrity checks and writers
# ---------------------------------------------------------------------------
def statement_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["file", "bank", "rows", "total_in", "total_out", "closing_balance"])
    rows = []
    for source_file, g in df.groupby("Source_File", sort=False):
        g = g.sort_values(["Date", "Source_Row"])
        bank = str(g["Bank"].iloc[0])
        closing = g["Balance"].dropna()
        rows.append({
            "file": bank.lower().replace(" bank", ""),
            "bank": bank,
            "source_file": source_file,
            "rows": int(len(g)),
            "total_in": round(float(g["Money_In"].sum()), 2),
            "total_out": round(float(g["Money_Out"].sum()), 2),
            "closing_balance": round(float(closing.iloc[-1]), 2) if len(closing) else np.nan,
        })
    return pd.DataFrame(rows)


def compare_control(actual: pd.DataFrame, control_path: Path | None) -> pd.DataFrame:
    if not control_path or not control_path.exists():
        actual = actual.copy()
        actual["status"] = "NO CONTROL"
        return actual

    expected = pd.read_csv(control_path)
    merged = expected.merge(actual, on="file", how="outer", suffixes=("_expected", "_actual"), indicator=True)
    for col in ["rows", "total_in", "total_out", "closing_balance"]:
        exp = f"{col}_expected"
        act = f"{col}_actual"
        if col == "rows":
            merged[f"{col}_ok"] = merged[exp].fillna(-1).astype(float).eq(merged[act].fillna(-2).astype(float))
        else:
            merged[f"{col}_ok"] = np.isclose(
                merged[exp].fillna(np.nan).astype(float),
                merged[act].fillna(np.nan).astype(float),
                rtol=0, atol=0.01, equal_nan=False,
            )
    checks = ["rows_ok", "total_in_ok", "total_out_ok", "closing_balance_ok"]
    merged["status"] = np.where((merged["_merge"] == "both") & merged[checks].all(axis=1), "PASS", "FAIL")
    return merged


def write_outputs(df: pd.DataFrame, output_xlsx: Path, integrity: pd.DataFrame) -> None:
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_xlsx.with_suffix(".csv")
    integrity_path = output_xlsx.with_name(output_xlsx.stem + "_integrity.csv")

    export = df.copy()
    export["Date"] = pd.to_datetime(export["Date"], errors="coerce")
    export.to_csv(csv_path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    integrity.to_csv(integrity_path, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        export.to_excel(writer, sheet_name="Transactions", index=False)
        integrity.to_excel(writer, sheet_name="Integrity", index=False)
        ws = writer.sheets["Transactions"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        widths = {
            "A": 28, "B": 12, "C": 12, "D": 12, "E": 12, "F": 28,
            "G": 10, "H": 52, "I": 22, "J": 14, "K": 14, "L": 16,
            "M": 14, "N": 34, "O": 34, "P": 16, "Q": 18, "R": 16,
            "S": 38, "T": 12,
        }
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
        # Excel-friendly formats.
        for cell in ws["B"][1:]:
            cell.number_format = "DD/MM/YYYY"
        for col in ["J", "K", "L", "M"]:
            for cell in ws[col][1:]:
                cell.number_format = '#,##0.00;[Red]-#,##0.00'

    print(f"Transactions: {len(df):,}")
    print(f"Excel: {output_xlsx}")
    print(f"CSV: {csv_path}")
    print(f"Integrity: {integrity_path}")


def discover_statement_files(input_dir: Path) -> list[Path]:
    excluded = {"case_config.xlsx", "expected_flags.csv", "control_totals.csv"}
    files = []
    for p in sorted(input_dir.iterdir()):
        if p.name in excluded or p.name.startswith("~$"):
            continue
        if p.suffix.lower() in {".csv", ".xlsx", ".xls"}:
            files.append(p)
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Normalise UK bank statements into one traceable transaction register.")
    ap.add_argument("--input", required=True, help="Folder containing statement CSV/XLSX files")
    ap.add_argument("--config", help="case_config.xlsx")
    ap.add_argument("--control", help="control_totals.csv for integrity validation")
    ap.add_argument("--output", default="normalized_transactions.xlsx")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if any integrity check fails")
    args = ap.parse_args(argv)

    input_dir = Path(args.input).resolve()
    config_path = Path(args.config).resolve() if args.config else None
    control_path = Path(args.control).resolve() if args.control else None
    output_path = Path(args.output).resolve()

    config = load_case_config(config_path)
    files = discover_statement_files(input_dir)
    if not files:
        print(f"No statement files found in {input_dir}", file=sys.stderr)
        return 2

    frames = []
    for p in files:
        try:
            part = normalise_statement(p, config)
            print(f"OK  {p.name}: {len(part):,} rows")
            frames.append(part)
        except Exception as exc:
            print(f"ERR {p.name}: {exc}", file=sys.stderr)
            if args.strict:
                return 3

    if not frames:
        print("No statements were normalised", file=sys.stderr)
        return 4

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(["Date", "Bank", "Source_File", "Source_Row"], kind="stable").reset_index(drop=True)
    actual = statement_summary(result)
    integrity = compare_control(actual, control_path)
    write_outputs(result, output_path, integrity)

    show_cols = [c for c in ["file", "rows_expected", "rows_actual", "total_in_expected", "total_in_actual",
                                "total_out_expected", "total_out_actual", "closing_balance_expected",
                                "closing_balance_actual", "status"] if c in integrity.columns]
    print("\nIntegrity checks:")
    print(integrity[show_cols].to_string(index=False))

    if args.strict and "status" in integrity and (integrity["status"] != "PASS").any():
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
