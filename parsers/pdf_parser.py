"""Parse PDF bank/card statements — handles Nubank, Itaú, Bradesco, Santander, Inter."""
from __future__ import annotations
import re
from datetime import datetime
from io import BytesIO


# ── Date patterns ────────────────────────────────────────────────────────────
_DATE_PATTERNS = [
    re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"),           # 15/01/2024
    re.compile(r"\b(\d{2}/\d{2}/\d{2})\b"),            # 15/01/24
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),            # 2024-01-15
    re.compile(r"\b(\d{2}-\d{2}-\d{4})\b"),            # 15-01-2024  ← Bradesco
    re.compile(r"\b(\d{2}\s+(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*\.?\s*\d{0,4})\b", re.I),
]

# Lines to skip (totals, headers, payment lines in credit card statements)
_SKIP_PATTERNS = re.compile(
    r"^(total\s+em|total\s+de|total\s+desta|pagamento\s+m[ií]nimo|fatura\s+anterior|"
    r"saldo\s+devedor|parcele\s+esta|se\s+voc[êe]|demonstrativo|transa[çc][õo]es\s+nacionais|"
    r"transa[çc][õo]es\s+internacionais|data\s+descri|valores\s+em|vencimento|fechamento|"
    r"pagamento\s+de\s+fatura|anuidade)",
    re.I,
)

_MONTH_MAP = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

# Amount: handles 1.234,56 / 1234,56 / -150.00 / R$ 1.500,00
_AMOUNT_PAT = re.compile(
    r"[-–]?\s*(?:R\$\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|\d+,\d{2}|\d+\.\d{2})"
)


def parse_pdf(file_bytes: bytes, filename: str = "") -> tuple[list[dict], str]:
    """
    Returns (rows, diagnostic_text).
    diagnostic_text is the raw text extracted — shown in the UI when rows is empty.
    Never raises; always returns a valid tuple.
    """
    try:
        import pdfplumber
    except ImportError:
        return [], "pdfplumber não instalado."

    try:
        all_text_pages = []
        rows = []

        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                # Extract text — try progressively looser tolerances
                text = ""
                for x_tol, y_tol in [(3, 3), (5, 3), (8, 5)]:
                    try:
                        t = page.extract_text(x_tolerance=x_tol, y_tolerance=y_tol) or ""
                        if t.strip():
                            text = t
                            break
                    except Exception:
                        continue
                all_text_pages.append(text)

        full_text = "\n".join(all_text_pages)
        full_text_norm = _normalize_text(full_text)

        is_santander = "santander" in full_text.lower() or "santander" in filename.lower()
        is_extrato_cc = is_santander and bool(re.search(r"per[ií]odos?:|agência:|conta:\s*\d", full_text, re.I))

        if is_extrato_cc:
            # Santander conta corrente — parser específico para formato multi-linha
            rows = _parse_santander_extrato(full_text_norm or full_text, filename)
        else:
            rows = _parse_text(full_text_norm, filename)
            if not rows:
                rows = _parse_text(full_text, filename)
            # Santander fatura de cartão: débitos positivos, créditos negativos — inverter
            if is_santander:
                for r in rows:
                    r["amount"] = -r["amount"]

        # Remove zero-amount or empty-description rows
        rows = [r for r in rows if r["amount"] != 0.0 and r["description"].strip()]

        # Diagnostic: start from "Demonstrativo" if present, else full text
        diag = full_text_norm or full_text
        demo_idx = diag.lower().find("demonstrativo")
        if demo_idx != -1:
            diag = diag[max(0, demo_idx - 100):]
        return rows, diag[:6000]

    except Exception as e:
        return [], f"Erro ao processar PDF: {e}"


