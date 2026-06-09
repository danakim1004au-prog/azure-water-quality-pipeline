# AquaSentry SCADA HMI + AI Overlay — Technical Report

## 1. Overview

This module extends the AquaSentry pipeline with a **web-based SCADA Human–Machine Interface (HMI)** that overlays AI model outputs directly onto live process tag data. The result is a single-screen operational view where a field operator or control-room engineer can see SCADA readings and ML predictions side by side — without switching between a SCADA workstation and a separate BI tool.

**Estimated implementation time:** 2–3 days for the prototype delivered here; 4–6 weeks for a production-grade deployment connected to a real OPC-UA server and Azure IoT Hub.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  FIELD / EDGE LAYER (simulated in demo)                             │
│                                                                     │
│  SCADA RTU / PLC ──OPC-UA──► Azure IoT Hub ──► Stream Analytics    │
│  (water level, TDS,               (ingestion)    (windowed agg.)   │
│   pump RPM, valve %)                                  │            │
└───────────────────────────────────────────────────────┼────────────┘
                                                        │ real-time stream
┌───────────────────────────────────────────────────────▼────────────┐
│  CLOUD / BATCH LAYER (existing AquaSentry pipeline)                 │
│                                                                     │
│  Azure Data Factory (daily ETL)                                     │
│  Azure SQL Database ──► Azure Functions (anomaly + forecast)        │
│          │                     │                                    │
│          │              ml/artifacts/                               │
│          │         forecast_predictions.csv                         │
│          │         anomaly_scores.csv                               │
└──────────┼──────────────────────────────────────────────────────── ┘
           │
┌──────────▼──────────────────────────────────────────────────────── ┐
│  HMI SERVER  (scada_hmi/hmi_server.py — FastAPI + WebSocket)        │
│                                                                     │
│  REST  GET /api/fleet        fleet snapshot + forecast delta        │
│        GET /api/well/{id}    full bore detail (history + AI)        │
│        GET /api/summary      KPI bar data                           │
│        GET /api/alerts       active rule-based events               │
│                                                                     │
│  WebSocket  /ws/live         SCADA tick every 5 s (simulated;       │
│                              production: OPC-UA subscription)       │
└──────────┬──────────────────────────────────────────────────────── ┘
           │ HTTP + WebSocket
┌──────────▼─────────────────────────────────────────────────────────┐
│  HMI DASHBOARD  (scada_hmi/hmi_dashboard.html — vanilla JS)         │
│                                                                     │
│  ① KPI header bar   (total bores, critical/watch/normal count)      │
│  ② Well list panel  (live SCADA level + TDS, AI forecast delta)     │
│  ③ AI overlay banner (30-day forecast, anomaly score, live tags)    │
│  ④ SCADA tag grid   (level, TDS, pump RPM, valve %)                 │
│  ⑤ Level chart      (90-day history + AI forecast overlay)          │
│  ⑥ Anomaly chart    (30-day Isolation Forest score bar chart)       │
│  ⑦ Active alerts    (rule-based detector events)                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Design Decisions

### 3.1 AI overlay, not a separate screen
The core idea is that an operator should never have to leave the HMI to see model outputs. The **AI overlay banner** sits at the top of every bore's detail panel, surfacing three numbers the operator cares about:
- **30-day ML forecast** (gradient-boosted regressor from `ml/forecast_train.py`)
- **Isolation Forest anomaly score** (from `ml/anomaly_unsupervised.py`)
- **Live SCADA level and TDS** refreshing every 5 seconds

Placing these side by side with live SCADA tags lets the operator immediately see whether the current reading is diverging from the model's expectation — the earliest possible signal of an emerging problem.

### 3.2 WebSocket for real-time SCADA tags
The HMI maintains a persistent WebSocket connection (`/ws/live`). The server pushes a **SCADA tick every 5 seconds** containing all six wells' live tag values. In the demo this is a small random walk around the last known reading — realistic enough to demonstrate the pattern.

In production the tick would be replaced by an **OPC-UA subscription** (via `asyncua`) or an **Azure IoT Hub consumer** (via `azure-eventhub`). The frontend code does not change; only the server-side `_build_scada_tags()` function is swapped out.

### 3.3 Human-in-the-loop (no automated actuation in the demo)
The HMI displays model outputs for **operator decision support only**. Automated pump/valve control commands are shown as a natural next step in Section 5 but are not implemented here, consistent with the project's explainability-first philosophy.

