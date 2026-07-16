/* ===========================================================================
   Schema for the regional water-quality monitoring platform.

   Design notes
   ------------
   * Every fact table carries a `data_quality_flag` and an `ingested_at`
     column so downstream reporting can distinguish trusted observations from
     interpolated / suspect ones in routine monitoring reports.
   * Natural keys from the public source systems are preserved
     (e.g. dh_no for drillholes, station_id for weather stations) so the
     warehouse can be reconciled against the source of truth at any time.
   * Tables are intentionally narrow and normalised; Power BI builds its star
     schema on top via relationships rather than baking it into storage.
   =========================================================================== */

IF SCHEMA_ID('monitoring') IS NULL
    EXEC('CREATE SCHEMA monitoring');
GO

/* Drop views before their source tables so the schema can be reapplied safely. */
DROP VIEW IF EXISTS monitoring.vw_water_security_risk;
DROP VIEW IF EXISTS monitoring.vw_licence_compliance;
DROP VIEW IF EXISTS monitoring.vw_rainfall_recharge;
DROP VIEW IF EXISTS monitoring.vw_well_status;
GO

/* Drop dependent tables first. */
DROP TABLE IF EXISTS monitoring.water_security_risk_snapshots;
DROP TABLE IF EXISTS monitoring.metered_extraction;
DROP TABLE IF EXISTS monitoring.anomaly_events;
DROP TABLE IF EXISTS monitoring.surface_water_readings;
DROP TABLE IF EXISTS monitoring.rainfall_observations;
DROP TABLE IF EXISTS monitoring.water_level_readings;
DROP TABLE IF EXISTS monitoring.water_licences;
DROP TABLE IF EXISTS monitoring.monitoring_wells;
GO

/* ---------------------------------------------------------------------------
   Reference dimension: groundwater monitoring wells (drillholes)
   --------------------------------------------------------------------------- */
CREATE TABLE monitoring.monitoring_wells (
    well_id         INT            NOT NULL PRIMARY KEY,   -- internal surrogate
    source_dh_no    NVARCHAR(30)   NOT NULL,               -- drillhole number from source
    location_name   NVARCHAR(120)  NULL,
    latitude        DECIMAL(9,6)   NULL,
    longitude       DECIMAL(9,6)   NULL,
    aquifer_name    NVARCHAR(120)  NULL,
    management_area NVARCHAR(120)  NULL,                    -- prescribed water area
    coastal_flag    BIT            NOT NULL DEFAULT 0,      -- relevant to salinity risk
    ingested_at     DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_wells_source UNIQUE (source_dh_no)
);
GO

/* ---------------------------------------------------------------------------
   Fact: groundwater level + salinity readings
   water_level_mbgl = metres below ground level (larger = deeper water table)
   tds_mg_per_l     = total dissolved solids (a proxy for salinity)
   --------------------------------------------------------------------------- */
CREATE TABLE monitoring.water_level_readings (
    reading_id        BIGINT        IDENTITY(1,1) PRIMARY KEY,
    well_id           INT           NOT NULL,
    reading_date      DATE          NOT NULL,
    water_level_mbgl  DECIMAL(8,3)  NULL,
    tds_mg_per_l      DECIMAL(10,2) NULL,
    pumping_event     BIT           NOT NULL DEFAULT 0,
    data_quality_flag NVARCHAR(20)  NOT NULL DEFAULT 'measured', -- measured | interpolated | suspect
    ingested_at       DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT fk_readings_well FOREIGN KEY (well_id)
        REFERENCES monitoring.monitoring_wells (well_id),
    CONSTRAINT uq_reading UNIQUE (well_id, reading_date)
);
GO

/* ---------------------------------------------------------------------------
   Fact: daily rainfall observations from weather stations
   --------------------------------------------------------------------------- */
CREATE TABLE monitoring.rainfall_observations (
    obs_id            BIGINT        IDENTITY(1,1) PRIMARY KEY,
    station_id        NVARCHAR(20)  NOT NULL,
    station_name      NVARCHAR(120) NULL,
    management_area   NVARCHAR(120) NOT NULL,
    obs_date          DATE          NOT NULL,
    rainfall_mm       DECIMAL(7,2)  NULL,
    data_quality_flag NVARCHAR(20)  NOT NULL DEFAULT 'measured',
    ingested_at       DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_rainfall UNIQUE (station_id, obs_date)
);
GO

/* ---------------------------------------------------------------------------
   Fact: surface-water / reservoir level readings (multi-source enrichment)
   --------------------------------------------------------------------------- */
CREATE TABLE monitoring.surface_water_readings (
    sw_reading_id     BIGINT        IDENTITY(1,1) PRIMARY KEY,
    site_id           NVARCHAR(30)  NOT NULL,
    site_name         NVARCHAR(120) NULL,
    reading_datetime  DATETIME2(0)  NOT NULL,
    metric_name       NVARCHAR(40)  NOT NULL,   -- e.g. 'level_m', 'discharge_cumecs'
    metric_value      DECIMAL(12,3) NULL,
    data_quality_flag NVARCHAR(20)  NOT NULL DEFAULT 'measured',
    ingested_at       DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_surface UNIQUE (site_id, reading_datetime, metric_name)
);
GO

