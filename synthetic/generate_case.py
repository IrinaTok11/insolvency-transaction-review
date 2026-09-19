"""
Synthetic case generator - Northbridge Trading Ltd (fictional).
Produces three bank statement files in three real UK export layouts, a case_config.xlsx
and expected_flags.csv (ground truth for rule validation). All data is synthetic.

Usage: python generate_case.py [--out ./data] [--seed 42]
"""
import argparse, random
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

# ----------------------------------------------------------------------------- case facts
COMPANY = "Northbridge Trading Ltd"
DIRECTOR = "John Smith"
START, END = date(2024, 10, 1), date(2026, 3, 31)          # 18 months
INSOLVENCY_DATE = date(2026, 3, 10)                          # winding-up / administration
APPOINTMENT_DATE = date(2026, 3, 14)
# analysis period is now defined in case_config: Analysis_Anchor=Appointment, Analysis_Lookback_Months=24 (demo values)

ACCOUNTS = {
    "lloyds":   {"bank": "Lloyds",   "sort": "30-96-26", "acc": "41283719", "name": "Northbridge Trading Ltd Current", "opening": 184_320.55},
    "natwest":  {"bank": "NatWest",  "sort": "60-12-44", "acc": "77120931", "name": "NORTHBRIDGE TRADING LTD",         "opening": 42_810.10},
    "barclays": {"bank": "Barclays", "sort": "20-45-18", "acc": "90311276", "name": "Northbridge Trading Limited",    "opening": 9_450.00},
}
KNOWN_PARTIES = [
    ("John Smith",                 "Director",            "", "",          "Sole director"),
    ("Smith Consulting Ltd",       "Connected company",   "", "",          "Owned by director"),
    ("Jane Smith",                 "Director's relative", "", "",          "Spouse"),
    ("Northbridge Holdings Ltd",   "Parent company",      "", "",          ""),
    ("Northbridge Trading Ltd",    "Own account",         "60-12-44", "77120931", "NatWest"),
    ("Northbridge Trading Ltd",    "Own account",         "20-45-18", "90311276", "Barclays"),
    ("Northbridge Trading Ltd",    "Own account",         "30-96-26", "41283719", "Lloyds"),
    ("Hartwell Finance plc",       "Secured lender",      "", "",          "Asset finance"),
    ("HMRC",                       "HMRC",                "", "",          ""),
]
THRESHOLDS = {"High value": 10_000, "Cash withdrawal": 1_000, "Round amount step": 1_000,
              "Round amount minimum": 5_000, "New counterparty first payment": 5_000,
              # Analytical recency setting only, not a statutory look-back period.
              "New counterparty window (days)": 180}

SUPPLIERS = ["Meridian Packaging Ltd","Coastline Logistics Ltd","Ashford Steel Supplies","Brightpath Print Ltd","Oakline Office Ltd",
             "Pennant Freight Ltd","Fenwick Components Ltd","Halcyon IT Services","Redgate Cleaning Ltd","Stonebridge Insurance Brokers",
             "Kestrel Fuel Cards","Lumen Energy Ltd","Thames Water","British Gas Business","BT Business","Vodafone Business",
             "Ridley Tools Ltd","Sable Marketing Ltd","Carrow Security Ltd","Wexford Catering Ltd","Ellery Legal LLP","Bramble Accountants",
             "Northgate Vehicle Hire","Amazon Business","Screwfix","Travis Perkins","Royal Mail","DHL Express","Sage Software","Microsoft"]
CUSTOMERS = ["Alderton Retail Ltd","Bexley Homeware plc","Castleford Stores","Denby Garden Centres","Everly Interiors Ltd",
             "Fairbank Wholesale","Glenmore Trading","Hexham Supplies Ltd","Isley & Co","Jarrow Builders Merchants",
             "Kingsmead Distribution","Larkhill Ltd","Marsden DIY","Newham Hardware","Orwell Furnishings Ltd",
             "Pembroke Living","Quayside Retail","Ravenscourt Ltd","Selby Home Stores","Tamar Traders"]
EMPLOYEES = ["A PATEL","S OKAFOR","L KOWALSKI","M HUGHES","R DAVIES","E MORGAN","T NGUYEN","C WALSH","D O'BRIEN","H SCHMIDT","P SINGH","J TAYLOR"]

# ----------------------------------------------------------------------------- helpers
def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)

