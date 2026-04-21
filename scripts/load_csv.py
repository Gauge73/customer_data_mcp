"""
Load customer_data.csv into a SQLite database with indexes and FTS5 support.

Usage:
    python scripts/load_csv.py [--csv PATH] [--db PATH]

Defaults:
    --csv  data/customer_data.csv
    --db   data/customers.db
"""

import argparse
import csv
import os
import sqlite3
import sys
import time

CHUNK_SIZE = 100_000

DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    last_name  TEXT NOT NULL,
    first_name TEXT NOT NULL,
    ssn        TEXT NOT NULL,
    dob        TEXT NOT NULL,
    city       TEXT NOT NULL,
    state      TEXT NOT NULL,
    zip        TEXT NOT NULL,
    ccn        TEXT NOT NULL,
    cc_exp     TEXT NOT NULL
);
"""

# Duplicate SSNs are removed after bulk load (faster than enforcing the
# constraint on every insert). The unique index is created afterward on
# clean data.
DEDUP_SQL = """
DELETE FROM customers
WHERE id NOT IN (
    SELECT MIN(id) FROM customers GROUP BY ssn
);
"""

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ssn       ON customers(ssn);",
    "CREATE INDEX IF NOT EXISTS idx_last_name  ON customers(last_name);",
    "CREATE INDEX IF NOT EXISTS idx_first_name ON customers(first_name);",
    "CREATE INDEX IF NOT EXISTS idx_state      ON customers(state);",
    "CREATE INDEX IF NOT EXISTS idx_zip        ON customers(zip);",
]

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS customers_fts
USING fts5(
    first_name,
    last_name,
    city,
    content=customers,
    content_rowid=id
);
"""

FTS_POPULATE = """
INSERT INTO customers_fts(rowid, first_name, last_name, city)
SELECT id, first_name, last_name, city FROM customers;
"""

INSERT_SQL = """
INSERT INTO customers (last_name, first_name, ssn, dob, city, state, zip, ccn, cc_exp)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

EXPECTED_COLUMNS = ["last_name", "first_name", "ssn", "dob", "city", "state", "zip", "ccn", "cc_exp"]


def load(csv_path: str, db_path: str) -> None:
    if not os.path.exists(csv_path):
        print(f"ERROR: CSV not found at {csv_path}", file=sys.stderr)
        sys.exit(1)

    csv_size = os.path.getsize(csv_path)
    print(f"CSV: {csv_path} ({csv_size / 1_073_741_824:.2f} GB)")
    print(f"DB:  {db_path}")

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA temp_store=MEMORY;")
    con.execute("PRAGMA cache_size=-524288;")  # 512 MB page cache

    print("Creating table...")
    con.execute(DDL)
    con.commit()

    t0 = time.monotonic()
    total = 0

    print("Loading rows...")
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)

        # Validate header
        if reader.fieldnames != EXPECTED_COLUMNS:
            print(f"ERROR: unexpected columns: {reader.fieldnames}", file=sys.stderr)
            sys.exit(1)

        chunk: list[tuple] = []
        for row in reader:
            chunk.append((
                row["last_name"],
                row["first_name"],
                row["ssn"],
                row["dob"],
                row["city"],
                row["state"],
                row["zip"],
                row["ccn"],
                row["cc_exp"],
            ))
            if len(chunk) >= CHUNK_SIZE:
                con.executemany(INSERT_SQL, chunk)
                con.commit()
                total += len(chunk)
                chunk = []
                elapsed = time.monotonic() - t0
                print(f"  {total:>12,} rows  {elapsed:.0f}s  ({total / elapsed:,.0f} rows/s)", flush=True)

        if chunk:
            con.executemany(INSERT_SQL, chunk)
            con.commit()
            total += len(chunk)

    elapsed = time.monotonic() - t0
    print(f"Loaded {total:,} rows in {elapsed:.1f}s")

    print("Removing duplicate SSNs (keeping first occurrence)...")
    t1 = time.monotonic()
    con.execute(DEDUP_SQL)
    con.commit()
    remaining = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    print(f"  {remaining:,} rows after dedup ({time.monotonic() - t1:.1f}s)")

    print("Creating indexes (this may take several minutes)...")
    for sql in INDEXES:
        name = sql.split("idx_")[1].split(" ")[0]
        t1 = time.monotonic()
        con.execute(sql)
        con.commit()
        print(f"  idx_{name}: {time.monotonic() - t1:.1f}s")

    print("Building FTS5 index...")
    t1 = time.monotonic()
    con.execute(FTS_DDL)
    con.execute(FTS_POPULATE)
    con.commit()
    print(f"  FTS5: {time.monotonic() - t1:.1f}s")

    con.close()

    db_size = os.path.getsize(db_path)
    total_elapsed = time.monotonic() - t0
    print(f"\nDone. DB size: {db_size / 1_073_741_824:.2f} GB  Total time: {total_elapsed:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load customer CSV into SQLite")
    parser.add_argument("--csv", default="data/customer_data.csv")
    parser.add_argument("--db", default="data/customers.db")
    args = parser.parse_args()
    load(args.csv, args.db)


if __name__ == "__main__":
    main()
