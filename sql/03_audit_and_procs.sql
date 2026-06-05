/* ===========================================================================
   Pipeline audit table + helper stored procedures.

   The audit trail supports the "trusted, high-quality data" requirement: every
   pipeline run that fails is recorded so data gaps are explainable and
   defensible during compliance reporting.
   =========================================================================== */

IF OBJECT_ID('monitoring.pipeline_audit', 'U') IS NULL
BEGIN
    CREATE TABLE monitoring.pipeline_audit (
        audit_id      BIGINT       IDENTITY(1,1) PRIMARY KEY,
        pipeline_name NVARCHAR(120) NOT NULL,
        run_id        NVARCHAR(80)  NULL,
        status        NVARCHAR(20)  NOT NULL,   -- Failed | Succeeded
        logged_at     DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME()
    );
END
GO

CREATE OR ALTER PROCEDURE monitoring.usp_log_pipeline_failure
    @pipeline_name NVARCHAR(120),
    @run_id        NVARCHAR(80)
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO monitoring.pipeline_audit (pipeline_name, run_id, status)
    VALUES (@pipeline_name, @run_id, 'Failed');
END
GO

/* Returns CRITICAL anomaly events that have not yet been alerted on.
   The Logic App calls this to decide whether to send an alert, then marks the
   rows as sent. */
CREATE OR ALTER PROCEDURE monitoring.usp_get_unsent_critical_events
AS
BEGIN
    SET NOCOUNT ON;
    SELECT  e.event_id,
            e.well_id,
            w.location_name,
            w.management_area,
            e.event_date,
            e.anomaly_type,
            e.severity,
            e.actual_value,
            e.detail
    FROM    monitoring.anomaly_events e
    JOIN    monitoring.monitoring_wells w ON w.well_id = e.well_id
    WHERE   e.severity = 'CRITICAL'
      AND   e.alert_sent = 0
    ORDER BY e.event_date DESC;
END
GO

CREATE OR ALTER PROCEDURE monitoring.usp_mark_event_alerted
    @event_id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE monitoring.anomaly_events
    SET    alert_sent = 1
    WHERE  event_id = @event_id;
END
GO
