/* ===========================================================================
   Indexes and reporting views.

   The views below pre-shape data for the Power BI model so that DirectQuery
   pushes the heavy lifting down to SQL and the report stays responsive.
   =========================================================================== */

/* ---- Indexes that support the most common report filters ---- */
CREATE INDEX ix_readings_date     ON monitoring.water_level_readings (reading_date) INCLUDE (well_id, water_level_mbgl, tds_mg_per_l);
CREATE INDEX ix_rainfall_date     ON monitoring.rainfall_observations (obs_date)    INCLUDE (station_id, rainfall_mm);
CREATE INDEX ix_anomaly_date_type ON monitoring.anomaly_events (event_date, anomaly_type) INCLUDE (severity, well_id);
CREATE INDEX ix_extraction_month  ON monitoring.metered_extraction (reading_month) INCLUDE (well_id, extraction_ml);
CREATE INDEX ix_risk_status       ON monitoring.water_security_risk_snapshots (risk_snapshot_date, risk_status) INCLUDE (well_id, risk_score);
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
       AND ro.management_area = w.management_area
GROUP BY w.management_area, r.reading_date, ro.rainfall_mm;
GO

/* ---------------------------------------------------------------------------
   Phase 2 view: latest bore-level water-security risk snapshot.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW monitoring.vw_water_security_risk AS
WITH latest_snapshot AS (
    SELECT  *,
            ROW_NUMBER() OVER (
                PARTITION BY well_id ORDER BY risk_snapshot_date DESC
            ) AS rn
    FROM monitoring.water_security_risk_snapshots
)
SELECT  risk_snapshot_date,
        well_id,
        source_dh_no,
        location_name,
        management_area,
        aquifer_name,
        licence_id,
        annual_allocation_ml,
        extraction_ytd_ml,
        allocation_used_pct,
        projected_year_end_extraction_ml,
        projected_allocation_pct,
        latest_level_mbgl,
        forecast_60d_mbgl,
        forecast_lower_80_mbgl,
        forecast_upper_80_mbgl,
        forecast_change_mbgl,
        data_completeness_30d_pct,
        latest_anomaly_type,
        latest_anomaly_severity,
        risk_score,
        risk_status,
        risk_drivers,
        recommended_action
FROM latest_snapshot
WHERE rn = 1;
GO

/* One row per licence period for compliance reporting and reconciliation. */
CREATE OR ALTER VIEW monitoring.vw_licence_compliance AS
SELECT  l.licence_id,
        l.management_area,
        l.licence_start_date,
        l.licence_end_date,
        l.annual_allocation_ml,
        l.compliance_limit_pct,
        SUM(CASE
                WHEN e.reading_month BETWEEN l.licence_start_date AND l.licence_end_date
                THEN e.extraction_ml ELSE 0
            END) AS extraction_to_date_ml,
        CAST(
            100.0 * SUM(CASE
                            WHEN e.reading_month BETWEEN l.licence_start_date AND l.licence_end_date
                            THEN e.extraction_ml ELSE 0
                        END) / NULLIF(l.annual_allocation_ml, 0)
            AS DECIMAL(7,2)
        ) AS allocation_used_pct
FROM monitoring.water_licences l
LEFT JOIN monitoring.monitoring_wells w
       ON w.management_area = l.management_area
LEFT JOIN monitoring.metered_extraction e
       ON e.well_id = w.well_id
      AND e.data_quality_flag <> 'suspect'
GROUP BY l.licence_id,
         l.management_area,
         l.licence_start_date,
         l.licence_end_date,
         l.annual_allocation_ml,
         l.compliance_limit_pct;
GO
