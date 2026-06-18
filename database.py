import sqlite3
import hashlib
from pathlib import Path
from contextlib import contextmanager


DB_PATH = Path(__file__).parent / "data" / "florence.db"


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword  TEXT    NOT NULL UNIQUE,
                category TEXT    NOT NULL,
                priority INTEGER DEFAULT 0
            );
        """)


def _hash(date, description, amount):
    raw = f"{date}|{description}|{amount:.2f}"
    return hashlib.md5(raw.encode()).hexdigest()


def insert_transactions(rows: list[dict]) -> tuple[int, int]:
    """Insert a list of transaction dicts. Returns (inserted, skipped)."""
    inserted = skipped = 0
    with _conn() as con:
        for r in rows:
            h = _hash(r["date"], r["description"], r["amount"])
            try:
                con.execute(
                    """INSERT INTO transactions
                       (row_hash, date, description, amount, category, source_file, month, year)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (h, r["date"], r["description"], r["amount"],
                     r.get("category", "OUTROS"), r.get("source_file", ""),
                     r.get("month"), r.get("year")),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return inserted, skipped


def get_transactions(month: int = None, year: int = None, category: str = None) -> list[dict]:
    clauses, params = [], []
    if month:
        clauses.append("month = ?")
        params.append(month)
    if year:
        clauses.append("year = ?")
        params.append(year)
    if category:
        clauses.append("category = ?")
        params.append(category)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM transactions {where} ORDER BY date DESC", params
        ).fetchall()
    return [dict(r) for r in rows]


def update_category(transaction_id: int, category: str):
    with _conn() as con:
        con.execute(
            "UPDATE transactions SET category = ? WHERE id = ?",
            (category, transaction_id),
        )


def delete_transaction(transaction_id: int):
    with _conn() as con:
        con.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))


def get_rules() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM rules ORDER BY priority DESC, keyword"
        ).fetchall()
    return [dict(r) for r in rows]


def add_rule(keyword: str, category: str, priority: int = 0):
    with _conn() as con:
        con.execute(
            """INSERT INTO rules (keyword, category, priority)
               VALUES (?, ?, ?)
               ON CONFLICT(keyword) DO UPDATE SET category=excluded.category, priority=excluded.priority""",
            (keyword.strip().upper(), category, priority),
        )


def delete_rule(rule_id: int):
    with _conn() as con:
        con.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def get_available_years() -> list[int]:
    with _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT year FROM transactions WHERE year IS NOT NULL ORDER BY year DESC"
        ).fetchall()
    return [r["year"] for r in rows]


def recategorize_all(rules: list[dict]):
    """Re-apply all rules to every transaction in the DB."""
    with _conn() as con:
        txs = con.execute(
            "SELECT id, description FROM transactions"
        ).fetchall()
        for tx in txs:
            desc_upper = tx["description"].upper()
            for rule in rules:
                if rule["keyword"].upper() in desc_upper:
                    con.execute(
                        "UPDATE transactions SET category = ? WHERE id = ?",
                        (rule["category"], tx["id"]),
                    )
                    break
