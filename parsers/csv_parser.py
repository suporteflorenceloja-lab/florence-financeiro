"""Parse CSV/XLSX bank statements from Brazilian banks.

Auto-detects: encoding, delimiter, column positions, date and amount format.
Handles Itaú, Bradesco, Nubank, Santander, Inter, and generic exports.
"""
from __future__ import annotations
import re
from datetime import datetime
from io import BytesIO, StringIO

import pandas as pd


_DATE_COLS  = ["data", "date", "data lançamento", "data lancamento", "dt."]
_DESC_COLS  = ["descrição", "descricao", "histórico", "historico", "lançamento",
               "lancamento", "memo", "title", "detalhes", "complemento"]
_AMOUNT_COLS = ["valor", "value", "amount", "crédito", "credito",
                "débito", "debito", "montante"]


def parse_csv(file_bytes: bytes, filename: str = "") -> list[dict]:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "xls"):
        return _parse_excel(file_bytes, filename)

    text, encoding = _decode(file_bytes)
    delimiter = _detect_delimiter(text)

    # Skip comment/header lines that don't contain delimiters
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if delimiter in line:
            start = i
            break

    clean_text = "\n".join(lines[start:])

    try:
        df = pd.read_csv(
            StringIO(clean_text),
            sep=delimiter,
            encoding=encoding,
            on_bad_lines="skip",
            dtype=str,
        )
    except Exception:
        return []

    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.dropna(how="all")

    date_col   = _find_col(df, _DATE_COLS)
    desc_col   = _find_col(df, _DESC_COLS)
    amount_col = _find_col(df, _AMOUNT_COLS)

    # Nubank credit card: has separate credit/debit columns
    credit_col = _find_col(df, ["crédito", "credito", "valor crédito"])
    debit_col  = _find_col(df, ["débito", "debito", "valor débito"])

    if not date_col or not (amount_col or (credit_col and debit_col)):
        return []

    rows = []
    for _, row in df.iterrows():
        raw_date = str(row.get(date_col, "")).strip()
        dt = _parse_date(raw_date)
        if not dt:
            continue

        description = str(row.get(desc_col, "Sem descrição")).strip() if desc_col else "Sem descrição"

        if amount_col:
            amount = _parse_amount(str(row.get(amount_col, "0")))
        else:
            credit = _parse_amount(str(row.get(credit_col, "0") or "0"))
            debit  = _parse_amount(str(row.get(debit_col, "0") or "0"))
            amount = credit - abs(debit)

        if amount == 0.0:
            continue

        rows.append({
            "date": dt.strftime("%Y-%m-%d"),
            "description": description,
            "amount": amount,
            "month": dt.month,
            "year": dt.year,
            "source_file": filename,
            "category": "OUTROS",
        })

    return rows


def _parse_excel(file_bytes: bytes, filename: str) -> list[dict]:
    try:
        df = pd.read_excel(BytesIO(file_bytes), dtype=str)
    except Exception:
        return []
    # Write to CSV in memory and reuse the CSV parsing logic
    buf = StringIO()
    df.to_csv(buf, index=False)
    return parse_csv(buf.getvalue().encode("utf-8"), filename.replace(".xlsx", ".csv"))


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
        # partial match
        for col in df.columns:
            if c in col:
                return col
    return None


def _parse_date(raw: str) -> datetime | None:
    raw = raw.strip().split(" ")[0]  # drop time component
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> float:
    raw = re.sub(r"[^\d.,-]", "", raw).strip()
    if not raw:
        return 0.0
    # Brazilian: 1.234,56  →  remove dots, replace comma
    if "," in raw and "." in raw:
        if raw.index(",") > raw.index("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    counts = {d: sample.count(d) for d in (";", ",", "\t")}
    return max(counts, key=counts.get)


def _decode(b: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return b.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace"), "utf-8"
