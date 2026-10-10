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

# Palavras que iniciam uma NOVA transação — linha não é continuação de outra
_TXN_START_RE = re.compile(
    r"^(pix|pagamento|pagto|transfer[êe]ncia|transf\.?|tarifa|ted\b|doc\b|saque|"
    r"dep[oó]sito|resgate|aplica[çc][aã]o|iof|juros|multa|rendimento|compra|"
    r"d[eé]bito|cr[eé]dito|recebimento|recebid|lançamento|lanc\.|estorno|devolu|"
    r"comiss[aã]o|anuidade|seguro)",
    re.I,
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

            ft_low = full_text.lower()
            fn_low = filename.lower()
            # Identifica o banco pelo CABEÇALHO (primeiros 400 chars) para evitar
            # falso positivo de "nu pagamentos" que aparece como destinatário de Pix
            # em extratos de outros bancos (ex: "Pix Enviado NU PAGAMENTOS S A").
            _hdr = ft_low[:400]
            is_nubank    = ("nubank" in _hdr or "nubank" in fn_low or
                            "nu pagamentos" in _hdr or "nu.com.br" in _hdr)
            is_santander = (not is_nubank) and ("santander" in _hdr or "santander" in fn_low)
            # Extrato CC: detecta pelo cabeçalho "Data Histórico" — único ao extrato de conta
            is_extrato_cc = is_santander and bool(re.search(
                r"data\s+hist[oó]rico", full_text, re.I
            ))
            is_nubank_extrato = is_nubank and bool(re.search(r"movimenta[çc][õo]es", full_text, re.I))

            # Extrato Consolidado Inteligente (Santander PJ): datas DD/MM sem ano,
            # débitos com "-" no final, colunas Créditos | Débitos | Saldo
            is_consolidado = is_santander and not is_extrato_cc and bool(re.search(
                r"extrato\s+consolidado\s+inteligente", full_text, re.I
            ))

            if is_consolidado:
                rows = _parse_santander_consolidado(full_text, filename)
            elif is_nubank_extrato:
                # Nubank conta corrente — sinais já corretos (entradas +, saídas -)
                rows = _parse_nubank_extrato(full_text_norm or full_text, filename)
            elif is_nubank:
                # Nubank fatura — despesas positivas no PDF, inverter para negativo
                rows = _parse_nubank_fatura(full_text_norm or full_text, filename)
                for r in rows:
                    r["amount"] = -r["amount"]
            elif is_extrato_cc:
                _dbg: list[str] = []
                rows = _parse_santander_extrato_tables(pdf, filename)
                _dbg.append(f"tables={len(rows)}")
                if not rows:
                    word_text = "\n".join(_extract_text_from_words(p) for p in pdf.pages)
                    _dbg.append(f"word_chars={len(word_text)}")
                    if word_text.strip():
                        rows = _parse_santander_extrato(_normalize_text(word_text), filename, _dbg)
                        _dbg.append(f"words_rows={len(rows)}")
                if not rows:
                    rows = _parse_santander_extrato(full_text_norm or full_text, filename, _dbg)
                    _dbg.append(f"text_rows={len(rows)}")
            else:
                # Genérico / fatura Santander / banco desconhecido
                rows = _parse_text(full_text_norm, filename)
                if not rows:
                    rows = _parse_text(full_text, filename)
                # Faturas de cartão e extratos genéricos: despesas vêm como positivos
                for r in rows:
                    if r["amount"] > 0:
                        r["amount"] = -r["amount"]

            # ── Monta diagnóstico DENTRO do with (pdf ainda aberto) ───────────
            _n_pages = len(pdf.pages)
            _diag_raw = full_text_norm or full_text
            _demo = _diag_raw.lower().find("demonstrativo")
            if _demo != -1:
                _diag_raw = _diag_raw[max(0, _demo - 100):]
            _flags = (
                f"páginas={_n_pages} chars={len(full_text)} "
                f"santander={is_santander} extrato_cc={is_extrato_cc}"
            )
            if is_extrato_cc:
                _flags += " | " + " ".join(_dbg)
            _diag = f"=== DEBUG ===\n{_flags}\n=== TEXTO (pdfplumber) ===\n{_diag_raw}"

        # Remove zero-amount or empty-description rows
        rows = [r for r in rows if r["amount"] != 0.0 and r["description"].strip()]
        return rows, _diag[:8000]

    except Exception as e:
        exc_name = type(e).__name__
        msg = str(e)
        if "encrypt" in msg.lower() or "password" in msg.lower():
            return [], "PDF protegido por senha. Baixe sem senha e tente novamente."
        return [], f"Erro ao processar PDF ({exc_name}): {msg or 'sem detalhes'}"


def _parse_santander_extrato_tables(pdf, filename: str) -> list[dict]:
    """Extrai transações do extrato Santander via pdfplumber.extract_tables().

    Mais robusto que text-parsing para PDFs com layout multi-coluna (Data | Histórico |
    Documento | Valor | Saldo). O sinal do valor é preservado diretamente do PDF.
    """
    rows = []
    # Formato BR: -10.000,00 ou 10.969,60
    amt_re = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$")

    for page in pdf.pages:
        for table in (page.extract_tables() or []):
            if not table:
                continue
            for row in table:
                if not row:
                    continue
                cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in row]
                if not any(cells):
                    continue

                # Ignora linhas de cabeçalho
                if any(re.search(r"hist[oó]rico|valor\s*\(r\$\)|saldo\s*\(r\$\)", c, re.I)
                       for c in cells):
                    continue

                # Data deve estar na primeira célula
                dt = None
                if cells and re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
                    dt = _parse_date_str(cells[0])
                if not dt:
                    continue

                # Células numéricas (identifica valor e saldo)
                num_cells = [(i, c) for i, c in enumerate(cells) if amt_re.match(c)]
                if not num_cells:
                    continue

                # Valor = penúltimo número (último = saldo); se só um, usa esse
                amt_str = num_cells[-2][1] if len(num_cells) >= 2 else num_cells[-1][1]
                amount = _parse_amount(amt_str)
                if amount == 0.0:
                    continue

                # Índices usados (data e números)
                used = {0} | {i for i, _ in num_cells}
                # Número de documento (só dígitos, 5+ chars) — ignorar
                used |= {i for i, c in enumerate(cells) if re.match(r"^\d{5,}$", c)}

                desc_parts = [cells[i] for i in range(len(cells)) if i not in used and cells[i]]
                description = " ".join(desc_parts).strip() or "Sem descrição"

                r = _make_row(dt, description[:200], amount, filename)
                if r:
                    rows.append(r)

    return rows


_MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


def _parse_santander_consolidado(text: str, filename: str) -> list[dict]:
    """Parser para o "Extrato Consolidado Inteligente" do Santander PJ.

    Seção "Conta Corrente > Movimentação", uma transação por linha:
        02/09 TED RECEBIDA 47917930000178 - 19.196,33
        PIX ENVIADO - 20.000,00-
        FL INDUSTRIA E COMERCIO D            ← continuação (favorecido)
        RESGATE CONTAMAX AUTOMATICO - 943,67 0,00
    Data só aparece na 1ª transação do dia (e às vezes sozinha antes da quebra
    de página). Débito = valor com "-" no final. Ano vem de "<mês>/AAAA".
    """
    rows = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    ref_year = None
    ref_month = None
    m = re.search(r"\b(" + "|".join(_MESES_PT) + r")/(\d{4})\b", text, re.I)
    if m:
        ref_month = _MESES_PT[m.group(1).lower()]
        ref_year = int(m.group(2))
    if not ref_year:
        ref_year = datetime.now().year

    # Delimita a seção de movimentação
    start = end = None
    for i, line in enumerate(lines):
        if start is None and re.match(r"^movimenta[çc][ãa]o$", line, re.I):
            start = i + 1
        elif start is not None and re.match(r"^saldos\s+por\s+per[ií]odo", line, re.I):
            end = i
            break
    if start is None:
        return rows
    lines = lines[start:end]

    amt = r"\d{1,3}(?:\.\d{3})*,\d{2}"
    txn_re = re.compile(
        rf"^(?:(\d{{2}}/\d{{2}})\s+)?(.+?)\s+(-|\d+)\s+({amt})(-?)(?:\s+-?{amt}-?)?$"
    )
    date_only_re = re.compile(r"^(\d{2}/\d{2})$")
    skip_re = re.compile(
        r"^(data\s+descri|cr[ée]ditos\s+d[ée]bitos|saldo\s+em\s|extrato_pj|balp_|"
        r"p[aá]gina\s*:|extrato\s+consolidado|(" + "|".join(_MESES_PT) + r")/\d{4}$|[“\"])",
        re.I,
    )

    cur_date = None
    last = None
    for line in lines:
        if skip_re.match(line):
            last = None
            continue
        dm = date_only_re.match(line)
        if dm:
            cur_date = dm.group(1)
            last = None
            continue
        tm = txn_re.match(line)
        if tm:
            if tm.group(1):
                cur_date = tm.group(1)
            if not cur_date:
                continue
            day, month = (int(x) for x in cur_date.split("/"))
            year = ref_year
            # Lançamento de dezembro em extrato de janeiro (ou vice-versa)
            if ref_month == 1 and month == 12:
                year -= 1
            try:
                dt = datetime(year, month, day)
            except ValueError:
                continue
            amount = _parse_amount(tm.group(4))
            if tm.group(5) == "-":
                amount = -amount
            last = _make_row(dt, tm.group(2).strip(), amount, filename)
            if last and amount != 0.0:
                rows.append(last)
            continue
        # Linha de continuação: favorecido/empresa da transação anterior
        if last is not None and not _find_all_amounts(line):
            last["description"] = f"{last['description']} {line}"[:200]
            last = None

    return rows


