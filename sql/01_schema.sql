/* ===========================================================================
   Schema for the regional water-quality monitoring platform.

   Design notes
   ------------
   * Every fact table carries a `data_quality_flag` and an `ingested_at`
     column so downstream reporting can distinguish trusted observations from
     interpolated / suspect ones, supporting compliance-grade reporting.
   * Natural keys from the public source systems are preserved
     (e.g. dh_no for drillholes, station_id for weather stations) so the
     warehouse can be reconciled against the source of truth at any time.
   * Tables are intentionally narrow and normalised; Power BI builds its star
     schema on top via relationships rather than baking it into storage.
   =========================================================================== */

IF SCHEMA_ID('monitoring') IS NULL
    EXEC('CREATE SCHEMA monitoring');
GO

/* ---------------------------------------------------------------------------
   Reference dimension: groundwater monitoring wells (drillholes)
   --------------------------------------------------------------------------- */
IF OBJECT_ID('monitoring.monitoring_wells', 'U') IS NOT NULL
    DROP TABLE monitoring.monitoring_wells;
GO

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
IF OBJECT_ID('monitoring.water_level_readings', 'U') IS NOT NULL
    DROP TABLE monitoring.water_level_readings;
GO

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
IF OBJECT_ID('monitoring.rainfall_observations', 'U') IS NOT NULL
    DROP TABLE monitoring.rainfall_observations;
GO

CREATE TABLE monitoring.rainfall_observations (
    obs_id            BIGINT        IDENTITY(1,1) PRIMARY KEY,
    station_id        NVARCHAR(20)  NOT NULL,
    station_name      NVARCHAR(120) NULL,
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
IF OBJECT_ID('monitoring.surface_water_readings', 'U') IS NOT NULL
    DROP TABLE monitoring.surface_water_readings;
GO

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
IF OBJECT_ID('monitoring.anomaly_events', 'U') IS NOT NULL
    DROP TABLE monitoring.anomaly_events;
GO

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
