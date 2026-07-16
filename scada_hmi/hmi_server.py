"""
AquaSentry SCADA HMI Server
============================
Serves live SCADA tag data alongside ML predictions and anomaly scores via a
REST + WebSocket API consumed by the HMI overlay (hmi_dashboard.html).

Process tags are read from the local OPC-UA simulator (opcua_server.py) through
an OPC-UA subscription in opcua_client.py. The remaining context comes from:
  - ml/artifacts/forecast_predictions.csv → 30-day ahead ML forecast
  - ml/artifacts/anomaly_scores.csv       → Isolation Forest scores
  - sample_data/anomaly_events.csv        → rule-based detector events
  - sample_data/monitoring_wells.csv      → well metadata (lat/lon, aquifer)
  - sample_data/water_level_readings.csv  → history + OPC-UA-down fallback

A production connection would require site-specific endpoint, security and
network configuration, followed by operational testing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import logging

logging.getLogger("asyncua").setLevel(logging.WARNING)  # silence nodeset noise

# Allow `from opcua_client import ...` when run as `python scada_hmi/hmi_server.py`
sys.path.insert(0, str(Path(__file__).parent))
from opcua_client import TagCache, run_opcua_client  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent.parent
WELLS_CSV      = BASE / "sample_data" / "monitoring_wells.csv"
READINGS_CSV   = BASE / "sample_data" / "water_level_readings.csv"
FORECASTS_CSV  = BASE / "ml" / "artifacts" / "forecast_predictions.csv"
ANOMALY_SC_CSV = BASE / "ml" / "artifacts" / "anomaly_scores.csv"
EVENTS_CSV     = BASE / "sample_data" / "anomaly_events.csv"
HMI_DIR        = Path(__file__).parent

# ---------------------------------------------------------------------------
# Load & pre-process data once at startup
# ---------------------------------------------------------------------------
def _read_optional_csv(
    path: Path,
    *,
    columns: list[str],
    parse_dates: list[str] | None = None,
) -> pd.DataFrame:
    """Load a generated CSV when present, otherwise return an empty frame."""
    if not path.exists():
        return pd.DataFrame(columns=columns)
    return pd.read_csv(path, parse_dates=parse_dates)


wells      = pd.read_csv(WELLS_CSV)
readings   = pd.read_csv(READINGS_CSV, parse_dates=["reading_date"])
forecasts  = _read_optional_csv(
    FORECASTS_CSV,
    columns=["well_id", "reading_date", "target_date", "actual", "predicted"],
    parse_dates=["reading_date", "target_date"],
)
anom_scores = _read_optional_csv(
    ANOMALY_SC_CSV,
    columns=["well_id", "reading_date", "anomaly_score", "is_anomaly"],
    parse_dates=["reading_date"],
)
events     = pd.read_csv(EVENTS_CSV, parse_dates=["event_date"])

# Latest reading date in the dataset — treat as "now" for the demo
LATEST_DATE: date = readings["reading_date"].max().date()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _well_status(well_id: int) -> str:
    """Derive current status from rule-based events on the latest date."""
    recent = events[
        (events["well_id"] == well_id) &
        (events["event_date"].dt.date >= LATEST_DATE - timedelta(days=1))
    ]
    if recent.empty:
        return "Normal"
    sev = recent["severity"].tolist()
    if "CRITICAL" in sev:
        return "Critical"
    return "Watch"


def _latest_reading(well_id: int) -> dict[str, Any]:
    r = readings[readings["well_id"] == well_id].sort_values("reading_date").iloc[-1]
    return {
        "date":           r["reading_date"].date().isoformat(),
        "level_mbgl":     round(float(r["water_level_mbgl"]), 3),
        "tds_mg_per_l":   round(float(r["tds_mg_per_l"]), 1),
    }


def _forecast_series(well_id: int) -> list[dict[str, Any]]:
    """Last issued 30-day forecast for a well (up to 30 points)."""
    wf = (
        forecasts[forecasts["well_id"] == well_id]
        .sort_values("reading_date")
    )
    if wf.empty:
        return []
    latest_issue = wf["reading_date"].max()
    series = wf[wf["reading_date"] == latest_issue].sort_values("target_date")
    return [
        {
            "target_date": row["target_date"].date().isoformat(),
            "predicted":   round(float(row["predicted"]), 3),
            "actual":      round(float(row["actual"]), 3) if pd.notna(row.get("actual")) else None,
        }
        for _, row in series.iterrows()
    ]


def _anomaly_trend(well_id: int, days: int = 30) -> list[dict[str, Any]]:
    cutoff = pd.Timestamp(LATEST_DATE) - pd.Timedelta(days=days)
    subset = (
        anom_scores[
            (anom_scores["well_id"] == well_id) &
            (anom_scores["reading_date"] >= cutoff)
        ]
        .sort_values("reading_date")
    )
    return [
        {
            "date":          row["reading_date"].date().isoformat(),
            "anomaly_score": round(float(row["anomaly_score"]), 4),
            "is_anomaly":    bool(row["is_anomaly"]),
        }
        for _, row in subset.iterrows()
    ]


def _active_alerts() -> list[dict[str, Any]]:
    cutoff = pd.Timestamp(LATEST_DATE) - pd.Timedelta(days=3)
    recent = events[events["event_date"] >= cutoff].sort_values(
        "event_date", ascending=False
    )
    return [
        {
            "well_id":      int(r["well_id"]),
            "date":         r["event_date"].date().isoformat(),
            "type":         r["anomaly_type"],
            "severity":     r["severity"],
            "detail":       r["detail"],
        }
        for _, r in recent.iterrows()
    ]


def _fleet_snapshot() -> list[dict[str, Any]]:
    snapshot = []
    for _, w in wells.iterrows():
        wid = int(w["well_id"])
        rdg = _latest_reading(wid)
        fc  = _forecast_series(wid)
        snapshot.append({
            "well_id":         wid,
            "location_name":   w["location_name"],
            "aquifer_name":    w["aquifer_name"],
            "management_area": w["management_area"],
            "coastal_flag":    bool(w["coastal_flag"]),
            "lat":             float(w["latitude"]),
            "lon":             float(w["longitude"]),
            "status":          _well_status(wid),
            "latest_reading":  rdg,
            "forecast_30d":    fc[-1]["predicted"] if fc else None,
            "forecast_delta":  round(
                (fc[-1]["predicted"] - rdg["level_mbgl"]), 3
            ) if fc else None,
        })
    return snapshot

# ---------------------------------------------------------------------------
# OPC-UA data-acquisition: live tag cache fed by a real OPC-UA subscription
# ---------------------------------------------------------------------------
tag_cache = TagCache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the OPC-UA client subscription for the life of the server."""
    stop = asyncio.Event()
    task = asyncio.create_task(run_opcua_client(tag_cache, stop))
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="AquaSentry SCADA HMI", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static HMI files
app.mount("/static", StaticFiles(directory=str(HMI_DIR)), name="static")


