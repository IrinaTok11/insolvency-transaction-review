from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "normalization"))

from normalize_statements import (  # noqa: E402
    compare_control,
    discover_statement_files,
    load_case_config,
    normalise_statement,
    statement_summary,
)

DATA = ROOT / "synthetic" / "data"


def build_normalized():
    cfg = load_case_config(DATA / "case_config.xlsx")
    parts = [normalise_statement(p, cfg) for p in discover_statement_files(DATA)]
    return pd.concat(parts, ignore_index=True)


def test_integrity_matches_control_totals():
    df = build_normalized()
    integrity = compare_control(statement_summary(df), DATA / "control_totals.csv")
    assert len(df) == 2239
    assert (integrity["status"] == "PASS").all()


def test_source_rows_reflect_physical_rows():
    df = build_normalized()
    mins = df.groupby("Bank")["Source_Row"].min().to_dict()
    assert mins["Lloyds"] == 2      # header row 1
    assert mins["NatWest"] == 6     # four preamble rows + header row 5
    assert mins["Barclays"] == 3    # preamble row 1 + header row 2


def test_own_account_transfers_are_recognised():
    df = build_normalized()
    own = df[df["Is_Own_Account_Transfer"]]
    assert not own.empty
    assert set(own["Counterparty_Norm"]) == {"NORTHBRIDGE TRADING LTD"}
    assert own["Counterparty_Account"].astype(str).str.len().gt(0).all()


def test_unknown_transfer_keeps_target_account_details():
    df = build_normalized()
    row = df[df["Description"].eq("TFR TO 40-11-07 55820019")].iloc[0]
    assert row["Counterparty_Raw"] == "Unknown account"
    assert row["Counterparty_Sort_Code"] == "40-11-07"
    assert str(row["Counterparty_Account"]) == "55820019"
    assert not bool(row["Is_Own_Account_Transfer"])


def test_every_planted_item_is_uniquely_traceable_after_normalisation():
    df = build_normalized().copy()
    df["Date"] = pd.to_datetime(df["Date"])
    expected = pd.read_csv(DATA / "expected_flags.csv")
    expected["date"] = pd.to_datetime(expected["date"])
    for _, e in expected.iterrows():
        m = df[
            (df["Bank"] == e["account"])
            & (df["Date"] == e["date"])
            & ((df["Amount_Signed"] - e["amount"]).abs() < 0.001)
        ]
        assert len(m) == 1, f"{e['flag_id']} expected one traceable transaction, got {len(m)}"


def test_post_appointment_ground_truth_is_deterministic():
    df = build_normalized().copy()
    df["Date"] = pd.to_datetime(df["Date"])
    post = df[df["Date"] > pd.Timestamp("2026-03-14")]
    assert len(post) == 2
    assert set(post["Description"]) == {
        "CARD PAYMENT AMAZON BUSINESS",
        "FPO SMITH CONSULTING LTD",
    }
