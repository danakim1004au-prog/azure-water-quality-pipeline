"""Apply the SQL schema files to Azure SQL.

The .sql files use `GO` batch separators (a client directive, not T-SQL), so
this splits each file on `GO` and executes the batches in order. This lets you
deploy the schema from any laptop without installing sqlcmd.

Usage (after setting the AZURE_SQL_* env vars — see scripts/_db.py):
    python scripts/apply_schema.py
"""

from __future__ import annotations

import os
import re

from sqlalchemy import text

from _db import make_engine

HERE = os.path.dirname(__file__)
SQL_DIR = os.path.join(HERE, "..", "sql")

SQL_FILES = [
    "01_schema.sql",
    "02_indexes_and_views.sql",
    "03_audit_and_procs.sql",
]


def split_batches(script: str) -> list[str]:
    """Split a script on standalone GO lines (case-insensitive)."""
    parts = re.split(r"(?im)^\s*GO\s*$", script)
    return [p.strip() for p in parts if p.strip()]


def main() -> int:
    engine = make_engine()
    with engine.begin() as conn:
        for filename in SQL_FILES:
            path = os.path.join(SQL_DIR, filename)
            with open(path, "r", encoding="utf-8") as fh:
                script = fh.read()
            batches = split_batches(script)
            print(f"Applying {filename} ({len(batches)} batches)...")
            for batch in batches:
                conn.execute(text(batch))
    print("Schema applied successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
