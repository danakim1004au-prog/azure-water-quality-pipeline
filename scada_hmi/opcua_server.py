"""
AquaSentry OPC-UA Server  (simulated field / RTU layer)
=======================================================
Stands in for the PLC / RTU layer of a SCADA system. Exposes one OPC-UA
object per monitoring bore, each carrying live process-tag variables:

    WaterLevel_mBGL      water level (metres below ground level)
    TDS_mg_per_L         salinity / total dissolved solids
    PumpSpeed_RPM        bore pump speed       (writable — supervisory control)
    ValvePosition_pct    isolation valve open % (writable — supervisory control)
    SignalQuality        OPC-UA-style quality flag

Tag values are seeded from the latest real readings and then advanced on a
fixed interval with a bounded random walk, emulating continuous field
telemetry. A standards-compliant OPC-UA client (``opcua_client.py``, used by
the HMI server) subscribes to these nodes over ``opc.tcp://`` — the exact
protocol a real SCADA historian or HMI would use.

The pump and valve nodes are exposed as *writable*, so a future supervisory-
control client can issue actuation commands (the closed-loop "Respond" step).

Run:
    python scada_hmi/opcua_server.py
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import pandas as pd
from asyncua import Server

logging.basicConfig(level=logging.INFO)
logging.getLogger("asyncua").setLevel(logging.WARNING)  # silence nodeset noise
_logger = logging.getLogger("aquasentry.opcua.server")

BASE         = Path(__file__).parent.parent
WELLS_CSV    = BASE / "sample_data" / "monitoring_wells.csv"
READINGS_CSV = BASE / "sample_data" / "water_level_readings.csv"

ENDPOINT          = "opc.tcp://0.0.0.0:4840/aquasentry/server/"
NAMESPACE         = "http://aquasentry.systems/opcua"
UPDATE_INTERVAL_S = 2.0


def _seed_values() -> dict[int, dict]:
    """Seed each bore's tags from its most recent real reading."""
    wells    = pd.read_csv(WELLS_CSV)
    readings = pd.read_csv(READINGS_CSV, parse_dates=["reading_date"])
    seed: dict[int, dict] = {}
    for _, w in wells.iterrows():
        wid = int(w["well_id"])
        r = (
            readings[readings["well_id"] == wid]
            .sort_values("reading_date")
            .iloc[-1]
        )
        seed[wid] = {
            "location": str(w["location_name"]),
            "level":    float(r["water_level_mbgl"]),
            "tds":      float(r["tds_mg_per_l"]),
        }
    return seed


async def main() -> None:
    seed = _seed_values()

    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    server.set_server_name("AquaSentry Field OPC-UA Server")
    idx = await server.register_namespace(NAMESPACE)

    objects = server.nodes.objects
    well_nodes: dict[int, dict] = {}

    for wid, s in seed.items():
        safe_loc = s["location"].replace(" ", "_")
        obj = await objects.add_object(idx, f"Bore_{wid}_{safe_loc}")

        level = await obj.add_variable(idx, "WaterLevel_mBGL",   s["level"])
        tds   = await obj.add_variable(idx, "TDS_mg_per_L",      s["tds"])
        pump  = await obj.add_variable(idx, "PumpSpeed_RPM",     0.0)
        valve = await obj.add_variable(idx, "ValvePosition_pct", 0.0)
        qual  = await obj.add_variable(idx, "SignalQuality",     "Good")

        # Make actuation tags writable for a future supervisory-control client.
        await pump.set_writable()
        await valve.set_writable()

        well_nodes[wid] = {
            "level": level, "tds": tds, "pump": pump,
            "valve": valve, "qual": qual,
        }

    _logger.info("OPC-UA server ready at %s (%d bores)", ENDPOINT, len(well_nodes))

    state = {wid: dict(s) for wid, s in seed.items()}
    async with server:
        while True:
            for wid, nodes in well_nodes.items():
                st = state[wid]
                # Bounded random walk around the seeded values.
                st["level"] = round(st["level"] + random.uniform(-0.02, 0.02), 3)
                st["tds"]   = round(st["tds"]   + random.uniform(-1.0, 1.0), 1)
                pump_rpm    = random.choice([0, 0, 1450, 1450, 1450])  # 0 = off
                valve_pct   = random.choice([0, 0, 75, 100])

                await nodes["level"].write_value(st["level"])
                await nodes["tds"].write_value(st["tds"])
                await nodes["pump"].write_value(float(pump_rpm))
                await nodes["valve"].write_value(float(valve_pct))
            await asyncio.sleep(UPDATE_INTERVAL_S)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _logger.info("OPC-UA server stopped")