def _parse_santander_extrato(text: str, filename: str) -> list[dict]:
    """Parser para extrato conta corrente Santander (dois passos).

    Passo 1: identifica linhas de transação (data + dois valores).
    Passo 2: para cada transação, busca a descrição nas linhas vizinhas.
    """
    rows = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Encontra início da tabela (após cabeçalho "Data Histórico")
    start = 0
    for i, line in enumerate(lines):
        if re.search(r"data\s+hist", line, re.I):
            start = i + 1
            break

    # Encontra fim (antes do rodapé de saldos)
    end = len(lines)
    for i, line in enumerate(lines[start:], start):
        if re.search(r"saldo de conta(max|\s+corrente)|posição em:|saldo disponível\s*$", line, re.I):
            end = i
            break

    lines = lines[start:end]

    date_pat = re.compile(r"^(\d{2}/\d{2}/\d{4})\s*")
    two_nums = re.compile(
        r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})\s*$"
    )
    skip_line = re.compile(
        r"^(saldo|total|vencimento|central|atendimento|sac|ouvidoria|4004|0800|"
        r"juros|iof|limite|desbloqueio|provisão|posição|período)",
        re.I,
    )

    def is_desc(line: str) -> bool:
        return (not date_pat.match(line) and not two_nums.search(line)
                and not skip_line.match(line) and bool(line))

    # Passo 1: coleta todas as linhas de transação
    txn_lines: list[tuple[int, object, float, str]] = []
    for i, line in enumerate(lines):
        date_m = date_pat.match(line)
        amt_m = two_nums.search(line)
        if date_m and amt_m:
            dt = _parse_date_str(date_m.group(1))
            amount = _parse_amount(amt_m.group(1))
            mid = line[date_m.end():amt_m.start()].strip()
            mid = re.sub(r"^\d{5,}\s*", "", mid).strip()
            txn_lines.append((i, dt, amount, mid))

    txn_idx_set = {t[0] for t in txn_lines}
    consumed: set[int] = set()

    # Passo 2: para cada transação, monta a descrição a partir das linhas vizinhas
    for tidx, (line_idx, dt, amount, inline_desc) in enumerate(txn_lines):
        if not dt or amount == 0.0:
            continue

        prev_txn_idx = txn_lines[tidx - 1][0] if tidx > 0 else -1
        next_txn_idx = txn_lines[tidx + 1][0] if tidx + 1 < len(txn_lines) else len(lines)

        # Linhas de continuação APÓS (curtas, apenas quando a linha de transação não tem
        # descrição inline — senão a linha pertence à próxima transação)
        # Processadas primeiro para marcar como consumidas antes do scan "before" da próxima txn
        after = []
        if not inline_desc:
            for j in range(line_idx + 1, min(next_txn_idx, line_idx + 3)):
                if j not in txn_idx_set and is_desc(lines[j]) and len(lines[j]) <= 25:
                    after.append(lines[j])
                    consumed.add(j)

        # Linhas de descrição ANTES (entre a transação anterior e esta, não consumidas)
        before = []
        for j in range(prev_txn_idx + 1, line_idx):
            if j not in txn_idx_set and j not in consumed and is_desc(lines[j]):
                before.append(lines[j])

        parts = before + ([inline_desc] if inline_desc else []) + after
        description = " ".join(p for p in parts if p).strip() or "Sem descrição"

        r = _make_row(dt, description[:200], amount, filename)
        if r:
            rows.append(r)

    return rows