### 3.4 Zero new frontend dependencies
The dashboard is a single HTML file using **Chart.js (CDN)** and vanilla JavaScript. No build toolchain, no React, no bundler. This keeps the demo instantly runnable (`python scada_hmi/hmi_server.py`, open browser) and portable.

---

## 4. Data Validation Results

Tested against the project's existing sample dataset:

| Well | Location | Level (mBGL) | AI 30d Forecast | Status |
|------|----------|:---:|:---:|:---:|
| 1 | Virginia NAP-01 | 6.507 | 4.433 | **Critical** |
| 2 | Angle Vale NAP-02 | 4.498 | 4.835 | Normal |
| 3 | Nuriootpa BAR-01 | 5.031 | 5.813 | Normal |
| 4 | Tanunda BAR-02 | 7.336 | 7.082 | Normal |
| 5 | Maslin Beach MLV-01 | 5.995 | 6.475 | **Critical** |
| 6 | Port Willunga MLV-02 | 6.513 | 6.880 | Normal |

Active alerts surfaced by the HMI:
- **Well 1 — CRITICAL RapidLevelChange:** level 6.51 mBGL is 6.7σ deeper than the 7-day baseline
- **Well 5 — CRITICAL SalinityIntrusionRisk:** TDS rising 4.6 mg/L/day over 30 days (R²=0.99) in a coastal aquifer

Both are correctly displayed in the HMI alerts panel and reflected in the fleet-level KPI bar.

---

## 5. Production Pathway

### Phase 1 — OPC-UA integration (2–3 weeks)
Replace `_build_scada_tags()` in `hmi_server.py` with a real OPC-UA subscription:

```python
# pip install asyncua
from asyncua import Client

async def opc_ua_reader(url: str, node_ids: list[str]):
    async with Client(url) as client:
        while True:
            values = await asyncio.gather(*[
                client.get_node(nid).read_value() for nid in node_ids
            ])
            yield values
            await asyncio.sleep(1)
```

The WebSocket broadcast loop then consumes this async generator instead of the random-walk simulation.

### Phase 2 — Azure IoT Hub stream (1–2 weeks)
For cloud-connected RTUs, replace OPC-UA with an Azure IoT Hub consumer group:

```python
# pip install azure-eventhub
from azure.eventhub.aio import EventHubConsumerClient

async def iothub_reader(conn_str, eventhub_name):
    client = EventHubConsumerClient.from_connection_string(conn_str, "$Default", eventhub_name)
    async with client:
        await client.receive(on_event=on_scada_event)
```

Azure Stream Analytics can perform windowed aggregation (e.g. 1-minute averages) before the data reaches the HMI server, reducing noise.

### Phase 3 — Bidirectional control (2–4 weeks)
Once read-path trust is established, add **write-path commands** triggered from the HMI when the AI forecast exceeds a threshold:

```
AI forecast: bore recovering (level falling) AND pump currently running
→ HMI surfaces "Reduce pump speed?" recommendation
→ Operator clicks Approve
→ HMI server writes OPC-UA node: PumpSpeedSetpoint = current × 0.8
→ SCADA executes command on PLC
→ Next tick confirms new RPM
```

This closes the loop from detection → decision → action — the defining characteristic of a production SCADA+AI system.

### Phase 4 — Azure Digital Twins (4–6 weeks)
Model each bore, aquifer, and rainfall station as a Digital Twin. SCADA tags update twin properties in real time. The HMI queries twin relationships to answer questions like "if Virginia NAP-01 fails, which downstream bores are at risk?" — moving from single-asset monitoring to system-level situational awareness.

---

## 6. How to Run

```bash
# Install dependencies
pip install -r scada_hmi/requirements.txt

# Start the server (from repo root)
python scada_hmi/hmi_server.py

# Open browser
open http://localhost:8080
```

The server reads from `sample_data/` and `ml/artifacts/` — no Azure connection required for the demo. SCADA tags update live in the browser every 5 seconds via WebSocket.

---

## 7. Files Delivered

| File | Purpose |
|------|---------|
| `scada_hmi/hmi_server.py` | FastAPI server — REST endpoints + WebSocket SCADA tick |
| `scada_hmi/hmi_dashboard.html` | Single-page HMI — fleet view, AI overlay, live SCADA tags, charts |
| `scada_hmi/requirements.txt` | Python dependencies (`fastapi`, `uvicorn`, `pandas`) |
| `scada_hmi/SCADA_HMI_REPORT.md` | This report |