@app.get("/", include_in_schema=False)
def serve_hmi():
    return FileResponse(str(HMI_DIR / "hmi_dashboard.html"))


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/fleet")
def fleet():
    """Fleet-level snapshot: all wells, latest reading, 30-day forecast delta."""
    return _fleet_snapshot()


@app.get("/api/well/{well_id}")
def well_detail(well_id: int):
    """Full detail for one well: readings history, forecast series, anomaly trend, alerts."""
    w = wells[wells["well_id"] == well_id].iloc[0]
    history = (
        readings[readings["well_id"] == well_id]
        .sort_values("reading_date")
        .tail(90)
    )
    return {
        "meta": {
            "well_id":         well_id,
            "location_name":   w["location_name"],
            "aquifer_name":    w["aquifer_name"],
            "management_area": w["management_area"],
            "coastal_flag":    bool(w["coastal_flag"]),
            "lat":             float(w["latitude"]),
            "lon":             float(w["longitude"]),
        },
        "status":          _well_status(well_id),
        "latest_reading":  _latest_reading(well_id),
        "history_90d": [
            {
                "date":         row["reading_date"].date().isoformat(),
                "level_mbgl":   round(float(row["water_level_mbgl"]), 3),
                "tds_mg_per_l": round(float(row["tds_mg_per_l"]), 1),
            }
            for _, row in history.iterrows()
        ],
        "forecast_series": _forecast_series(well_id),
        "anomaly_trend":   _anomaly_trend(well_id),
        "active_alerts": [
            a for a in _active_alerts() if a["well_id"] == well_id
        ],
    }


