/* ===========================================================================
   Indexes and reporting views.

   The views below pre-shape data for the Power BI model so that DirectQuery
   pushes the heavy lifting down to SQL and the report stays responsive.
   =========================================================================== */

/* ---- Indexes that support the most common report filters ---- */
CREATE INDEX ix_readings_date     ON monitoring.water_level_readings (reading_date) INCLUDE (well_id, water_level_mbgl, tds_mg_per_l);
CREATE INDEX ix_rainfall_date     ON monitoring.rainfall_observations (obs_date)    INCLUDE (station_id, rainfall_mm);
CREATE INDEX ix_anomaly_date_type ON monitoring.anomaly_events (event_date, anomaly_type) INCLUDE (severity, well_id);
GO

/* ---------------------------------------------------------------------------
   View: latest status per well (drives the regional overview map)
   Classifies each well as Normal / Watch / Critical based on its most recent
   open anomaly.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW monitoring.vw_well_status AS
WITH latest_reading AS (
    SELECT  r.well_id,
            r.reading_date,
            r.water_level_mbgl,
            r.tds_mg_per_l,
            ROW_NUMBER() OVER (PARTITION BY r.well_id ORDER BY r.reading_date DESC) AS rn
    FROM    monitoring.water_level_readings r
),
worst_anomaly AS (
    SELECT  well_id,
            MAX(CASE severity
                    WHEN 'CRITICAL' THEN 3
                    WHEN 'WARNING'  THEN 2
                    WHEN 'INFO'     THEN 1
                    ELSE 0 END) AS severity_rank
    FROM    monitoring.anomaly_events
    WHERE   event_date >= DATEADD(DAY, -30, CAST(SYSUTCDATETIME() AS DATE))
    GROUP BY well_id
)
SELECT  w.well_id,
        w.location_name,
        w.latitude,
        w.longitude,
        w.aquifer_name,
        w.management_area,
        w.coastal_flag,
        lr.reading_date      AS last_reading_date,
        lr.water_level_mbgl  AS last_water_level_mbgl,
        lr.tds_mg_per_l      AS last_tds_mg_per_l,
        CASE COALESCE(wa.severity_rank, 0)
            WHEN 3 THEN 'Critical'
            WHEN 2 THEN 'Watch'
            ELSE 'Normal'
        END AS status
FROM    monitoring.monitoring_wells w
LEFT JOIN latest_reading lr ON lr.well_id = w.well_id AND lr.rn = 1
LEFT JOIN worst_anomaly  wa ON wa.well_id = w.well_id;
GO

/* ---------------------------------------------------------------------------
   View: daily rainfall vs water level, aligned by date and management area.
   Feeds the Rainfall-Recharge Correlation page.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW monitoring.vw_rainfall_recharge AS
SELECT  w.management_area,
        r.reading_date            AS obs_date,
        AVG(r.water_level_mbgl)   AS avg_water_level_mbgl,
        ro.rainfall_mm
FROM    monitoring.water_level_readings r
JOIN    monitoring.monitoring_wells     w  ON w.well_id = r.well_id
LEFT JOIN monitoring.rainfall_observations ro
        ON ro.obs_date = r.reading_date
GROUP BY w.management_area, r.reading_date, ro.rainfall_mm;
GO
