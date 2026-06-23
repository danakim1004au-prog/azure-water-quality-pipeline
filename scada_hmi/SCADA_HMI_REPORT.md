# AquaSentry SCADA HMI + OPC-UA + AI Overlay — Technical Report

## 1. Overview

This module extends the AquaSentry pipeline with a **web-based SCADA Human–Machine Interface (HMI)** that acquires live process tags over a **real OPC-UA subscription** and overlays the machine-learning outputs directly on top of them. The result is a single-screen operational view where a control-room engineer sees live SCADA readings and ML predictions together — without switching between a SCADA workstation and a separate BI tool.

**What is genuine here:** the OPC-UA protocol path is real. A standards-compliant OPC-UA server exposes one object per bore; the HMI connects as an OPC-UA client and receives **data-change notifications** over `opc.tcp://`. **What is simulated:** the field values themselves, because no physical sensors are attached — the OPC-UA server advances realistic values seeded from the project's demonstration-dataset readings. Swapping in real hardware means repointing the client endpoint, not rewriting the application.

**Implementation time:** ~1 day to add the OPC-UA server + client subscription on top of the existing HMI; 3–5 weeks for a production deployment against a real PLC/RTU estate or an Azure IoT Hub OPC-UA bridge.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│  FIELD / RTU LAYER                                                          │
│                                                                            │
│  scada_hmi/opcua_server.py  —  standards-compliant OPC-UA server           │
│  Endpoint: opc.tcp://0.0.0.0:4840/aquasentry/server/                        │
│  Namespace: http://aquasentry.systems/opcua                                 │
│                                                                            │
│  One object per bore (Bore_<id>_<location>) with variables:                 │
│    WaterLevel_mBGL · TDS_mg_per_L · PumpSpeed_RPM (writable)               │
│    ValvePosition_pct (writable) · SignalQuality                            │
│  Values seeded from demo readings, advanced every 2 s (bounded walk)        │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ opc.tcp:// — OPC-UA data-change subscription
┌───────────────────────────────▼────────────────────────────────────────────┐
│  DATA-ACQUISITION LAYER                                                     │
│                                                                            │
│  scada_hmi/opcua_client.py  —  OPC-UA client                               │
│  • browses the server, discovers bore objects + tag variables             │
│  • create_subscription(500ms) + subscribe_data_change(...)                 │
│  • DataChangeNotification → TagCache (in-memory, keyed by well_id)         │
│  • auto-reconnect with backoff; connection state exposed                   │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ in-process TagCache
┌───────────────────────────────▼────────────────────────────────────────────┐
│  HMI SERVER  —  scada_hmi/hmi_server.py  (FastAPI + WebSocket)              │
│                                                                            │
│  Lifespan task runs the OPC-UA client for the life of the server.          │
│  REST  GET /api/fleet         fleet snapshot + forecast delta              │
│        GET /api/well/{id}     full bore detail (history + AI)              │
│        GET /api/summary       KPI bar data                                 │
│        GET /api/alerts        active rule-based events                     │
│        GET /api/scada/live    current OPC-UA tag values (curl-testable)    │
│        GET /api/opcua/status  acquisition health (connected, tag_count)    │
│  WS    /ws/live               pushes OPC-UA tag snapshot every 5 s         │
│                                                                            │
│  Context joined from the existing pipeline:                                │
│   ml/artifacts/forecast_predictions.csv · anomaly_scores.csv              │
│   sample_data/anomaly_events.csv · monitoring_wells.csv · readings.csv     │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ HTTP + WebSocket
┌───────────────────────────────▼────────────────────────────────────────────┐
│  HMI DASHBOARD  —  scada_hmi/hmi_dashboard.html  (vanilla JS + Chart.js)    │
│                                                                            │
│  ① KPI header bar + live OPC-UA connection badge (tag count)               │
│  ② Well list (live OPC-UA level/TDS, AI forecast delta)                    │
│  ③ AI overlay banner (30-day forecast, anomaly score, live OPC-UA tags)    │
│  ④ SCADA tag grid (level, TDS, pump RPM, valve %)                          │
│  ⑤ Level chart (90-day history + AI forecast overlay)                      │
│  ⑥ Anomaly chart (30-day Isolation Forest score)                          │
│  ⑦ Active rule-based alerts                                                │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. The OPC-UA implementation in detail

### 3.1 Server (`opcua_server.py`)
- Built on `asyncua.Server`; registers a custom namespace and exposes the OPC-UA standard address space.
- For each of the six bores it adds an object `Bore_<id>_<location>` carrying five variables. `PumpSpeed_RPM` and `ValvePosition_pct` are marked **writable** (`set_writable()`), so a supervisory-control client could later command actuation — the OPC-UA half of "Respond".
- A 2-second loop advances each tag with a bounded random walk around values seeded from the latest demonstration-dataset reading, emulating continuous field telemetry with realistic dynamics.