def bizday(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d

class Ledger:
    def __init__(self):
        self.rows = []      # dict(acc, date, type, counterparty, desc, amount(+in/-out), flag_id or None)
        self.flags = []     # expected flags
    def add(self, acc, d, typ, cp, desc, amount, flag=None, reason=None, rule=None):
        self.rows.append(dict(acc=acc, date=d, type=typ, cp=cp, desc=desc, amount=round(amount, 2)))
        if flag:
            self.flags.append(dict(flag_id=flag, rule=rule, account=ACCOUNTS[acc]["bank"], date=d, counterparty=cp,
                                   amount=round(amount, 2), reason=reason))
            self.rows[-1]["flag"] = flag

def gen(seed):
    rnd = random.Random(seed)
    L = Ledger()

    # --- background: customer receipts (Lloyds main), suppliers, payroll, HMRC, DD utilities, card, fees
    # Ordinary trading background stops at the appointment date. Post-appointment
    # transactions are planted explicitly below so R12 has deterministic ground truth.
    for d in daterange(START, APPOINTMENT_DATE):
        if d.weekday() < 5:
            for _ in range(rnd.choice([1, 2, 2, 3, 3, 4])):
                c = rnd.choice(CUSTOMERS)
                decline = 1.0 if d < date(2025, 9, 1) else max(0.45, 1.0 - (d - date(2025, 9, 1)).days / 300)
                L.add("lloyds", d, "BGC", c, f"BGC {c.upper()} INV{rnd.randint(10000,99999)}", rnd.uniform(700, 8_500) * decline)
            for _ in range(rnd.choice([1, 1, 2, 2, 3])):
                s = rnd.choice(SUPPLIERS[:22])
                L.add("lloyds", d, "FPO", s, f"FPO {s.upper()} REF {rnd.randint(1000,9999)}", -rnd.uniform(150, 6_000))
            if rnd.random() < 0.25:
                s = rnd.choice(SUPPLIERS[22:])
                L.add("barclays", d, "CARD", s, f"CARD PAYMENT {s.upper()}", -rnd.uniform(20, 480))
        if d.day == 25:  # payroll from NatWest
            for e in EMPLOYEES:
                L.add("natwest", bizday(d), "FPO", e, f"FPO {e} SALARY {d.strftime('%b%y').upper()}", -rnd.uniform(1_900, 3_600))
        if d.day == 22:
            L.add("natwest", bizday(d), "FPO", "HMRC", f"FPO HMRC PAYE {d.strftime('%m%y')}", -rnd.uniform(9_000, 13_500))
        if d.day == 7 and d.month in (1, 4, 7, 10):
            L.add("lloyds", bizday(d), "FPO", "HMRC", "FPO HMRC VAT", -rnd.uniform(18_000, 34_000))
        if d.day == 1:
            L.add("lloyds", bizday(d), "SO", "Ashcombe Estates Ltd", "SO ASHCOMBE ESTATES RENT", -6_250.00)
            L.add("lloyds", bizday(d), "DD", "Hartwell Finance plc", "DD HARTWELL FINANCE PLC", -3_180.40)
        if d.day == 12:
            for u, a in [("British Gas Business", -640), ("Thames Water", -215), ("BT Business", -389), ("Lumen Energy Ltd", -1_120)]:
                L.add("lloyds", bizday(d), "DD", u, f"DD {u.upper()}", a * rnd.uniform(0.85, 1.15))
        if d.day == 28:
            L.add("lloyds", bizday(d), "DEB", "Lloyds Bank", "ACCOUNT FEE", -rnd.uniform(35, 60))
            L.add("natwest", bizday(d), "DEB", "NatWest", "ACCOUNT CHARGES", -rnd.uniform(20, 40))
        # regular inter-account top-ups (legit own transfers)
        if d.day == 20:
            L.add("lloyds", bizday(d), "TFR", "Northbridge Trading Ltd", "TFR TO 60-12-44 77120931", -56_000.00)
            L.add("natwest", bizday(d), "TFR", "Northbridge Trading Ltd", "TFR FROM 30-96-26 41283719", 56_000.00)
        if d.day == 15:
            L.add("lloyds", bizday(d), "TFR", "Northbridge Trading Ltd", "TFR TO 20-45-18 90311276", -4_000.00)
            L.add("barclays", bizday(d), "TFR", "Northbridge Trading Ltd", "TFR FROM 30-96-26 41283719", 4_000.00)
        # small legit cash withdrawals (below threshold)
        if rnd.random() < 0.03:
            L.add("barclays", d, "CASH", "CASH", f"CASH WDL {d.strftime('%d%b').upper()}", -rnd.choice([100, 150, 200, 250, 300]))
        # director's modest monthly salary (legit)
        if d.day == 25:
            L.add("natwest", bizday(d), "FPO", "John Smith", f"FPO J SMITH SALARY {d.strftime('%b%y').upper()}", -4_200.00)

    # --- planted review items (ground truth)
    P = lambda days: bizday(INSOLVENCY_DATE - timedelta(days=days))
    # R01 connected party payments
    L.add("lloyds", P(150), "FPO", "Smith Consulting Ltd", "FPO SMITH CONSULTING LTD MGMT FEES", -18_500.00, "F01", "Payment to known connected party: Smith Consulting Ltd (Connected company)", "R01")
    L.add("lloyds", P(95),  "FPO", "Smith Consulting Ltd", "FPO SMITH CONSULTING LTD CONSULTANCY Q4", -24_000.00, "F02", "Payment to known connected party: Smith Consulting Ltd (Connected company)", "R01")
    L.add("lloyds", P(41),  "FPO", "Smith Consulting Ltd", "FPO SMITH CONSULTING LTD FINAL SETTLEMENT", -62_900.00, "F03", "Payment to known connected party: Smith Consulting Ltd (Connected company)", "R01")
    L.add("natwest", P(60), "FPO", "Jane Smith", "FPO JANE SMITH REPAYMENT", -15_000.00, "F04", "Payment to known connected party: Jane Smith (Director's relative)", "R01")
    L.add("lloyds", P(33),  "FPO", "Northbridge Holdings Ltd", "FPO NORTHBRIDGE HOLDINGS LTD INTERCO", -66_000.00, "F05", "Payment to known connected party: Northbridge Holdings Ltd (Parent company)", "R01")
    # R10 loan / dividend / director keywords
    L.add("natwest", P(120), "FPO", "John Smith", "FPO J SMITH DIRECTOR LOAN", -30_000.00, "F06", "Description indicates loan/dividend/director payment", "R10")
    L.add("lloyds", P(72),  "FPO", "John Smith", "FPO J SMITH DIVIDEND", -40_000.00, "F07", "Description indicates loan/dividend/director payment", "R10")
    # R03 high value to ordinary payee (unusual)
    L.add("lloyds", P(200), "FPO", "Ellery Legal LLP", "FPO ELLERY LEGAL LLP", -14_750.00, "F08", "High-value transaction above £10,000", "R03")
    L.add("lloyds", P(52),  "FPO", "Sable Marketing Ltd", "FPO SABLE MARKETING LTD", -21_300.00, "F09", "High-value transaction above £10,000", "R03")
    # R04 cash withdrawals above threshold / repeated
    for i, days in enumerate([28, 24, 21, 18, 14, 11]):
        L.add("barclays", P(days), "CASH", "CASH", f"CASH WDL {P(days).strftime('%d%b').upper()}", -rnd.choice([1_500, 2_000, 2_500, 3_000]),
              f"F1{i}", "Cash withdrawal above £1,000; repeated cash withdrawals in 30 days", "R04")
    # R05 round-amount large payments to a new payee
    L.add("lloyds", P(88), "FPO", "Vantage Property Services Ltd", "FPO VANTAGE PROPERTY SERVICES LTD", -25_000.00, "F16", "Round-amount payment (£25,000); new counterparty within analysis period; first payment £25,000", "R05")
    L.add("lloyds", P(47), "FPO", "Vantage Property Services Ltd", "FPO VANTAGE PROPERTY SERVICES LTD", -30_000.00, "F17", "Round-amount payment (£30,000)", "R05")
    # R06 new high-value counterparty
    L.add("lloyds", P(66), "FPO", "Delta Asset Partners LLP", "FPO DELTA ASSET PARTNERS LLP ADVISORY", -12_400.00, "F18", "New counterparty within analysis period; first payment £12,400", "R06")
    # R09 transfer to unidentified account
    L.add("lloyds", P(38), "TFR", "Unknown account", "TFR TO 40-11-07 55820019", -20_000.00, "F19", "Transfer to unidentified account 40-11-07 55820019", "R09")
    # R12 post-appointment transactions
    L.add("barclays", APPOINTMENT_DATE + timedelta(days=2), "CARD", "Amazon Business", "CARD PAYMENT AMAZON BUSINESS", -412.60, "F20", "Post-appointment transaction - verify authority and purpose", "R12")
    L.add("lloyds", APPOINTMENT_DATE + timedelta(days=4), "FPO", "Smith Consulting Ltd", "FPO SMITH CONSULTING LTD", -4_000.00, "F21", "Post-appointment transaction - verify authority and purpose; payment to known connected party", "R12")
    return L

# ----------------------------------------------------------------------------- writers
def with_balance(rows, opening):
    rows = sorted(rows, key=lambda r: (r["date"], r["type"], r["desc"]))
    bal = opening
    for r in rows:
        bal = round(bal + r["amount"], 2)
        r["balance"] = bal
    return rows

def write_lloyds(rows, out):
    # Lloyds Commercial CSV: single signed Amount + Type + Balance, header on row 1
    df = pd.DataFrame([{"Post Date": r["date"].strftime("%d/%m/%Y"), "Account Number": ACCOUNTS["lloyds"]["acc"],
                        "Account Name": ACCOUNTS["lloyds"]["name"], "Type": r["type"], "Amount": f"{r['amount']:.2f}",
                        "Customer Reference": r["desc"].split(" REF ")[-1] if " REF " in r["desc"] else "",
                        "Transaction Detail": r["desc"], "Balance": f"{r['balance']:.2f}"} for r in rows])
    df.to_csv(out / "Lloyds_41283719_Oct24-Mar26.csv", index=False)

def write_natwest(rows, out):
    # NatWest XLSX: 3 preamble rows, Money In / Money Out, running balance
    data = [{"Date": r["date"].strftime("%d %b %Y"), "Type": r["type"], "Description": r["desc"],
             "Money In": r["amount"] if r["amount"] > 0 else None, "Money Out": -r["amount"] if r["amount"] < 0 else None,
             "Balance": r["balance"]} for r in rows]
    df = pd.DataFrame(data)
    path = out / "NatWest_77120931_statement.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pre = pd.DataFrame([["Business Current Account"], [f"Account name: {ACCOUNTS['natwest']['name']}"],
                            [f"Sort code 60-12-44  Account number 77120931"], [""]])
        pre.to_excel(w, index=False, header=False, startrow=0, sheet_name="Statement")
        df.to_excel(w, index=False, startrow=4, sheet_name="Statement")