def _parse_santander_extrato(text: str, filename: str, _dbg: list | None = None) -> list[dict]:
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
        r"juros|iof|limite|desbloqueio|provisão|posição|período"
        r"|\d{1,2}/\d{1,2}$)",  # "1/5", "2/5" etc. — números de página
        re.I,
    )

    def is_desc(line: str) -> bool:
        return (not date_pat.match(line) and not two_nums.search(line)
                and not skip_line.match(line) and bool(line))

    # Passo 1: coleta todas as linhas de transação (com saldo para inferir débito/crédito)
    txn_lines: list[tuple[int, object, float, float, str]] = []
    for i, line in enumerate(lines):
        date_m = date_pat.match(line)
        amt_m = two_nums.search(line)
        if date_m and amt_m:
            dt = _parse_date_str(date_m.group(1))
            raw_amount  = _parse_amount(amt_m.group(1))  # valor da transação (sempre positivo no PDF)
            raw_balance = _parse_amount(amt_m.group(2))  # saldo após a transação
            mid = line[date_m.end():amt_m.start()].strip()
            mid = re.sub(r"^\d{5,}\s*", "", mid).strip()   # número no início (doc simples)
            # Nº de documento no fim (ex: "Pix Recebido 15615783710 13273866");
            # CPF (11) / CNPJ (14) ficam na descrição para identificar a origem
            mid = re.sub(r"(?<=\d)\s+\d{5,10}$", "", mid).strip()
            txn_lines.append((i, dt, raw_amount, raw_balance, mid))

    if _dbg is not None:
        _dbg.append(
            f"total_linhas={len(lines)} start_original={start} txn_encontradas={len(txn_lines)}"
        )
        # Mostra primeiras linhas do texto e primeira linha de transação para diagnóstico
        if lines:
            _dbg.append(f"linha[0]={repr(lines[0][:80])}")
        if txn_lines:
            _dbg.append(f"1ª_txn=date={txn_lines[0][1]} amt={txn_lines[0][2]}")
        else:
            # Nenhuma transação — mostra por que as primeiras linhas com data falham
            for i, line in enumerate(lines[:10]):
                dm = date_pat.match(line)
                am = two_nums.search(line) if dm else None
                _dbg.append(f"  [{i}] date={'ok' if dm else 'no'} two_nums={'ok' if am else 'no'} | {repr(line[:60])}")

    # Extrato do app Santander Empresas já traz o sinal (saídas com "-", entradas
    # sem sinal). Se houver qualquer valor negativo, confia no PDF — a inferência
    # por saldo erra porque a lista vem do mais recente para o mais antigo.
    signed_pdf = any(t[2] < 0 for t in txn_lines)

    txn_idx_set = {t[0] for t in txn_lines}
    consumed: set[int] = set()

    # Passo 2: para cada transação, monta a descrição a partir das linhas vizinhas
    for tidx, (line_idx, dt, raw_amount, raw_balance, inline_desc) in enumerate(txn_lines):
        # Determina sinal via comparação de saldos consecutivos:
        # saldo diminuiu → débito (despesa, negativo); saldo aumentou → crédito (receita, positivo)
        if signed_pdf or raw_amount < 0:
            # PDF já trouxe sinal explícito — respeita
            amount = raw_amount
        elif tidx > 0:
            prev_balance = txn_lines[tidx - 1][3]
            amount = -abs(raw_amount) if raw_balance < prev_balance else abs(raw_amount)
        else:
            # Primeira transação sem saldo anterior: usa palavras-chave para determinar
            _income_kw = (
                "RECEBID", "CREDITAD", "DEPÓSITO", "DEPOSITO", "RESTITUI",
                "RESGATE", "TED RECEBID", "PIX RECEBID", "RENDIMENTO",
            )
            desc_upper = inline_desc.upper()
            amount = abs(raw_amount) if any(k in desc_upper for k in _income_kw) else -abs(raw_amount)

        if not dt or amount == 0.0:
            continue

        prev_txn_idx = txn_lines[tidx - 1][0] if tidx > 0 else -1
        next_txn_idx = txn_lines[tidx + 1][0] if tidx + 1 < len(txn_lines) else len(lines)

        # Linhas de continuação APÓS — até 2 linhas imediatas, curtas, que não começam
        # como nova transação (_TXN_START_RE) e não são números de página (skip_line).
        # O guard "if not inline_desc" foi removido: mesmo com desc inline, a linha
        # seguinte pode ser uma continuação (nome de empresa truncado). O _TXN_START_RE
        # garante que descrições de novas transações não sejam capturadas aqui.
        after = []
        for j in range(line_idx + 1, min(next_txn_idx, line_idx + 3)):
            line_j = lines[j]
            if (j not in txn_idx_set and is_desc(line_j)
                    and len(line_j) <= 30
                    and not _TXN_START_RE.match(line_j)):
                after.append(line_j)
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