@app.get("/api/alerts")
def alerts():
    """All active alerts across the fleet (last 3 days)."""
    return _active_alerts()


@app.get("/api/scada/live")
def scada_live():
    """Current SCADA tag values from the OPC-UA subscription (curl-testable)."""
    return {
        "source":      "OPC-UA" if tag_cache.connected else "fallback",
        "connected":   tag_cache.connected,
        "last_update": tag_cache.last_update,
        "tags":        _build_scada_tags(),
    }


@app.get("/api/opcua/status")
def opcua_status():
    """OPC-UA acquisition health for the HMI connection badge."""
    return {
        "connected":   tag_cache.connected,
        "last_update": tag_cache.last_update,
        "tag_count":   sum(
            1 for v in tag_cache.snapshot().values() if "level" in v
        ),
    }


@app.get("/api/summary")
def summary():
    """KPI summary for the fleet header bar."""
    snap = _fleet_snapshot()
    statuses = [w["status"] for w in snap]
    return {
        "as_of":           LATEST_DATE.isoformat(),
        "total_bores":     len(snap),
        "critical":        statuses.count("Critical"),
        "watch":           statuses.count("Watch"),
        "normal":          statuses.count("Normal"),
        "avg_level_mbgl":  round(
            sum(w["latest_reading"]["level_mbgl"] for w in snap) / len(snap), 2
        ),
    }


# ---------------------------------------------------------------------------
# WebSocket — simulated live SCADA tick (every 5 seconds)
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        for ws in list(self.active):
            try:
                await ws.send_text(msg)
            except Exception:
                self.active.remove(ws)


manager = ConnectionManager()


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """
    Pushes a simulated SCADA tick every 5 s.
    In production this would subscribe to OPC-UA / Azure IoT Hub events.
    """
    await manager.connect(ws)
    try:
        while True:
            tick = {
                "type":      "scada_tick",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "tags":      _build_scada_tags(),
            }
            await ws.send_text(json.dumps(tick))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        manager.disconnect(ws)


def _build_scada_tags() -> list[dict[str, Any]]:
    """
    Build the SCADA tag list from the live OPC-UA subscription cache.

    If the OPC-UA server is unreachable, fall back to each bore's last stored
    reading and flag the quality as 'Uncertain' so the HMI shows degraded data
    honestly rather than silently inventing values.
    """
    snap = tag_cache.snapshot()
    tags = []
    for _, w in wells.iterrows():
        wid = int(w["well_id"])
        t = snap.get(wid, {})
        if "level" not in t:  # OPC-UA not yet connected → fallback
            rdg = _latest_reading(wid)
            t = {
                "level": rdg["level_mbgl"], "tds": rdg["tds_mg_per_l"],
                "pump": 0, "valve": 0, "quality": "Uncertain",
            }
        tags.append({
            "well_id":       wid,
            "location":      w["location_name"],
            "tag_level":     round(float(t.get("level", 0)), 3),
            "tag_tds":       round(float(t.get("tds", 0)), 1),
            "tag_pump_rpm":  int(t.get("pump", 0)),
            "tag_valve_pct": int(t.get("valve", 0)),
            "quality":       t.get("quality", "Good"),
            "source":        "OPC-UA" if tag_cache.connected else "fallback",
        })
    return tags


if __name__ == "__main__":
    # No reload: the OPC-UA client runs as a lifespan background task.
    uvicorn.run(app, host="0.0.0.0", port=8080)