def _parse_table(table: list[list], filename: str) -> list[dict]:
    if not table:
        return []

    rows = []
    # Try to identify which column is date, description, amount
    header = [str(c or "").strip().lower() for c in (table[0] or [])]
    date_col = desc_col = amt_col = credit_col = debit_col = None

    for i, h in enumerate(header):
        if any(k in h for k in ["data", "date", "dt"]):
            date_col = i
        elif any(k in h for k in ["descrição", "descricao", "histórico", "historico", "estabelecimento", "lançamento", "lancamento", "memo", "title", "detail"]):
            desc_col = i
        elif any(k in h for k in ["valor", "amount", "montante", "total"]):
            amt_col = i
        elif any(k in h for k in ["crédito", "credito", "entrada"]):
            credit_col = i
        elif any(k in h for k in ["débito", "debito", "saída", "saida"]):
            debit_col = i

    data_rows = table[1:] if (date_col is not None or desc_col is not None) else table
    if not data_rows:
        data_rows = table

    for row in data_rows:
        if not row:
            continue
        cells = [str(c or "").strip() for c in row]
        if not any(cells):
            continue

        # Find date in any cell
        date_str = None
        if date_col is not None and date_col < len(cells):
            date_str = cells[date_col]
        else:
            for cell in cells:
                dt = _find_date(cell)
                if dt:
                    date_str = cell
                    break

        dt = _find_date(date_str or "") if date_str else None
        if not dt:
            # Last attempt: scan all cells for a date
            for cell in cells:
                dt = _find_date(cell)
                if dt:
                    break
        if not dt:
            continue

        # Find description
        if desc_col is not None and desc_col < len(cells):
            description = cells[desc_col]
        else:
            # Use all non-date, non-amount cells
            description = " ".join(
                c for c in cells
                if c and not _find_date(c) and not _looks_like_amount(c) and len(c) > 2
            )

        # Find amount
        amount = None
        if amt_col is not None and amt_col < len(cells):
            amount = _parse_amount(cells[amt_col])
        elif credit_col is not None and debit_col is not None:
            credit = _parse_amount(cells[credit_col] if credit_col < len(cells) else "0")
            debit  = _parse_amount(cells[debit_col]  if debit_col  < len(cells) else "0")
            amount = (credit or 0) - abs(debit or 0)
        else:
            # Search all cells for an amount
            for cell in reversed(cells):
                if _looks_like_amount(cell):
                    amount = _parse_amount(cell)
                    if amount != 0:
                        break

        if amount is None or amount == 0.0:
            continue

        r = _make_row(dt, description or "Sem descrição", amount, filename)
        if r:
            rows.append(r)

    return rows


def _extract_text_from_words(page) -> str:
    """Reconstruct text from word bounding boxes — works better for multi-column PDFs."""
    try:
        words = page.extract_words(x_tolerance=5, y_tolerance=5, keep_blank_chars=False)
        if not words:
            return ""
        # Group words by approximate y-coordinate (5pt buckets = same line)
        lines: dict[int, list] = {}
        for w in words:
            y_key = round(w["top"] / 5) * 5
            lines.setdefault(y_key, []).append(w)
        result = []
        for y in sorted(lines):
            line_words = sorted(lines[y], key=lambda w: w["x0"])
            result.append(" ".join(w["text"] for w in line_words))
        return "\n".join(result)
    except Exception:
        return ""


def _normalize_text(text: str) -> str:
    """Fix common pdfplumber spacing artifacts in Brazilian bank PDFs."""
    # "28 - 02 - 2026" → "28-02-2026"
    text = re.sub(r"(\d{1,2})\s*-\s*(\d{2})\s*-\s*(\d{2,4})", r"\1-\2-\3", text)
    # "10 / 06 / 2026" → "10/06/2026"
    text = re.sub(r"(\d{1,2})\s*/\s*(\d{2})\s*/\s*(\d{2,4})", r"\1/\2/\3", text)
    # "1 . 3 2 7 , 5 1" → "1.327,51"  (spaced-out amounts)
    text = re.sub(r"(\d)\s\.\s(\d)", r"\1.\2", text)
    text = re.sub(r"(\d)\s,\s(\d)", r"\1,\2", text)
    # Remove excessive internal spaces in single tokens (e.g., "F L" → keep, "2 8" → "28")
    # Only merge isolated single digits
    text = re.sub(r"(?<!\w)(\d)\s+(\d)(?!\w)", r"\1\2", text)
    return text


