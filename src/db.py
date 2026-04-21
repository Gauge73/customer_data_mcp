"""SQLite connection and query helpers for the customer database."""

import os
import sqlite3
from typing import Any

DB_PATH = os.environ.get("DB_PATH", "data/customers.db")

# WAL mode + a generous cache make reads fast under concurrent MCP tool calls.
_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA cache_size=-131072;
PRAGMA temp_store=MEMORY;
"""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    for stmt in _PRAGMAS.strip().splitlines():
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)
    return con


# Module-level connection — MCP servers are single-process, single-thread.
_con: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = _connect()
    return _con


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def get_customer(customer_id: int) -> dict | None:
    row = get_conn().execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


def find_by_ssn(ssn: str) -> dict | None:
    row = get_conn().execute(
        "SELECT * FROM customers WHERE ssn = ?", (ssn,)
    ).fetchone()
    return row_to_dict(row) if row else None


def search_customers(
    *,
    query: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    dob: str | None = None,
    limit: int = 20,
) -> list[dict]:
    limit = min(max(1, limit), 100)

    if query:
        # FTS path: free-text match, then optionally filter by structured fields
        sql = """
            SELECT c.*
            FROM customers_fts f
            JOIN customers c ON c.id = f.rowid
            WHERE customers_fts MATCH ?
        """
        params: list[Any] = [query]
        if state:
            sql += " AND c.state = ?"
            params.append(state.upper())
        if zip_code:
            sql += " AND c.zip = ?"
            params.append(zip_code)
        if dob:
            sql += " AND c.dob = ?"
            params.append(dob)
        if first_name:
            sql += " AND c.first_name LIKE ?"
            params.append(f"{first_name}%")
        if last_name:
            sql += " AND c.last_name LIKE ?"
            params.append(f"{last_name}%")
        sql += " LIMIT ?"
        params.append(limit)
    else:
        # Structured field path
        conditions: list[str] = []
        params = []
        if first_name:
            conditions.append("first_name LIKE ?")
            params.append(f"{first_name}%")
        if last_name:
            conditions.append("last_name LIKE ?")
            params.append(f"{last_name}%")
        if state:
            conditions.append("state = ?")
            params.append(state.upper())
        if zip_code:
            conditions.append("zip = ?")
            params.append(zip_code)
        if dob:
            conditions.append("dob = ?")
            params.append(dob)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM customers {where} LIMIT ?"
        params.append(limit)

    rows = get_conn().execute(sql, params).fetchall()
    return [row_to_dict(r) for r in rows]


def count_customers(
    *,
    query: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    dob: str | None = None,
) -> int:
    if query:
        sql = """
            SELECT COUNT(*)
            FROM customers_fts f
            JOIN customers c ON c.id = f.rowid
            WHERE customers_fts MATCH ?
        """
        params: list[Any] = [query]
        if state:
            sql += " AND c.state = ?"
            params.append(state.upper())
        if zip_code:
            sql += " AND c.zip = ?"
            params.append(zip_code)
        if dob:
            sql += " AND c.dob = ?"
            params.append(dob)
        if first_name:
            sql += " AND c.first_name LIKE ?"
            params.append(f"{first_name}%")
        if last_name:
            sql += " AND c.last_name LIKE ?"
            params.append(f"{last_name}%")
    else:
        conditions: list[str] = []
        params = []
        if first_name:
            conditions.append("first_name LIKE ?")
            params.append(f"{first_name}%")
        if last_name:
            conditions.append("last_name LIKE ?")
            params.append(f"{last_name}%")
        if state:
            conditions.append("state = ?")
            params.append(state.upper())
        if zip_code:
            conditions.append("zip = ?")
            params.append(zip_code)
        if dob:
            conditions.append("dob = ?")
            params.append(dob)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT COUNT(*) FROM customers {where}"

    return get_conn().execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

CUSTOMER_FIELDS = ("last_name", "first_name", "ssn", "dob", "city", "state", "zip", "ccn", "cc_exp")


def create_customer(data: dict) -> dict:
    missing = [f for f in CUSTOMER_FIELDS if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    con = get_conn()
    cur = con.execute(
        """
        INSERT INTO customers (last_name, first_name, ssn, dob, city, state, zip, ccn, cc_exp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(data[f] for f in CUSTOMER_FIELDS),
    )
    con.execute(
        "INSERT INTO customers_fts(rowid, first_name, last_name, city) VALUES (?, ?, ?, ?)",
        (cur.lastrowid, data["first_name"], data["last_name"], data["city"]),
    )
    con.commit()
    return get_customer(cur.lastrowid)


def update_customer(customer_id: int, data: dict) -> dict | None:
    allowed = set(CUSTOMER_FIELDS)
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields to update")

    con = get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [customer_id]
    con.execute(f"UPDATE customers SET {set_clause} WHERE id = ?", values)

    # Rebuild FTS entry if any FTS-indexed field changed
    if updates.keys() & {"first_name", "last_name", "city"}:
        con.execute("DELETE FROM customers_fts WHERE rowid = ?", (customer_id,))
        row = con.execute(
            "SELECT first_name, last_name, city FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if row:
            con.execute(
                "INSERT INTO customers_fts(rowid, first_name, last_name, city) VALUES (?, ?, ?, ?)",
                (customer_id, row["first_name"], row["last_name"], row["city"]),
            )

    con.commit()
    return get_customer(customer_id)


def delete_customer(customer_id: int) -> bool:
    con = get_conn()
    cur = con.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    if cur.rowcount:
        con.execute("DELETE FROM customers_fts WHERE rowid = ?", (customer_id,))
        con.commit()
        return True
    return False
