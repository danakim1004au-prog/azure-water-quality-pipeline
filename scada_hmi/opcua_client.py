"""
AquaSentry OPC-UA Client  (SCADA data-acquisition layer)
========================================================
Connects to the field OPC-UA server (``opcua_server.py``) and subscribes to
every bore's process tags over ``opc.tcp://``. Incoming OPC-UA
``DataChangeNotification`` events update an in-memory ``TagCache`` that the
HMI server then serves to the browser.

This is the *Data Acquisition* half of SCADA implemented for real: a
standards-compliant OPC-UA subscription (not an in-process simulation). The
field values themselves are simulated by the server because no physical
sensors are attached, but the protocol path — server → subscription →
data-change callback → cache — is genuine OPC-UA.
"""

from __future__ import annotations

import asyncio
import logging

from asyncua import Client

_logger = logging.getLogger("aquasentry.opcua.client")

ENDPOINT  = "opc.tcp://127.0.0.1:4840/aquasentry/server/"
NAMESPACE = "http://aquasentry.sawater/opcua"

# OPC-UA BrowseName -> internal cache field
_FIELD_BY_BROWSENAME = {
    "WaterLevel_mBGL":   "level",
    "TDS_mg_per_L":      "tds",
    "PumpSpeed_RPM":     "pump",
    "ValvePosition_pct": "valve",
    "SignalQuality":     "quality",
}


class TagCache:
    """Latest tag values keyed by well_id, plus connection state."""

    def __init__(self) -> None:
        self._tags: dict[int, dict] = {}
        self.connected: bool = False
        self.last_update: str | None = None

    def update(self, wid: int, field: str, value) -> None:
        self._tags.setdefault(wid, {})[field] = value

    def set_location(self, wid: int, location: str) -> None:
        self._tags.setdefault(wid, {})["location"] = location

    def snapshot(self) -> dict[int, dict]:
        return {wid: dict(v) for wid, v in self._tags.items()}


class _SubHandler:
    """asyncua subscription handler — routes data-change events into the cache."""

    def __init__(self, cache: TagCache, node_map: dict[str, tuple[int, str]]):
        self.cache = cache
        self.node_map = node_map

    def datachange_notification(self, node, val, data):  # noqa: D401 (asyncua API)
        mapping = self.node_map.get(node.nodeid.to_string())
        if mapping:
            wid, field = mapping
            self.cache.update(wid, field, val)
            self.cache.last_update = (
                data.monitored_item.Value.SourceTimestamp.isoformat()
                if data.monitored_item.Value.SourceTimestamp else None
            )


async def run_opcua_client(cache: TagCache, stop: asyncio.Event,
                           endpoint: str = ENDPOINT) -> None:
    """
    Long-running OPC-UA client: connect, discover bore objects, subscribe to
    their tag variables, and keep the cache live. Auto-reconnects on failure.
    """
    while not stop.is_set():
        try:
            async with Client(url=endpoint) as client:
                await client.get_namespace_index(NAMESPACE)  # verify namespace
                objects  = client.nodes.objects
                children = await objects.get_children()

                node_map: dict[str, tuple[int, str]] = {}
                sub_nodes = []

                for obj in children:
                    bn = await obj.read_browse_name()
                    if not bn.Name.startswith("Bore_"):
                        continue
                    parts = bn.Name.split("_", 2)
                    wid = int(parts[1])
                    location = parts[2].replace("_", " ") if len(parts) > 2 else f"Bore {wid}"
                    cache.set_location(wid, location)

                    for var in await obj.get_variables():
                        vbn = await var.read_browse_name()
                        field = _FIELD_BY_BROWSENAME.get(vbn.Name)
                        if field:
                            node_map[var.nodeid.to_string()] = (wid, field)
                            sub_nodes.append(var)
                            cache.update(wid, field, await var.read_value())

                handler = _SubHandler(cache, node_map)
                sub = await client.create_subscription(500, handler)
                await sub.subscribe_data_change(sub_nodes)

                cache.connected = True
                _logger.info("OPC-UA subscribed to %d tags across %d bores",
                             len(sub_nodes), len({w for w, _ in node_map.values()}))

                # Keep the session alive until asked to stop.
                while not stop.is_set():
                    await client.check_connection()
                    await asyncio.sleep(2)

        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            cache.connected = False
            _logger.warning("OPC-UA client error: %s — retrying in 3s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass

    cache.connected = False
    _logger.info("OPC-UA client stopped")