def _parse_text(text: str, filename: str) -> list[dict]:
    """Line-by-line scan: find every date, then grab description + closest amount."""
    rows = []
    lines = text.splitlines()

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        dt = _find_date(line)
        if not dt:
            continue

        # Skip header/total/payment lines (search on the part after the date)
        if _SKIP_PATTERNS.search(_remove_date(line)):
            continue

        # Remove the date from the line to get the rest
        clean = _remove_date(line)

        # ── Bradesco credit card: "DESCRIPTION CITY\ AMT" or international
        # Split on backslash (location separator used by Bradesco)
        if "\\" in clean:
            desc_part, after_slash = clean.split("\\", 1)
        else:
            desc_part = clean
            after_slash = clean

        # Find all amounts in the line
        all_amounts = _find_all_amounts(after_slash)

        if not all_amounts:
            # Try next line
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            all_amounts = _find_all_amounts(next_line)

        if not all_amounts:
            continue

        # International lines have 4 numbers: [foreign_val, usd, brl, cotação]
        # → pick second-to-last (R$ amount).  National: just take last.
        if len(all_amounts) >= 4:
            amount = all_amounts[-2]   # R$ column
        else:
            amount = all_amounts[-1]   # last value = amount

        if amount == 0.0:
            continue

        # Clean up description: keep merchant name, strip PARC/city/amounts
        description = desc_part.strip()
        description = re.sub(r"\s+PARC\s+\d+/\d+.*$", "", description, flags=re.I)  # remove PARC XX/YY + city
        description = re.sub(r"\s+\d+/\d+\S*$", "", description)  # remove leftover XX/YY
        description = _remove_amount(description)  # remove any stray amount
        description = re.sub(r"\s{2,}", " ", description).strip(" -–|·\\")
        if not description:
            description = "Sem descrição"

        r = _make_row(dt, description, amount, filename)
        if r:
            rows.append(r)

    return rows


# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_date(text: str) -> datetime | None:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return _parse_date_str(m.group(1))
    return None


def _parse_date_str(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    # Try "15 jan 2024" or "15 jan"
    m = re.match(r"(\d{1,2})\s+([a-zç]{3})", raw, re.I)
    if m:
        day = int(m.group(1))
        month = _MONTH_MAP.get(m.group(2).lower()[:3])
        if month:
            year_m = re.search(r"\d{4}", raw)
            year = int(year_m.group()) if year_m else datetime.now().year
            try:
                return datetime(year, month, day)
            except ValueError:
                pass
    return None


def _remove_date(text: str) -> str:
    for pat in _DATE_PATTERNS:
        text = pat.sub("", text)
    return text.strip()


def _looks_like_amount(text: str) -> bool:
    text = text.strip()
    return bool(re.fullmatch(
        r"[-–R$\s]*\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|[-–R$\s]*\d+[,\.]\d{2}", text
    ))


def _find_amount_in_text(text: str) -> float | None:
    amounts = _find_all_amounts(text)
    return amounts[-1] if amounts else None


def _find_all_amounts(text: str) -> list[float]:
    """Return all numeric amounts found in text, preserving sign."""
    # Pattern: optional sign + digits with Brazilian separators
    pat = re.compile(r"([-–])?\s*(\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|\d+[.,]\d{2})")
    results = []
    for m in pat.finditer(text):
        sign = m.group(1) or ""
        val = _parse_amount(sign + m.group(2))
        results.append(val)
    return results


def _remove_amount(text: str) -> str:
    return _AMOUNT_PAT.sub("", text).strip()


def _parse_amount(raw: str) -> float:
    if not raw:
        return 0.0
    raw = raw.strip().replace("R$", "").replace(" ", "").replace("\xa0", "")
    negative = raw.startswith("-") or raw.startswith("–")
    raw = raw.lstrip("-–")

    if "," in raw and "." in raw:
        # 1.234,56 (Brazilian) or 1,234.56 (US)
        if raw.index(",") > raw.index("."):
            raw = raw.replace(".", "").replace(",", ".")  # BR
        else:
            raw = raw.replace(",", "")  # US
    elif "," in raw:
        raw = raw.replace(",", ".")

    try:
        v = float(raw)
        return -v if negative else v
    except ValueError:
        return 0.0


def _make_row(dt: datetime, description: str, amount: float, source: str) -> dict | None:
    if not dt:
        return None
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "description": description.strip()[:200],
        "amount": amount,
        "month": dt.month,
        "year": dt.year,
        "source_file": source,
        "category": "OUTROS",
    }
