import hashlib
import os


def _client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        try:
            import streamlit as st
            url = url or st.secrets.get("SUPABASE_URL")
            key = key or st.secrets.get("SUPABASE_SERVICE_KEY")
        except Exception:
            pass
    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_KEY precisam estar configurados.")
    from supabase import create_client
    return create_client(url, key)


def init_db():
    _client()  # Verifica conexão; tabelas criadas no dashboard do Supabase


def _hash(date, description, amount):
    raw = f"{date}|{description}|{amount:.2f}"
    return hashlib.md5(raw.encode()).hexdigest()


def _is_duplicate_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "23505" in msg or "duplicate" in msg or "unique" in msg


def insert_transactions(rows: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    sb = _client()
    for r in rows:
        h = _hash(r["date"], r["description"], r["amount"])
        data = {
            "row_hash": h,
            "date": r["date"],
            "description": r["description"],
            "amount": r["amount"],
            "category": r.get("category", "OUTROS"),
            "source_file": r.get("source_file", ""),
            "month": r.get("month"),
            "year": r.get("year"),
        }
        try:
            sb.table("transactions").insert(data).execute()
            inserted += 1
        except Exception as e:
            if _is_duplicate_error(e):
                skipped += 1
            else:
                raise
    return inserted, skipped


def get_transactions(month: int = None, year: int = None, category: str = None) -> list[dict]:
    sb = _client()
    query = sb.table("transactions").select("*").order("date", desc=True)
    if month:
        query = query.eq("month", month)
    if year:
        query = query.eq("year", year)
    if category:
        query = query.eq("category", category)
    return query.execute().data


def update_category(transaction_id: int, category: str):
    sb = _client()
    sb.table("transactions").update({"category": category}).eq("id", transaction_id).execute()


def delete_transaction(transaction_id: int):
    sb = _client()
    sb.table("transactions").delete().eq("id", transaction_id).execute()


def get_rules() -> list[dict]:
    sb = _client()
    return sb.table("rules").select("*").order("priority", desc=True).order("keyword").execute().data


def add_rule(keyword: str, category: str, priority: int = 0):
    sb = _client()
    kw = keyword.strip().upper()
    try:
        sb.table("rules").insert({"keyword": kw, "category": category, "priority": priority}).execute()
    except Exception as e:
        if _is_duplicate_error(e):
            sb.table("rules").update({"category": category, "priority": priority}).eq("keyword", kw).execute()
        else:
            raise


def delete_rule(rule_id: int):
    sb = _client()
    sb.table("rules").delete().eq("id", rule_id).execute()


def get_available_years() -> list[int]:
    sb = _client()
    result = sb.table("transactions").select("year").not_.is_("year", "null").execute()
    return sorted(set(r["year"] for r in result.data if r["year"]), reverse=True)


def recategorize_all(rules: list[dict]):
    sb = _client()
    txs = sb.table("transactions").select("id,description").execute().data
    for tx in txs:
        desc_upper = tx["description"].upper()
        for rule in rules:
            if rule["keyword"].upper() in desc_upper:
                sb.table("transactions").update({"category": rule["category"]}).eq("id", tx["id"]).execute()
                break
