"""Florence Intimates — Sistema Financeiro"""
import os
import sys
from pathlib import Path

# Make sure imports resolve from project root
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import streamlit as st

import database as db
from categorizer import categorize
from config import CATEGORIES, MONTHS_PT, SKIP_DESCRIPTIONS
from dre import calculate_dre, export_excel
from parsers import parse_csv, parse_ofx, parse_pdf

# ── Initialise ──────────────────────────────────────────────────────────────
db.init_db()


def _extract_keyword(description: str) -> str:
    """Extract a stable keyword from a transaction description to use as a rule."""
    import re
    desc = description.upper().strip()
    # Remove trailing city names and noise
    desc = re.sub(r"\s+(SAO PAULO|JOINVILLE|OSASCO|CURITIBA|RIO DE JANEIRO|FAINA|NOVA FRIBURGO|SAN FRANCISCO)\s*$", "", desc)
    desc = re.sub(r"\s+PARC\s+\d+/\d+.*$", "", desc)
    # Take first meaningful token (up to first * or space-separated word)
    # Use up to 3 words to keep it specific enough
    words = desc.split()
    keyword = " ".join(words[:3]) if words else ""
    # Minimum 4 chars to avoid too-generic rules
    return keyword if len(keyword) >= 4 else ""

st.set_page_config(
    page_title="Florence — Financeiro",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Tab strip */
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid #F0D6E4; }
.stTabs [data-baseweb="tab"] { padding: 8px 22px; border-radius: 6px 6px 0 0; }
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: #8B1A4A; color: white !important; font-weight: 700;
}
/* DRE row dividers */
.dre-subtotal { border-top: 1px solid #F0D6E4; padding: 5px 0; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# Header
st.markdown("""
<div style="padding: 18px 0 10px 0; border-bottom: 2px solid #8B1A4A; margin-bottom: 20px;">
  <span style="font-size:26px; font-weight:700; color:#8B1A4A; letter-spacing:-0.5px;">
    Florence Intimates
  </span>
  <span style="font-size:18px; color:#9CA3AF; margin-left:10px;">· Financeiro</span>
</div>
""", unsafe_allow_html=True)

tab_upload, tab_lancamentos, tab_dre, tab_regras = st.tabs(
    ["📤 Upload", "📋 Lançamentos", "📊 DRE", "⚙️ Regras"]
)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — UPLOAD
# ═══════════════════════════════════════════════════════════════════════════
with tab_upload:
    st.subheader("Importar extratos e faturas")

    # ── Lançamento manual ────────────────────────────────────────────────────
    with st.expander("✏️ Adicionar lançamento manual"):
        with st.form("manual_entry", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns([1, 2, 1, 2])
            man_date = c1.date_input("Data")
            man_desc = c2.text_input("Descrição", placeholder="ex: Aluguel maio")
            man_amt  = c3.number_input("Valor (R$)", step=0.01, format="%.2f",
                                       help="Use valor negativo para despesa, positivo para receita")
            man_cat  = c4.selectbox("Categoria", CATEGORIES)
            if st.form_submit_button("Adicionar", type="primary"):
                if man_desc.strip():
                    from datetime import datetime
                    dt = man_date
                    row = {
                        "date": dt.strftime("%Y-%m-%d"),
                        "description": man_desc.strip(),
                        "amount": man_amt,
                        "category": man_cat,
                        "source_file": "manual",
                        "month": dt.month,
                        "year": dt.year,
                    }
                    inserted, skipped = db.insert_transactions([row])
                    if inserted:
                        st.success(f"Lançamento adicionado: **{man_desc}** — R$ {man_amt:.2f}")
                    else:
                        st.warning("Lançamento duplicado, ignorado.")
                else:
                    st.warning("Informe uma descrição.")

    st.divider()

    # ── Importação histórica ─────────────────────────────────────────────────
    with st.expander("📊 Importar histórico (planilha DRE por mês)"):
        st.caption(
            "Planilha com coluna **Mês** (ex: 01/2023) e colunas de categoria. "
            "Valores positivos — despesas são negadas automaticamente."
        )

        _HIST_COL_MAP = {
            "RECEITA BRUTA":        ("RECEITA BRUTA",          1),
            "IMPOSTO":              ("SIMPLES NACIONAL",       -1),
            "MATERIAL":             ("MATERIAL",               -1),
            "AVIAMENTO":            ("AVIAMENTO",              -1),
            "ETIQUETA":             ("ETIQUETA",               -1),
            "ALÇA":                 ("ALÇA",                   -1),
            "FACÇÃO":               ("FACÇÃO",                 -1),
            "TALHAÇÃO":             ("TALHAÇÃO",               -1),
            "EMBALAGEM":            ("EMBALAGEM",              -1),
            "FRETE PRODUÇÃO":       ("FRETE PRODUÇÃO",         -1),
            "FRETE PEDIDO":         ("FRETE PEDIDO",           -1),
            "CONTABILIDADE":        ("CONTABILIDADE",          -1),
            "ECOMMERCE":            ("ECOMMERCE",              -1),
            "MATERIAIS EXPEDIENTE": ("MATERIAIS EXPEDIENTE",   -1),
            "TRÁFEGO":              ("TRÁFEGO",                -1),
            "SERVIÇOS MKT/EVENTOS": ("SERVIÇOS MKT/EVENTOS",  -1),
            "ENERGIA":              ("ENERGIA",                -1),
            "CONDOMINIO":           ("CONDOMÍNIO",             -1),
            "ALUGUEL":              ("ALUGUEL",                -1),
            "TRANSPORTE":           ("TRANSPORTE",             -1),
            "INTERNET":             ("INTERNET",               -1),
            "SALÁRIO":              ("SALÁRIO",                -1),
            "OUTROS":               ("OUTROS",                 -1),
        }

        def _parse_mes(val):
            import re as _re
            from datetime import datetime as _dt
            _months = {"jan":1,"fev":2,"mar":3,"abr":4,"mai":5,"jun":6,
                       "jul":7,"ago":8,"set":9,"out":10,"nov":11,"dez":12}
            if isinstance(val, (_dt, pd.Timestamp)):
                return int(val.year), int(val.month)
            s = str(val).strip()
            m = _re.match(r"^(\d{1,2})[/\-](\d{4})$", s)
            if m: return int(m.group(2)), int(m.group(1))
            m = _re.match(r"^(\d{4})[/\-](\d{1,2})$", s)
            if m: return int(m.group(1)), int(m.group(2))
            m = _re.match(r"^([a-zçã]+)[./\-\s]?(\d{4})$", s, _re.I)
            if m:
                mn = _months.get(m.group(1).lower()[:3])
                if mn: return int(m.group(2)), mn
            try:
                d = pd.to_datetime(val)
                return int(d.year), int(d.month)
            except Exception:
                return None, None

        hist_file = st.file_uploader(
            "Selecione a planilha histórica",
            type=["xlsx", "xls", "csv"],
            key="hist_uploader",
        )

        if hist_file:
            try:
                ext = hist_file.name.lower().rsplit(".", 1)[-1]
                if ext == "csv":
                    df_hist = pd.read_csv(hist_file)
                else:
                    df_hist = pd.read_excel(hist_file)

                # Normaliza nomes de colunas
                df_hist.columns = [str(c).strip().upper() for c in df_hist.columns]

                # Encontra coluna de mês
                mes_col = next((c for c in df_hist.columns if "MÊS" in c or "MES" in c), None)
                if not mes_col:
                    st.error("Coluna 'Mês' não encontrada na planilha.")
                else:
                    hist_rows = []
                    erros = []
                    for _, row in df_hist.iterrows():
                        year, month = _parse_mes(row[mes_col])
                        if not year:
                            erros.append(f"Mês inválido: {row[mes_col]}")
                            continue
                        date_str = f"{year}-{month:02d}-01"
                        for col, (cat, sign) in _HIST_COL_MAP.items():
                            # Aceita variações com/sem acento
                            match = next((c for c in df_hist.columns if c == col
                                          or c.replace("Ã","A").replace("Á","A")
                                             .replace("É","E").replace("Ç","C")
                                             .replace("Ó","O").replace("Ú","U")
                                          == col.replace("Ã","A").replace("Á","A")
                                             .replace("É","E").replace("Ç","C")
                                             .replace("Ó","O").replace("Ú","U")), None)
                            if not match or pd.isna(row.get(match)):
                                continue
                            try:
                                val = float(str(row[match]).replace(",", ".").replace(" ", ""))
                            except Exception:
                                continue
                            if val == 0:
                                continue
                            amount = round(val * sign, 2)
                            hist_rows.append({
                                "date": date_str,
                                "description": f"Histórico {cat}",
                                "amount": amount,
                                "category": cat,
                                "source_file": hist_file.name,
                                "month": month,
                                "year": year,
                            })

                    if erros:
                        for e in erros:
                            st.warning(e)

                    if not hist_rows:
                        st.error("Nenhum lançamento encontrado na planilha.")
                    else:
                        meses = sorted({(r["year"], r["month"]) for r in hist_rows})
                        st.success(
                            f"**{len(hist_rows)} lançamentos** em **{len(meses)} meses** "
                            f"({meses[0][1]:02d}/{meses[0][0]} a {meses[-1][1]:02d}/{meses[-1][0]}). "
                            "Clique em Importar para gravar."
                        )
                        if st.button("✅ Importar histórico", type="primary", key="btn_hist"):
                            inserted, skipped = db.insert_transactions(hist_rows)
                            st.success(f"Importados: **{inserted}** | Duplicados ignorados: **{skipped}**")

            except Exception as e:
                st.error(f"Erro ao ler planilha: {e}")

    st.divider()

    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0
    if "import_msg" in st.session_state and st.session_state.import_msg:
        st.success(st.session_state.import_msg)
        st.session_state.import_msg = None

    uploaded = st.file_uploader(
        "Arraste os arquivos aqui",
        accept_multiple_files=True,
        type=["ofx", "qfx", "csv", "xlsx", "xls", "pdf"],
        help="Aceita OFX (Itaú, Bradesco, Nubank…), CSV, Excel e PDF",
        key=f"uploader_{st.session_state.uploader_key}",
    )

    if uploaded:
        all_rows: list[dict] = []
        errors: list[str] = []
        pdf_diagnostics: list[tuple[str, str]] = []  # (filename, raw_text)

        for f in uploaded:
            file_bytes = f.read()
            ext = f.name.lower().rsplit(".", 1)[-1]
            try:
                if ext in ("ofx", "qfx"):
                    rows = parse_ofx(file_bytes, f.name)
                elif ext in ("csv", "xlsx", "xls"):
                    rows = parse_csv(file_bytes, f.name)
                elif ext == "pdf":
                    rows, diag = parse_pdf(file_bytes, f.name)
                    pdf_diagnostics.append((f.name, diag))
                else:
                    rows = []
                all_rows.extend(rows)
            except Exception as e:
                errors.append(f"{f.name}: {e}")

        if errors:
            for err in errors:
                st.warning(f"⚠️ {err}")

        # Diagnóstico sempre visível para PDFs (ajuda a depurar layout)
        for fname, diag in pdf_diagnostics:
            with st.expander(f"🔍 Texto extraído de {fname}"):
                st.code(diag or "(PDF sem texto)", language="text")

        if not all_rows:
            st.error("Nenhum lançamento encontrado nos arquivos enviados.")
        else:
            # Remove lançamentos irrelevantes
            def _should_skip(desc: str) -> bool:
                desc_upper = desc.upper()
                return any(kw.upper() in desc_upper for kw in SKIP_DESCRIPTIONS)
            skipped_auto = sum(1 for r in all_rows if _should_skip(r["description"]))
            all_rows = [r for r in all_rows if not _should_skip(r["description"])]
            if skipped_auto:
                st.caption(f"{skipped_auto} lançamento(s) ignorados automaticamente.")

            # Apply rules + AI categorization
            rules = db.get_rules()
            all_rows = categorize(all_rows, rules)

            st.success(f"**{len(all_rows)} lançamentos** encontrados. Revise as categorias abaixo e clique em Importar.")

            # Editable preview
            preview_df = pd.DataFrame(all_rows)[
                ["date", "description", "amount", "category", "source_file"]
            ].rename(columns={
                "date": "Data", "description": "Descrição",
                "amount": "Valor (R$)", "category": "Categoria",
                "source_file": "Arquivo",
            })

            edited = st.data_editor(
                preview_df,
                column_config={
                    "Categoria": st.column_config.SelectboxColumn(
                        "Categoria", options=CATEGORIES, required=True
                    ),
                    "Valor (R$)": st.column_config.NumberColumn(
                        "Valor (R$)", format="R$ %.2f"
                    ),
                    "Data": st.column_config.TextColumn("Data"),
                },
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                key="preview_editor",
            )

            if st.button("✅ Importar lançamentos", type="primary"):
                rules_saved = 0
                for i, row in enumerate(all_rows):
                    new_cat = edited.iloc[i]["Categoria"]
                    row["category"] = new_cat
                    # Auto-save rule: first word(s) of description as keyword
                    if new_cat not in ("OUTROS", "RECEITA BRUTA"):
                        keyword = _extract_keyword(row["description"])
                        if keyword:
                            existing = [r["keyword"] for r in db.get_rules()]
                            if keyword not in existing:
                                db.add_rule(keyword, new_cat)
                                rules_saved += 1

                inserted, skipped = db.insert_transactions(all_rows)
                msg = f"Importados: **{inserted}** | Duplicados ignorados: **{skipped}**"
                if rules_saved:
                    msg += f" | **{rules_saved} regras** criadas automaticamente"
                st.session_state.import_msg = msg
                st.session_state.uploader_key += 1
                st.balloons()
                st.rerun()

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — LANÇAMENTOS
# ═══════════════════════════════════════════════════════════════════════════
with tab_lancamentos:
    st.subheader("Lançamentos")

    years = db.get_available_years() or [pd.Timestamp.now().year]
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        sel_year = st.selectbox("Ano", years, key="lc_year")
    with col2:
        month_opts = {0: "Todos"} | MONTHS_PT
        sel_month_label = st.selectbox(
            "Mês", list(month_opts.values()), key="lc_month"
        )
        sel_month = [k for k, v in month_opts.items() if v == sel_month_label][0]
    with col3:
        sel_cat = st.selectbox(
            "Categoria", ["Todas"] + CATEGORIES, key="lc_cat"
        )

    txs = db.get_transactions(
        month=sel_month or None,
        year=sel_year,
        category=sel_cat if sel_cat != "Todas" else None,
    )

    if not txs:
        st.info("Nenhum lançamento encontrado para os filtros selecionados.")
    else:
        df = pd.DataFrame(txs)
        display_cols = ["id", "date", "description", "amount", "category", "source_file"]
        df = df[display_cols].rename(columns={
            "id": "ID", "date": "Data", "description": "Descrição",
            "amount": "Valor (R$)", "category": "Categoria", "source_file": "Arquivo",
        })

        edited_lc = st.data_editor(
            df,
            column_config={
                "Categoria": st.column_config.SelectboxColumn(
                    "Categoria", options=CATEGORIES, required=True
                ),
                "Valor (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                "ID": st.column_config.NumberColumn(disabled=True),
                "Data": st.column_config.TextColumn(disabled=True),
                "Descrição": st.column_config.TextColumn(disabled=True),
                "Arquivo": st.column_config.TextColumn(disabled=True),
            },
            hide_index=True,
            use_container_width=True,
            key="lc_editor",
        )

        col_save, col_del, col_info = st.columns([1, 1, 4])

        with col_save:
            if st.button("💾 Salvar categorias", type="primary"):
                changed = 0
                for i, orig in enumerate(txs):
                    new_cat = edited_lc.iloc[i]["Categoria"]
                    if new_cat != orig["category"]:
                        db.update_category(orig["id"], new_cat)
                        changed += 1
                if changed:
                    st.success(f"{changed} lançamento(s) atualizados.")
                else:
                    st.info("Nenhuma alteração detectada.")
                st.rerun()

        with col_del:
            del_ids = st.multiselect(
                "Excluir IDs", [r["id"] for r in txs], label_visibility="collapsed",
                placeholder="Selecionar IDs para excluir"
            )
            if del_ids and st.button("🗑️ Excluir selecionados", type="secondary"):
                for tid in del_ids:
                    db.delete_transaction(tid)
                st.rerun()

        with col_info:
            total = df["Valor (R$)"].sum()
            st.caption(f"**{len(txs)}** lançamentos · Total: **R$ {total:,.2f}**")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — DRE
# ═══════════════════════════════════════════════════════════════════════════
with tab_dre:
    st.subheader("Demonstração do Resultado — DRE")

    years = db.get_available_years() or [pd.Timestamp.now().year]
    year_opts = ["Todos os anos"] + [str(y) for y in years]
    col1, col2 = st.columns(2)
    with col1:
        dre_year_label = st.selectbox("Ano", year_opts, key="dre_year")
        dre_year = None if dre_year_label == "Todos os anos" else int(dre_year_label)
    with col2:
        if dre_year is None:
            st.selectbox("Mês", ["Consolidado geral"], key="dre_month", disabled=True)
            dre_month = None
        else:
            month_opts_dre = {0: "Acumulado no ano"} | MONTHS_PT
            dre_month_label = st.selectbox(
                "Mês", list(month_opts_dre.values()), key="dre_month"
            )
            dre_month = [k for k, v in month_opts_dre.items() if v == dre_month_label][0]

    txs = db.get_transactions(
        month=dre_month or None,
        year=dre_year,
    )

    dre_rows = calculate_dre(txs)

    # KPI cards
    vals = {r["label"]: r["amount"] for r in dre_rows if r["amount"] is not None}
    receita    = next((r["amount"] for r in dre_rows if r["label"] == "RECEITA BRUTA"), 0) or 0
    lucro_bruto = next((r["amount"] for r in dre_rows if "LUCRO BRUTO" in r["label"]), 0) or 0
    resultado  = next((r["amount"] for r in dre_rows if "RESULTADO" in r["label"]), 0) or 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Receita Bruta", f"R$ {receita:,.2f}")
    k2.metric("Lucro Bruto",   f"R$ {lucro_bruto:,.2f}",
              delta=f"{lucro_bruto/receita*100:.1f}% margem" if receita else None)
    k3.metric("Resultado",     f"R$ {resultado:,.2f}",
              delta=f"{resultado/receita*100:.1f}% margem" if receita else None)
    if receita:
        k4.metric("Margem Líquida", f"{resultado/receita*100:.1f}%")
    else:
        k4.metric("Margem Líquida", "—")

    st.divider()

    # DRE table
    av_base = receita if receita else None  # base para análise vertical

    def _av(amt):
        """Retorna string de análise vertical (% sobre Receita Bruta)."""
        if av_base and amt is not None:
            return f"{amt / av_base * 100:.1f}%"
        return "—"

    for entry in dre_rows:
        t     = entry["type"]
        label = entry["label"]
        amt   = entry["amount"]
        level = entry["level"]

        if t == "section":
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;'
                f'font-weight:700;padding:6px 0 2px 0;border-top:2px solid #F0D6E4;">'
                f'<span>{label}</span>'
                f'<span style="color:#9CA3AF;font-size:0.8em;font-weight:400">AV%&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;R$</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            continue

        amt_str = f"{amt:,.2f}" if amt is not None else "—"
        av_str  = _av(amt)

        if t in ("subtotal", "result"):
            color = "#166534" if (amt or 0) >= 0 else "#991b1b"
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
                f'font-weight:700;border-top:1px solid #e9d5ff;padding:4px 0;">'
                f'<span>{label}</span>'
                f'<span>'
                f'<span style="color:#9CA3AF;font-size:0.85em;margin-right:16px">{av_str}</span>'
                f'<span style="color:{color}">R$ {amt_str}</span>'
                f'</span></div>',
                unsafe_allow_html=True,
            )
        else:
            is_negative_display = t == "cost" and amt and amt > 0
            display_amt = f"({amt_str})" if is_negative_display else amt_str
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
                f'padding:2px 0;color:#374151;">'
                f'<span style="padding-left:{level*16}px">{label}</span>'
                f'<span>'
                f'<span style="color:#9CA3AF;font-size:0.85em;margin-right:16px">{av_str}</span>'
                f'<span>R$ {display_amt}</span>'
                f'</span></div>',
                unsafe_allow_html=True,
            )

    st.divider()

    # Export button
    if txs:
        excel_bytes = export_excel(dre_rows, dre_month or 0, dre_year or 0)
        if dre_year is None:
            file_label = "Consolidado"
        elif dre_month:
            file_label = f"{MONTHS_PT[dre_month]}_{dre_year}"
        else:
            file_label = str(dre_year)
        st.download_button(
            label="📥 Baixar DRE em Excel",
            data=excel_bytes,
            file_name=f"DRE_Florence_{file_label}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("Nenhum lançamento encontrado para o período selecionado.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 4 — REGRAS
# ═══════════════════════════════════════════════════════════════════════════
with tab_regras:
    st.subheader("Regras de categorização automática")
    st.caption(
        "Quando uma descrição de lançamento contém a palavra-chave, "
        "a categoria é aplicada automaticamente na importação."
    )

    rules = db.get_rules()

    # Add new rule
    with st.form("add_rule_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 2, 1])
        new_kw  = c1.text_input("Palavra-chave (ex: CORREIOS, MERCADO LIVRE)")
        new_cat = c2.selectbox("Categoria", CATEGORIES)
        new_pri = c3.number_input("Prioridade", min_value=0, max_value=100, value=0)
        if st.form_submit_button("➕ Adicionar regra", type="primary"):
            if new_kw.strip():
                db.add_rule(new_kw.strip(), new_cat, int(new_pri))
                st.success(f"Regra adicionada: **{new_kw.upper()}** → {new_cat}")
                st.rerun()
            else:
                st.warning("Informe uma palavra-chave.")

    st.divider()

    if rules:
        rules_df = pd.DataFrame(rules)[["id", "keyword", "category", "priority"]]
        rules_df.columns = ["ID", "Palavra-chave", "Categoria", "Prioridade"]
        st.dataframe(rules_df, hide_index=True, use_container_width=True)

        del_rule_ids = st.multiselect(
            "Excluir regras (selecione os IDs)",
            [r["id"] for r in rules],
            format_func=lambda i: next(
                f"{r['keyword']} → {r['category']}" for r in rules if r["id"] == i
            ),
        )
        col_del, col_recategorize = st.columns([1, 2])
        with col_del:
            if del_rule_ids and st.button("🗑️ Excluir selecionadas", type="secondary"):
                for rid in del_rule_ids:
                    db.delete_rule(rid)
                st.rerun()
        with col_recategorize:
            if st.button("🔄 Re-categorizar TODOS os lançamentos com estas regras"):
                rules = db.get_rules()
                db.recategorize_all(rules)
                st.success("Todos os lançamentos foram re-categorizados.")
                st.rerun()
    else:
        st.info(
            "Nenhuma regra cadastrada ainda. "
            "Adicione palavras-chave para automatizar a categorização."
        )