def _parse_nubank_fatura(text: str, filename: str) -> list[dict]:
    """Parser para fatura Nubank.

    Formato de cada transação:
        26 ABR •••• 1303 Lycrimalhas - Parcela 4/5 R$ 239,88

    Só lê linhas a partir da seção 'TRANSAÇÕES DE', ignorando o cabeçalho.
    """
    rows = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Extrai o ano da fatura a partir do nome do arquivo ou do texto
    year_m = re.search(r"\b(20\d{2})\b", filename)
    if not year_m:
        for line in lines[:40]:
            year_m = re.search(r"\b(20\d{2})\b", line)
            if year_m:
                break
    bill_year = int(year_m.group(1)) if year_m else datetime.now().year

    # Encontra o início das transações
    start = 0
    for i, line in enumerate(lines):
        if re.search(r"transa[çc][õo]es\s+de\b", line, re.I):
            start = i + 1
            break

    # Padrão: "26 ABR •••• 1303 Descrição R$ 239,88"
    txn_pat = re.compile(
        r"^(\d{2}\s+[A-Za-z]{3})\s+\S+\s+\d{4}\s+(.+?)\s+R\$\s*([\d.]+,\d{2})\s*$"
    )

    for line in lines[start:]:
        m = txn_pat.match(line)
        if not m:
            continue
        dt = _parse_date_str(m.group(1) + " " + str(bill_year))
        description = m.group(2).strip()
        amount = _parse_amount(m.group(3))
        if not dt or amount == 0.0:
            continue
        r = _make_row(dt, description[:200], amount, filename)
        if r:
            rows.append(r)

    return rows


def _parse_nubank_extrato(text: str, filename: str) -> list[dict]:
    """Parser para extrato de conta corrente Nubank.

    Cada dia abre com:
        15 MAI 2026 Total de saídas - 4,90
    Seguido por transações no formato:
        Transferência enviada pelo Pix EBANX... 4,90
    O sinal (entrada/saída) vem do cabeçalho do bloco do dia.
    """
    rows = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Começa após "Movimentações"
    start = 0
    for i, line in enumerate(lines):
        if re.search(r"^movimenta[çc][õo]es$", line, re.I):
            start = i + 1
            break

    # Cabeçalho de dia com data: "15 MAI 2026 Total de saídas - 4,90"
    day_pat = re.compile(r"^(\d{2}\s+[A-Za-z]+\s+\d{4})\s+Total de (entradas|sa[íi]das)", re.I)
    # Mudança de direção dentro do mesmo dia: "Total de saídas - 40.020,00"
    dir_pat = re.compile(r"^Total de (entradas|sa[íi]das)", re.I)
    # Linhas a ignorar
    skip_pat = re.compile(
        r"^(Saldo do dia|Saldo inicial|Saldo final|Rendimento|Tem alguma|Caso a solu|"
        r"Extrato gerado|Asseguramos|N[ãa]o nos|O saldo l[íi]quido|"
        r"Nu Paga|Nu Finan|Pr[óo]xima|Atendimento)",
        re.I,
    )
    # Valor BR no final da linha (ex: 4,90 ou 40.000,00)
    amt_end = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*$")

    current_date = None
    current_sign = None  # +1 entradas, -1 saídas

    for line in lines[start:]:
        # Cabeçalho de dia com data e direção
        m = day_pat.match(line)
        if m:
            current_date = _parse_date_str(m.group(1))
            current_sign = 1 if "entrada" in m.group(2).lower() else -1
            continue

        # Mudança de direção sem data (segundo bloco do mesmo dia)
        m = dir_pat.match(line)
        if m:
            current_sign = 1 if "entrada" in m.group(1).lower() else -1
            continue

        if skip_pat.match(line) or current_date is None or current_sign is None:
            continue

        # Transação: linha termina com valor BR
        amt_m = amt_end.search(line)
        if not amt_m:
            continue

        amount = _parse_amount(amt_m.group(1)) * current_sign
        if amount == 0.0:
            continue

        description = line[:amt_m.start()].strip()
        # Remove traço/espaço residual no fim: "...LTDA. - "
        description = re.sub(r"[\s\-]+$", "", description).strip()
        # Remove CNPJ inline: "... - 13.236.697/0001-46 - EBANX..."
        description = re.sub(r"\s*-\s*\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}.*$", "", description).strip()

        if not description:
            continue

        r = _make_row(current_date, description[:200], amount, filename)
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
