"""Central configuration for the ingestion layer.

Values are read from environment variables so the same code runs locally, in
the Data Factory-hosted integration runtime, and in CI. Secrets (the SQL
connection string in particular) are never hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Study area: groundwater management areas and the wells / stations we track.
# These are public prescribed water areas in the state's groundwater network.
# Drillhole numbers are placeholders here; populate with real dh_no values
# discovered via the groundwater API (see groundwater_client.discover_wells).
# ---------------------------------------------------------------------------
MANAGEMENT_AREAS = [
    "Northern Adelaide Plains",
    "Barossa",
    "McLaren Vale",
]

# Public daily-rainfall stations, one near each management area.
# (station_id, friendly name, management area)
RAINFALL_STATIONS = [
    ("23083", "Edinburgh", "Northern Adelaide Plains"),
    ("23321", "Nuriootpa", "Barossa"),
    ("23753", "Willunga", "McLaren Vale"),
]


@dataclass
class IngestionWindow:
    """Date range to collect. Defaults to a five-year history."""

    start: date = field(
        default_factory=lambda: date.today() - timedelta(days=365 * 5)
    )
    end: date = field(default_factory=date.today)


@dataclass
class Settings:
    # Public REST endpoints. Documented and Creative-Commons licensed.
    groundwater_base_url: str = os.getenv(
        "GROUNDWATER_API_URL",
        "https://www.waterconnect.sa.gov.au/_layouts/15/dfw.sharepoint.wdd/WDDDMS.ashx",
    )
    rainfall_base_url: str = os.getenv(
        "RAINFALL_API_URL",
        "https://www.longpaddock.qld.gov.au/cgi-bin/silo/DataDrillDataset.php",
    )
    surface_water_base_url: str = os.getenv(
        "SURFACE_WATER_API_URL",
        "https://water.data.sa.gov.au/Export/BulkExport",
    )

    # SILO climate API requires a (any valid) contact email for attribution.
    rainfall_api_email: str = os.getenv("RAINFALL_API_EMAIL", "portfolio@example.com")

    # Where raw files are staged before loading.
    raw_output_dir: str = os.getenv("RAW_OUTPUT_DIR", "data/raw")

    # Azure SQL connection string (ODBC format). Sourced from Key Vault in
    # production; from the environment locally.
    sql_connection_string: str = os.getenv("SQL_CONNECTION_STRING", "")

    # HTTP politeness.
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT", "60"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))


SETTINGS = Settings()