def write_barclays(rows, out):
    # Barclays CSV: Paid In / Paid Out, Date, Description, Balance; first line "Statement for account ..."
    path = out / "Barclays_90311276_export.csv"
    df = pd.DataFrame([{"Date": r["date"].strftime("%d/%m/%Y"), "Description": r["desc"],
                        "Paid In": f"{r['amount']:.2f}" if r["amount"] > 0 else "",
                        "Paid Out": f"{-r['amount']:.2f}" if r["amount"] < 0 else "",
                        "Balance": f"{r['balance']:.2f}"} for r in rows])
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(f"Statement for account 20-45-18 90311276,{ACCOUNTS['barclays']['name']}\n")
        df.to_csv(f, index=False)

def write_config(out):
    with pd.ExcelWriter(out / "case_config.xlsx", engine="openpyxl") as w:
        pd.DataFrame({"Field": ["Company name", "Procedure", "Appointment date", "Insolvency date", "Petition_Date", "Resolution_Date",
                                "Analysis_Anchor", "Analysis_Anchor_Date", "Analysis_Lookback_Months", "Analysis_Period_Start_Override", "Review_Output_Mode", "Currency"],
                      "Value": [COMPANY, "CVL", APPOINTMENT_DATE, INSOLVENCY_DATE, None, INSOLVENCY_DATE,
                                "Appointment", None, 24, None, "All flagged", "GBP"]}).to_excel(w, index=False, sheet_name="Case")
        pd.DataFrame(KNOWN_PARTIES, columns=["Name", "Relationship", "Sort code", "Account no.", "Notes"]).astype(str).replace("", None).to_excel(w, index=False, sheet_name="Known_Parties")
        pd.DataFrame({"Threshold": list(THRESHOLDS), "Value": list(THRESHOLDS.values())}).to_excel(w, index=False, sheet_name="Thresholds")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./data"); ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    L = gen(a.seed)
    by = {k: [r for r in L.rows if r["acc"] == k] for k in ACCOUNTS}
    for k in by:
        by[k] = with_balance(by[k], ACCOUNTS[k]["opening"])
    write_lloyds(by["lloyds"], out); write_natwest(by["natwest"], out); write_barclays(by["barclays"], out)
    write_config(out)
    pd.DataFrame(L.flags).sort_values("date").to_csv(out / "expected_flags.csv", index=False)
    # control totals for integrity tests
    ctl = pd.DataFrame([{"file": k, "rows": len(v), "total_in": round(sum(r["amount"] for r in v if r["amount"] > 0), 2),
                         "total_out": round(-sum(r["amount"] for r in v if r["amount"] < 0), 2),
                         "closing_balance": v[-1]["balance"]} for k, v in by.items()])
    ctl.to_csv(out / "control_totals.csv", index=False)
    print(f"{COMPANY}: {sum(len(v) for v in by.values())} transactions across {len(by)} accounts, {len(L.flags)} expected flags")
    print(ctl.to_string(index=False))

if __name__ == "__main__":
    main()
