"""Timer-triggered entry point for the anomaly-detection function.

Runs daily (see function.json). It:
  1. loads non-suspect groundwater readings + regional rainfall,
  2. runs the three domain detectors,
  3. writes any new events to monitoring.anomaly_events.

CRITICAL events are picked up downstream by the Logic App, which polls the
table and dispatches the email / Teams alert.
"""

from __future__ import annotations

import logging

import azure.functions as func

from . import db, detectors


def main(timer: func.TimerRequest) -> None:
    logging.info("Anomaly detection run starting (past_due=%s).", timer.past_due)

    engine = db.get_engine()

    readings = db.load_readings(engine)
    rainfall = db.load_rainfall(engine)
    logging.info(
        "Loaded %d readings across %d wells and %d rainfall days.",
        len(readings),
        readings["well_id"].nunique() if not readings.empty else 0,
        len(rainfall),
    )

    events = detectors.run_all(readings, rainfall)
    logging.info("Detectors produced %d candidate events.", len(events))

    inserted = db.write_events(engine, events)
    logging.info("Anomaly detection run complete. %d new events stored.", inserted)