### 3.2 Client + subscription (`opcua_client.py`)
- Connects to `opc.tcp://127.0.0.1:4840/...`, browses the Objects folder, and discovers bore objects by browse-name convention.
- Maps each tag variable's NodeId to an internal field, seeds initial values with a direct read, then registers a **subscription** (`create_subscription(500, handler)`) and **subscribes to data changes** (`subscribe_data_change(nodes)`).
- A `_SubHandler.datachange_notification(...)` callback routes every OPC-UA `DataChangeNotification` (with its `SourceTimestamp`) into a thread-light `TagCache`.
- Resilient: on any disconnect it flips `connected = False`, backs off, and reconnects — so the HMI degrades gracefully rather than crashing.

### 3.3 Server integration (`hmi_server.py`)
- The OPC-UA client runs as a **FastAPI lifespan background task** — it starts with the server and is cancelled cleanly on shutdown.
- `_build_scada_tags()` reads from the live `TagCache`. If OPC-UA is unreachable it falls back to each bore's last stored reading and stamps the quality `Uncertain` and source `fallback` — the HMI shows degraded data **honestly** instead of inventing values.

---

## 4. Verification

Tested end-to-end against the project's sample data and ML artifacts.

**OPC-UA acquisition health** (`GET /api/opcua/status`):
```json
{ "connected": true, "last_update": "2026-06-09T06:42:02.929740+00:00", "tag_count": 6 }
```

**Live tags changing over time** (`GET /api/scada/live`, well 1, 3 s apart) — proving real data-change notifications, not a one-off read:
```
t1: well1 level=6.524 tds=445.9   last_update=...06:40:54
t2: well1 level=6.517 tds=445.0   last_update=...06:40:58
t3: well1 level=6.487 tds=446.2   last_update=...06:41:02
```

**Graceful degradation** — with the OPC-UA server stopped, the HMI server still starts and reports `connected: false`, serving fallback readings flagged `Uncertain`.

**AI + rule-based context preserved** (`GET /api/summary`): 6 bores, 2 Critical, 4 Normal, avg 5.98 mBGL; alerts for Well 1 (RapidLevelChange) and Well 5 (SalinityIntrusionRisk) surface correctly alongside the live OPC-UA tags.

---

## 5. Production Pathway

### Phase 1 — Point at a real OPC-UA server (1–2 weeks)
Change one constant (`ENDPOINT` in `opcua_client.py`) to the real PLC/RTU or gateway OPC-UA URL, add security (certificates + `SignAndEncrypt`, user auth), and map real NodeIds. No other code changes — the subscription, cache and HMI are unchanged.

### Phase 2 — Azure IoT Hub OPC-UA bridge (1–2 weeks)
For cloud-connected sites, run Microsoft's **OPC Publisher** on an edge gateway to forward OPC-UA nodes into **Azure IoT Hub**; **Azure Stream Analytics** performs windowed aggregation before the HMI consumes the stream. This mirrors the batch pipeline's existing Azure footprint.

### Phase 3 — Closed-loop supervisory control (2–3 weeks)
The pump/valve nodes are already writable. Add an operator-approved write path:
```
AI forecast: aquifer recovering AND pump running
→ HMI surfaces "Reduce pump speed?" recommendation
→ operator approves → OPC-UA write: PumpSpeed_RPM ← current × 0.8
→ next data-change notification confirms the new value
```
This closes detection → decision → action — the defining loop of a production SCADA+AI system, with a human in the loop.

### Phase 4 — Azure Digital Twins (3–5 weeks)
Model each bore, aquifer and rainfall station as a twin; OPC-UA tags update twin properties in real time, enabling system-level questions ("if this bore fails, which downstream bores are at risk?").

---

## 6. How to Run

```bash
pip install -r scada_hmi/requirements.txt

# Terminal 1 — field OPC-UA server (PLC/RTU stand-in)
python scada_hmi/opcua_server.py

# Terminal 2 — HMI + OPC-UA client subscription
python scada_hmi/hmi_server.py
# open http://localhost:8080
```

The HMI connection badge shows `LIVE · OPC-UA (6 tags)` when the subscription is up, or `OPC-UA DOWN · fallback data` otherwise. No Azure connection is required for the demo.

---

## 7. Files Delivered

| File | Purpose |
|------|---------|
| `scada_hmi/opcua_server.py`     | OPC-UA server — field/RTU stand-in, writable pump/valve nodes |
| `scada_hmi/opcua_client.py`     | OPC-UA client — data-change subscription → TagCache |
| `scada_hmi/hmi_server.py`       | FastAPI server — REST + WebSocket; runs the OPC-UA client |
| `scada_hmi/hmi_dashboard.html`  | Single-page HMI — AI overlay, live OPC-UA tags, charts |
| `scada_hmi/requirements.txt`    | Dependencies (`fastapi`, `uvicorn`, `pandas`, `asyncua`) |
| `scada_hmi/SCADA_HMI_REPORT.md` | This report |
