"""Shared helper to build a SQLAlchemy engine for Azure SQL from env vars.

Works with either driver so it runs on any laptop:
  * ODBC Driver 18 + pyodbc  (preferred; matches the production code path)
  * pymssql                  (fallback; pip-installable with no system ODBC)

Required environment variables:
  AZURE_SQL_SERVER    fully-qualified server name (…database.windows.net)
  AZURE_SQL_DB        database name
  AZURE_SQL_USER      admin login
  AZURE_SQL_PASSWORD  admin password
"""

from __future__ import annotations

import os
import urllib.parse

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Environment variable {name} is not set.")
    return value


def make_engine() -> Engine:
    server = _require("AZURE_SQL_SERVER")
    database = _require("AZURE_SQL_DB")
    user = _require("AZURE_SQL_USER")
    password = _require("AZURE_SQL_PASSWORD")

    # Prefer the ODBC path (identical to how the Function/ingestion connect).
    try:
        import pyodbc  # noqa: F401

        odbc = (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server=tcp:{server},1433;Database={database};"
            f"Uid={user};Pwd={password};"
            "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
        )
        return create_engine(
            "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
        )
    except ImportError:
        # Fallback: pymssql bundles its own TDS layer, no system ODBC needed.
        import pymssql  # noqa: F401

        url = URL.create(
            "mssql+pymssql",
            username=user,
            password=password,
            host=server,
            port=1433,
            database=database,
        )
        return create_engine(url)
