"""
AquaSentry SCADA HMI Server
============================
Serves real-time SCADA tag data alongside ML predictions and anomaly scores
via a REST + WebSocket API consumed by the HMI overlay (hmi_dashboard.html).

Data sources (offline demo):
  - sample_data/water_level_readings.csv   → simulates live SCADA tag feed
  - ml/artifacts/forecast_predictions.csv → 30-day ahead ML forecast
  - ml/artifacts/anomaly_scores.csv       → Isolation Forest scores
  - sample_data/anomaly_events.csv        → rule-based detector events
  - sample_data/monitoring_wells.csv      → well metadata (lat/lon, aquifer)

In a production deployment the SCADA tag feed would be replaced by an
OPC-UA subscription or Azure IoT Hub event stream.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
wells      = pd.read_csv(WELLS_CSV)
readings   = pd.read_csv(READINGS_CSV, parse_dates=["reading_date"])
forecasts  = pd.read_csv(FORECASTS_CSV, parse_dates=["reading_date", "target_date"])
anom_scores= pd.read_csv(ANOMALY_SC_CSV, parse_dates=["reading_date"])
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
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="AquaSentry SCADA HMI", version="1.0.0")

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
    Simulate SCADA tag values with small random walk from last known reading.
    In production: read from OPC-UA node or IoT Hub telemetry.
    """
    import random
    tags = []
    for _, w in wells.iterrows():
        wid = int(w["well_id"])
        rdg = _latest_reading(wid)
        tags.append({
            "well_id":      wid,
            "location":     w["location_name"],
            "tag_level":    round(rdg["level_mbgl"] + random.uniform(-0.02, 0.02), 3),
            "tag_tds":      round(rdg["tds_mg_per_l"] + random.uniform(-1.0, 1.0), 1),
            "tag_pump_rpm": random.choice([0, 0, 1450, 1450, 1450]),  # 0 = off
            "tag_valve_pct":random.choice([0, 0, 75, 100]),
            "quality":      "Good",
        })
    return tags


if __name__ == "__main__":
    uvicorn.run("hmi_server:app", host="0.0.0.0", port=8080, reload=True)
