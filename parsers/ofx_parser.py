"""Parse OFX/QFX bank statement files."""
from __future__ import annotations
import re
from datetime import datetime
from io import BytesIO


def parse_ofx(file_bytes: bytes, filename: str = "") -> list[dict]:
    """Return list of transaction dicts from an OFX file."""
    try:
        from ofxparse import OfxParser
        ofx = OfxParser.parse(BytesIO(file_bytes))
        rows = []
        for account in ofx.accounts:
            for tx in account.statement.transactions:
                dt = tx.date if hasattr(tx.date, "year") else datetime.now()
                rows.append(_make_row(
                    date=dt.strftime("%Y-%m-%d"),
                    description=str(tx.memo or tx.payee or ""),
                    amount=float(tx.amount),
                    source=filename,
                ))
        return rows
    except Exception:
        # Fallback: parse OFX as plain text with regex
        return _parse_ofx_text(file_bytes, filename)


def _parse_ofx_text(file_bytes: bytes, filename: str) -> list[dict]:
    text = _decode(file_bytes)
    rows = []

    date_pat   = re.compile(r"<DTPOSTED>(\d{8})", re.IGNORECASE)
    amount_pat = re.compile(r"<TRNAMT>([-\d.,]+)", re.IGNORECASE)
    memo_pat   = re.compile(r"<(?:MEMO|NAME)>([^\r\n<]+)", re.IGNORECASE)

    stmttrns = re.split(r"<STMTTRN>", text, flags=re.IGNORECASE)[1:]
    for block in stmttrns:
        d = date_pat.search(block)
        a = amount_pat.search(block)
        m = memo_pat.search(block)
        if d and a:
            raw_date = d.group(1)[:8]
            try:
                dt = datetime.strptime(raw_date, "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                continue
            amount = _parse_amount(a.group(1))
            description = m.group(1).strip() if m else "Sem descrição"
            rows.append(_make_row(dt, description, amount, filename))
    return rows


def _make_row(date: str, description: str, amount: float, source: str) -> dict:
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "description": description.strip(),
        "amount": amount,
        "month": dt.month,
        "year": dt.year,
        "source_file": source,
        "category": "OUTROS",
    }


def _parse_amount(raw: str) -> float:
    raw = raw.replace(" ", "")
    # Brazilian format: 1.234,56 → 1234.56
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")