/* ---------------------------------------------------------------------------
   Output: anomaly events produced by the detection function
   --------------------------------------------------------------------------- */
CREATE TABLE monitoring.anomaly_events (
    event_id        BIGINT        IDENTITY(1,1) PRIMARY KEY,
    well_id         INT           NULL,
    detected_at     DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    event_date      DATE          NOT NULL,        -- date the anomalous reading relates to
    anomaly_type    NVARCHAR(40)  NOT NULL,        -- RapidLevelChange | LowRechargeResponse | SalinityIntrusionRisk
    severity        NVARCHAR(10)  NOT NULL,        -- INFO | WARNING | CRITICAL
    threshold_value DECIMAL(12,3) NULL,
    actual_value    DECIMAL(12,3) NULL,
    detail          NVARCHAR(400) NULL,            -- human-readable explanation
    alert_sent      BIT           NOT NULL DEFAULT 0,
    CONSTRAINT fk_anomaly_well FOREIGN KEY (well_id)
        REFERENCES monitoring.monitoring_wells (well_id)
);
GO

/* ---------------------------------------------------------------------------
   Phase 2: groundwater licence allocation and metered extraction
   --------------------------------------------------------------------------- */
CREATE TABLE monitoring.water_licences (
    licence_id          NVARCHAR(40)  NOT NULL PRIMARY KEY,
    management_area     NVARCHAR(120) NOT NULL,
    licence_start_date  DATE          NOT NULL,
    licence_end_date    DATE          NOT NULL,
    annual_allocation_ml DECIMAL(12,2) NOT NULL,
    compliance_limit_pct DECIMAL(6,2)  NOT NULL DEFAULT 100.0,
    ingested_at         DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT ck_licence_dates CHECK (licence_end_date >= licence_start_date),
    CONSTRAINT ck_licence_allocation CHECK (annual_allocation_ml > 0)
);
GO

CREATE TABLE monitoring.metered_extraction (
    extraction_id      BIGINT        IDENTITY(1,1) PRIMARY KEY,
    well_id            INT           NOT NULL,
    reading_month      DATE          NOT NULL,
    coverage_days      SMALLINT      NOT NULL,
    extraction_ml      DECIMAL(12,2) NOT NULL,
    data_quality_flag  NVARCHAR(20)  NOT NULL DEFAULT 'measured',
    ingested_at        DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT fk_extraction_well FOREIGN KEY (well_id)
        REFERENCES monitoring.monitoring_wells (well_id),
    CONSTRAINT uq_extraction UNIQUE (well_id, reading_month),
    CONSTRAINT ck_extraction_nonnegative CHECK (extraction_ml >= 0),
    CONSTRAINT ck_extraction_coverage CHECK (coverage_days BETWEEN 1 AND 31)
);
GO

/* Power BI-ready snapshot produced by analytics/water_security_risk.py. */
CREATE TABLE monitoring.water_security_risk_snapshots (
    risk_snapshot_date                DATE           NOT NULL,
    well_id                           INT            NOT NULL,
    source_dh_no                      NVARCHAR(30)   NOT NULL,
    location_name                     NVARCHAR(120)  NULL,
    management_area                   NVARCHAR(120)  NOT NULL,
    aquifer_name                      NVARCHAR(120)  NULL,
    licence_id                        NVARCHAR(40)   NULL,
    annual_allocation_ml              DECIMAL(12,2) NULL,
    extraction_ytd_ml                 DECIMAL(12,2) NULL,
    allocation_used_pct               DECIMAL(7,2)  NULL,
    projected_year_end_extraction_ml  DECIMAL(12,2) NULL,
    projected_allocation_pct          DECIMAL(7,2)  NULL,
    latest_level_mbgl                 DECIMAL(8,3)  NULL,
    forecast_60d_mbgl                 DECIMAL(8,3)  NULL,
    forecast_lower_80_mbgl            DECIMAL(8,3)  NULL,
    forecast_upper_80_mbgl            DECIMAL(8,3)  NULL,
    forecast_change_mbgl              DECIMAL(8,3)  NULL,
    data_completeness_30d_pct         DECIMAL(6,2)  NULL,
    latest_anomaly_type               NVARCHAR(40)  NULL,
    latest_anomaly_severity           NVARCHAR(10)  NULL,
    risk_score                        SMALLINT       NOT NULL,
    risk_status                       NVARCHAR(10)   NOT NULL,
    risk_drivers                      NVARCHAR(500)  NOT NULL,
    recommended_action                NVARCHAR(500)  NOT NULL,
    ingested_at                       DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_water_security_risk PRIMARY KEY (risk_snapshot_date, well_id),
    CONSTRAINT fk_risk_well FOREIGN KEY (well_id)
        REFERENCES monitoring.monitoring_wells (well_id),
    CONSTRAINT fk_risk_licence FOREIGN KEY (licence_id)
        REFERENCES monitoring.water_licences (licence_id),
    CONSTRAINT ck_risk_score CHECK (risk_score BETWEEN 0 AND 100),
    CONSTRAINT ck_risk_status CHECK (risk_status IN ('Normal', 'Watch', 'Critical'))
);
GO
