import hashlib
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import errors as pg_errors


def _get_dsn() -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        # Tenta via st.secrets como fallback
        try:
            import streamlit as st
            dsn = st.secrets.get("DATABASE_URL")
        except Exception:
            pass
    if not dsn:
        raise RuntimeError("DATABASE_URL não configurada.")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)
    if "sslmode" not in dsn:
        dsn += "?sslmode=require"
    # Log seguro: mostra usuário e host, esconde senha
    import re
    safe = re.sub(r":([^:@]+)@", ":***@", dsn)
    print(f"[DB] Conectando: {safe}", flush=True)
    return dsn


@contextmanager
def _conn():
    con = psycopg2.connect(_get_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id          SERIAL PRIMARY KEY,
                    row_hash    TEXT    UNIQUE,
                    date        TEXT    NOT NULL,
                    description TEXT    NOT NULL,
                    amount      REAL    NOT NULL,
                    category    TEXT    NOT NULL DEFAULT 'OUTROS',
                    source_file TEXT,
                    month       INTEGER,
                    year        INTEGER,
                    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS rules (
                    id       SERIAL PRIMARY KEY,
                    keyword  TEXT    NOT NULL UNIQUE,
                    category TEXT    NOT NULL,
                    priority INTEGER DEFAULT 0
                );
            """)


def _hash(date, description, amount):
    raw = f"{date}|{description}|{amount:.2f}"
    return hashlib.md5(raw.encode()).hexdigest()


def insert_transactions(rows: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    with _conn() as con:
        with con.cursor() as cur:
            for r in rows:
                h = _hash(r["date"], r["description"], r["amount"])
                try:
                    cur.execute(
                        """INSERT INTO transactions
                           (row_hash, date, description, amount, category, source_file, month, year)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (h, r["date"], r["description"], r["amount"],
                         r.get("category", "OUTROS"), r.get("source_file", ""),
                         r.get("month"), r.get("year")),
                    )
                    inserted += 1
                except pg_errors.UniqueViolation:
                    con.rollback()
                    skipped += 1
    return inserted, skipped


def get_transactions(month: int = None, year: int = None, category: str = None) -> list[dict]:
    clauses, params = [], []
    if month:
        clauses.append("month = %s")
        params.append(month)
    if year:
        clauses.append("year = %s")
        params.append(year)
    if category:
        clauses.append("category = %s")
        params.append(category)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(f"SELECT * FROM transactions {where} ORDER BY date DESC", params)
            return [dict(r) for r in cur.fetchall()]


def update_category(transaction_id: int, category: str):
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE transactions SET category = %s WHERE id = %s",
                (category, transaction_id),
            )


def delete_transaction(transaction_id: int):
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("DELETE FROM transactions WHERE id = %s", (transaction_id,))


def get_rules() -> list[dict]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM rules ORDER BY priority DESC, keyword")
            return [dict(r) for r in cur.fetchall()]


def add_rule(keyword: str, category: str, priority: int = 0):
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """INSERT INTO rules (keyword, category, priority)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (keyword) DO UPDATE
                   SET category = EXCLUDED.category, priority = EXCLUDED.priority""",
                (keyword.strip().upper(), category, priority),
            )


def delete_rule(rule_id: int):
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("DELETE FROM rules WHERE id = %s", (rule_id,))


def get_available_years() -> list[int]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT year FROM transactions WHERE year IS NOT NULL ORDER BY year DESC"
            )
            return [r["year"] for r in cur.fetchall()]


def recategorize_all(rules: list[dict]):
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT id, description FROM transactions")
            txs = cur.fetchall()
            for tx in txs:
                desc_upper = tx["description"].upper()
                for rule in rules:
                    if rule["keyword"].upper() in desc_upper:
                        cur.execute(
                            "UPDATE transactions SET category = %s WHERE id = %s",
                            (rule["category"], tx["id"]),
                        )
                        break
