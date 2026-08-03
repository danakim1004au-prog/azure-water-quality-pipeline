# AquaSentry support runbook (Level 2 and Level 3)

Operational runbook for the AquaSentry groundwater monitoring platform: daily
ETL into Azure SQL, a timer triggered anomaly detection Function, and a Power BI
report used by management area reviewers.

It is written for the engineer who picks up the ticket rather than for the
engineer who built the system. Each playbook states the symptom as the customer
reports it, the checks in the order they should be run, the fix, and what to
write down afterwards.

For the transformation layer behind the report, see
[`docs/power-query.md`](power-query.md).

> **Scope note.** This is a portfolio implementation. Thresholds, response
> targets and escalation contacts below describe how the platform would be
> supported in a managed service context. They are not contractual.

---

## 1. Service map

Know what depends on what before touching anything.

```
Public data sources  ──►  ADF pipeline (05:00 UTC)  ──►  Azure SQL  ──►  Power BI (DirectQuery)
                                                            ▲
                          Azure Function (06:00 UTC)  ───────┘
                          rule-based detectors → anomaly_events
                                    │
                                    └──►  Logic App  ──►  Email / Teams
```

| Component | Resource | Fails how | Blast radius |
|---|---|---|---|
| Ingestion | Data Factory pipeline, 05:00 UTC trigger | Source API change, auth expiry, staging config | All downstream data stale |
| Storage | Azure SQL Database, Basic tier | DTU saturation, blocking, schema drift | Report and Function both down |
| Analytics | Function App, 06:00 UTC timer | Managed identity or Key Vault access, unhandled exception | No new `anomaly_events`, report shows stale risk status |
| Secrets | Key Vault (SQL connection string) | Access policy or RBAC change, secret rotation | Function cannot connect |
| Reporting | Power BI, four page report, DirectQuery to Azure SQL | Data source credentials, gateway, changed view, slow query | User visible, usually the first thing reported |
| Reporting | Power BI, Water Security Phase 2 PBIP, import with embedded rows | Stale figures, because nothing reconnects it to the database | Renders regardless of platform health, so it can look healthy while everything else is down |
| Alerting | Logic App | Connector authorisation, throttling | Silent failure, alerts stop and nobody notices |

Azure SQL is the single point of failure for everything except the Phase 2
report. If it is degraded, treat every symptom below as a downstream effect
until you have proven otherwise.

The exception is worth stating plainly, because it inverts the usual triage. The
Water Security Phase 2 PBIP carries its sample rows inside the M expression, so
it keeps rendering when the database is unreachable. A user looking at that page
cannot tell you whether the platform is healthy, and "the dashboard looks fine"
from them is not evidence. See [`docs/power-query.md`](power-query.md).

---

## 2. Priority matrix

| Priority | Definition | Response | Resolution target |
|---|---|---|---|
| **P1** | Report unavailable to all users, or data loss or corruption in `monitoring.*` | 15 min | 4 h |
| **P2** | Report available but data stale beyond 24 h, or anomaly detection not running | 1 h | 1 business day |
| **P3** | Single visual, single bore or single detector affected, workaround exists | 4 h | 3 business days |
| **P4** | Cosmetic, documentation, or service request (access, new measure, new bore) | 1 business day | Next release |

Raise a P3 to P2 if it affects a licence compliance figure. Those numbers are
used for regulatory reporting, and a wrong number is worse than a missing one.

---

## 3. First response: the 10 minute triage

Run these four checks before opening any playbook. They separate "the report is
broken" from "the data is stale" from "Azure is having a bad day". Most tickets
are resolved or correctly routed by check 3.

**1. Is the platform up?**
Azure Portal, Service Health, filtered to the subscription and `australiaeast`.
An active advisory turns the ticket from a defect into a vendor incident, and
that changes what you tell the customer.

**2. How stale is the data?**

```sql
SELECT  MAX(reading_date)                                                AS latest_reading,
        DATEDIFF(day, MAX(reading_date), CAST(SYSUTCDATETIME() AS date)) AS days_behind,
        COUNT(DISTINCT well_id)                                          AS bores_reporting
FROM    monitoring.water_level_readings;
```

`reading_date` is a `DATE` column, so the gap is measured in whole days rather
than hours. A `days_behind` of 0 or 1 means ingestion is healthy and the problem
sits downstream. A value of 2 or more means go to INC-02. Fewer than 6 bores
reporting means a partial load, so go to INC-04.

**3. Did the detector Function run?**

```sql
SELECT  MAX(detected_at) AS last_detector_run,
        COUNT(*)         AS events_last_24h
FROM    monitoring.anomaly_events
WHERE   detected_at >= DATEADD(day, -1, SYSUTCDATETIME());
```

Zero events is not automatically a fault, since a quiet day is a valid outcome.
What matters is a missing run timestamp. Confirm in Function App, Monitor,
Invocations before you conclude anything.

**4. Can the report reach the database?**
Open the report and load a single visual. If the visual fails while the queries
above succeed from the query editor, the fault sits in the Power BI connection
layer rather than in SQL. Go to INC-01.

Record the outcome of all four checks in the ticket even when they pass. The
next engineer needs to know what has already been ruled out.

---

## 4. Incident playbooks

### Core support scenarios

This runbook covers the three highest value support scenarios for the role:

1. Power BI report connection and DirectQuery timeout
2. Azure Data Factory ETL interruption or stale data
3. Azure SQL, Function App and downstream reporting failures

Two notes on terminology before you start.

First, establish which report the user has open. The four page report reads
Azure SQL live. The Water Security Phase 2 page does not read it at all, so a
complaint about stale numbers there is never an ingestion fault.

Second, the monitoring tables are read over DirectQuery, so there is no
scheduled dataset refresh that moves groundwater data. Three different things
get called "refresh" by users, and they need different playbooks:

| What the user says | What it actually is | Where to go |
|---|---|---|
| "It won't refresh" and the visual errors | Visual query or connection failure | INC-01 |
| "It's slow to refresh" and the visual eventually loads or times out | DirectQuery query performance | INC-03 |
| "The scheduled refresh failed" | The four page report is composite, so only the imported `Date` dimension refreshes on a schedule and bore readings cannot go stale from it | Confirm the staleness separately with check 2 in section 3, then treat the refresh failure on its own |
| "The risk figures haven't moved in weeks" on the Phase 2 page | Expected. That model holds its rows inline and does not reload from SQL | Explain the design, then raise it as a change request rather than an incident |

---

### INC-01: Power BI report connection or visual query failure

*Typical report:* "The dashboard is blank" or "one page shows an error, the rest
are fine."

This playbook covers the four page DirectQuery report. The Phase 2 PBIP has no
data source, so it cannot produce a connection error. If someone reports one
there, the model has been repointed at SQL and the column mapping in
[`docs/power-query.md`](power-query.md) is the first thing to check.

**Diagnose**

1. Reproduce it. Note which page and which visual. A single failing visual
   points at one view. A whole page usually points at the connection.
2. Run the view the visual depends on directly:

   ```sql
   SELECT TOP 50 * FROM monitoring.vw_water_security_risk;
   SELECT TOP 50 * FROM monitoring.vw_licence_compliance;
   ```

3. If the view errors, the fault is in SQL. A column was renamed, a base table
   was dropped, or a dependent object is broken. Check for schema drift:

   ```sql
   SELECT  o.name AS view_name, d.referenced_entity_name
   FROM    sys.sql_expression_dependencies d
   JOIN    sys.objects o ON o.object_id = d.referencing_id
   WHERE   o.name IN ('vw_water_security_risk', 'vw_licence_compliance')
     AND   d.referenced_id IS NULL;   -- unresolved reference means broken dependency
   ```

4. If the view returns rows, the fault sits in the connection or the
   transformation layer. Check the data source credentials, the gateway status
   if the report is published through one, and then the applied steps in
   [`docs/power-query.md`](power-query.md). A renamed source column often fails
   at a Power Query step rather than in SQL.
5. If a single card or table is broken while the rest of the page renders, check
   the measure rather than the connection. Every measure in
   `WaterSecurityPhase2.SemanticModel/definition/tables/vw_water_security_risk.tmdl`
   addresses its own table by name, so renaming that query breaks all of them at
   once. The same measures are kept as plain text in
   [`powerbi/phase2_water_security_measures.dax`](../powerbi/phase2_water_security_measures.dax).

**Fix**

Broken dependency: redeploy the affected object from `sql/` and re-run the view.
Do not hand patch the view in production. Change the file, deploy it, and record
the change under section 6.

Credential expiry: re-enter the data source credentials and note the new expiry
date in the ticket so it can be pre-empted next time.

Tell the customer whether the number they are looking at is wrong or simply
missing. Those need different actions on their side.

**Recovery validation**

- Confirm the failed component is healthy.
- Confirm downstream data has changed as expected.
- Re-run the four triage checks in section 3.
- Record the root cause, recovery time and evidence in the ticket.

---

### INC-02: Daily ADF pipeline failed or did not run

*Typical report:* raised by monitoring, or by a user noticing yesterday's date
on the overview page.

**Diagnose**

1. Data Factory, Monitor, Pipeline runs. Read the activity error rather than the
   pipeline error. The pipeline error is almost always a generic wrapper.
2. Classify what you find:

   **Source API.** A 4xx or 5xx from the groundwater, climate or surface water
   endpoint. Not our defect. Confirm with a manual call, then decide whether to
   wait or backfill.

   **Authentication.** An expired service principal secret, or a Key Vault
   reference that no longer resolves.

   **Sink.** Azure SQL rejected the write: constraint violation, type mismatch,
   or the database was unavailable. A constraint violation on `uq_reading` has
   its own playbook in INC-04.

   **Trigger.** The 05:00 UTC trigger is stopped. Check the trigger state before
   you assume a failure, because a pipeline that never started produces no error
   at all.

**Fix**

Transient source or sink error: re-run the failed activity from the point of
failure. The loaders are idempotent, so a re-run will not duplicate rows.

Source contract change: this is a code change rather than a re-run. Raise a
problem record, patch the ingestion client, and deploy it through section 6.

Missed window: trigger a backfill for the affected date range and verify with
the staleness query in section 3.

Always confirm the data actually landed. A green pipeline run that loaded zero
rows is the most common false resolution on this platform:

```sql
SELECT   reading_date,
         COUNT(*) AS rows_loaded
FROM     monitoring.water_level_readings
WHERE    reading_date >= DATEADD(day, -7, CAST(SYSUTCDATETIME() AS date))
GROUP BY reading_date
ORDER BY reading_date DESC;
```

**Recovery validation**

- Confirm the failed component is healthy.
- Confirm downstream data has changed as expected.
- Re-run the four triage checks in section 3.
- Record the root cause, recovery time and evidence in the ticket.

---

### INC-03: DirectQuery timeout or slow visuals

*Typical report:* "It takes forever to load" or "the map spins and then gives
up."

DirectQuery means every visual is a live query against Azure SQL. Treat slow
visuals as a SQL problem until proven otherwise.

Confirm the page first. The Phase 2 report renders from memory and is not
affected by database load, so a slow visual there is a rendering or client
issue rather than a query one.

**Diagnose**

1. Identify the expensive statements:

   ```sql
   SELECT TOP 20
          qt.query_sql_text,
          rs.count_executions,
          rs.avg_duration / 1000.0  AS avg_duration_ms,
          rs.avg_logical_io_reads
   FROM   sys.query_store_runtime_stats      rs
   JOIN   sys.query_store_plan               p  ON p.plan_id  = rs.plan_id
   JOIN   sys.query_store_query              q  ON q.query_id = p.query_id
   JOIN   sys.query_store_query_text         qt ON qt.query_text_id = q.query_text_id
   WHERE  rs.last_execution_time >= DATEADD(hour, -6, SYSUTCDATETIME())
   ORDER  BY rs.avg_duration DESC;
   ```

   `avg_duration` is recorded in microseconds, which is why it is divided by
   1000 to read as milliseconds.

2. Check whether the database is simply saturated:

   ```sql
   SELECT TOP 20 end_time, avg_cpu_percent, avg_data_io_percent, avg_log_write_percent
   FROM   sys.dm_db_resource_stats
   ORDER  BY end_time DESC;
   ```

   The database is provisioned at the Basic tier, 5 DTU, set by
   `sql_database_sku` in [`infra/variables.tf`](../infra/variables.tf). At that
   size a sustained `avg_cpu_percent` above 80 is a capacity finding rather than
   a query defect. Say that explicitly in the ticket, because the remediation is
   a service tier decision the customer has to make.

3. Check for blocking if the slowdown comes and goes:

   ```sql
   SELECT  r.session_id, r.status, r.wait_type, r.wait_time,
           r.blocking_session_id, t.text
   FROM    sys.dm_exec_requests r
   CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
   WHERE   r.session_id <> @@SPID
     AND   r.blocking_session_id <> 0;
   ```

   A daily 05:00 to 06:15 UTC window of blocking is expected. The ETL load and
   the detector Function overlap with early shift report use.

**Fix**

Missing index on a fact table filter column: add it through a deployed
migration, never ad hoc. The existing report filter indexes are defined in
[`sql/02_indexes_and_views.sql`](../sql/02_indexes_and_views.sql), so check
there before adding a new one.

Repeated identical aggregate: materialise it into
`monitoring.water_security_risk_snapshots` instead of computing it once per
visual.

Genuinely large historical slicer range: recommend an Import mode or aggregated
page for trend analysis, and keep DirectQuery for current status. Going that way
puts fact data behind a scheduled refresh, which is a new failure mode this
runbook does not yet cover.

Known good baseline: the four page report renders in under 5 seconds against the
demonstration dataset outside the 05:00 to 06:15 UTC load window. Quote that
baseline when the customer asks whether what they are seeing is normal.

**Recovery validation**

- Confirm the failed component is healthy.
- Confirm downstream data has changed as expected.
- Re-run the four triage checks in section 3.
- Record the root cause, recovery time and evidence in the ticket.

---

### INC-04: Missing or implausible readings, and rejected duplicates

*Typical report:* "This bore has gaps for the last fortnight" or "the level
jumped 40 m overnight."

**Diagnose**

Start by reading the constraint, because it determines which of these symptoms
is even possible. `monitoring.water_level_readings` carries
`CONSTRAINT uq_reading UNIQUE (well_id, reading_date)`, defined in
[`sql/01_schema.sql`](../sql/01_schema.sql). A second reading for the same bore
and date cannot land. It is rejected at the sink, which means a duplicate in the
source surfaces as a failed ADF activity rather than as a double row in the
report.

So if a user reports seeing two readings for one day, they are looking at
something else: two bores in the same management area, an unfiltered visual, or
a measure without the expected filter context. Check the visual before you check
the data.

Run this as an assertion rather than as a search. It should always return zero
rows:

```sql
-- uq_reading should keep this empty
SELECT   well_id, reading_date, COUNT(*) AS duplicate_rows
FROM     monitoring.water_level_readings
GROUP BY well_id, reading_date
HAVING   COUNT(*) > 1;
```

A row here means the unique constraint is missing or was disabled, most likely
by a partial schema deployment or a restore. That is a schema fault, so redeploy
[`sql/01_schema.sql`](../sql/01_schema.sql) and treat it as P1 under section 2.

For the gaps, check completeness by bore over the last 30 days:

```sql
SELECT    w.well_id,
          COUNT(r.reading_date)                     AS readings,
          100.0 * COUNT(r.reading_date) / 30        AS pct_complete
FROM      monitoring.monitoring_wells w
LEFT JOIN monitoring.water_level_readings r
       ON r.well_id = w.well_id
      AND r.reading_date >= DATEADD(day, -30, CAST(SYSUTCDATETIME() AS date))
GROUP BY  w.well_id
ORDER BY  pct_complete;
```

For the jump, look at the day on day movement:

```sql
WITH readings AS (
    SELECT
        well_id,
        reading_date,
        water_level_mbgl,
        water_level_mbgl
          - LAG(water_level_mbgl)
            OVER (
                PARTITION BY well_id
                ORDER BY reading_date
            ) AS delta_m
    FROM monitoring.water_level_readings
    WHERE reading_date >= DATEADD(day, -30, CAST(SYSUTCDATETIME() AS date))
)
SELECT
    well_id,
    reading_date,
    water_level_mbgl,
    delta_m
FROM readings
ORDER BY ABS(delta_m) DESC;
```

**Fix**

A rejected duplicate at the sink usually means the source changed its record
identifier, so the loader no longer recognises a record it has already written.
Patch the ingestion client rather than relaxing the constraint. Dropping
`uq_reading` to make the pipeline go green removes the only thing preventing
double counted extraction figures.

An implausible value that is genuinely present in the source is a data quality
finding rather than a defect. Flag it to the customer instead of quietly
correcting it, and note that completeness feeds the risk score, so suppressing
rows will change a bore's status.

**Recovery validation**

- Confirm the failed component is healthy.
- Confirm downstream data has changed as expected.
- Re-run the four triage checks in section 3.
- Record the root cause, recovery time and evidence in the ticket.

---

### INC-05: Detector Function not producing events

*Typical report:* "The risk status hasn't changed in days."

**Diagnose**

1. Function App, Monitor, Invocations. There are three cases to tell apart.

   **No invocation.** The timer is disabled or the app is stopped. Check
   `AzureWebJobs.<FunctionName>.Disabled` and the running state of the app.

   **Invocation failed.** Read the exception. The most common cause is a
   connection failure, covered in step 2.

   **Invocation succeeded with zero events.** Legitimate. Verify that source
   data exists for the window at all, because a detector cannot fire on data
   that never arrived. Re-run the INC-02 checks.

2. Connection failures here are almost always identity rather than network.
   Confirm the system assigned identity is still enabled on the Function App,
   confirm that identity still has `get` on Key Vault secrets, and confirm the
   SQL connection string secret has not been rotated to a value the database no
   longer accepts.

**Fix**

Restore the Key Vault access assignment or re-enable the identity, then run the
function manually and confirm rows appear in `monitoring.anomaly_events`.

If a detector produces no events because its threshold was mis-calibrated, that
is a problem record rather than an incident. Thresholds are documented in
[`docs/anomaly_methodology.md`](anomaly_methodology.md), and changing one is a
release rather than a fix.

**Recovery validation**

- Confirm the failed component is healthy.
- Confirm downstream data has changed as expected.
- Re-run the four triage checks in section 3.
- Record the root cause, recovery time and evidence in the ticket.

---

### INC-06: Alerts stopped arriving

*Typical report:* usually none. That is what makes this one worth checking on a
schedule rather than waiting for a ticket.

**Diagnose**

1. Logic App, Runs history. No runs at all means the trigger is not firing.
   Failed runs mean the connector is the problem.
2. Confirm there was actually something to alert on:

   ```sql
   SELECT COUNT(*) FROM monitoring.anomaly_events
   WHERE  severity = 'CRITICAL'
     AND  detected_at >= DATEADD(day, -7, SYSUTCDATETIME());
   ```

   `severity` holds `INFO`, `WARNING` or `CRITICAL`, so the filter above is
   case sensitive against the values the detectors write. Critical events with
   no matching Logic App runs is a real fault. Zero critical events is a quiet
   week.

**Fix**

Reauthorise the email or Teams connector. Then add a heartbeat check that raises
a ticket when no Logic App run has occurred in seven days, so that the next
silent failure does not depend on somebody noticing.

**Recovery validation**

- Confirm the failed component is healthy.
- Confirm downstream data has changed as expected.
- Re-run the four triage checks in section 3.
- Record the root cause, recovery time and evidence in the ticket.

---

## 5. Known errors

Reproduced faults with a confirmed root cause. Check here before investigating
from scratch.

| ID | Symptom | Root cause | Resolution | Status |
|---|---|---|---|---|
| KE-01 | Function connects locally, fails in Azure with a credential error | Managed identity lost its Key Vault secret permission after an RBAC change | Reassign `get` on secrets to the Function's system assigned identity | Permanent fix applied |
| KE-02 | Report renders slowly between 05:00 and 06:15 UTC only | ETL load and detector run overlap with early shift report use | Documented as expected behaviour, users advised of the window | Accepted, not fixed |
| KE-03 | Pipeline succeeds, no new rows | Source returned an empty payload and the loader treats empty as success | Verify with the row count query in INC-02, add a row count assertion to the pipeline | Workaround, permanent fix open |
| KE-04 | Licence compliance figures differ from the customer's own records | Repo values are demonstration scenarios rather than regulatory records | Clarify data provenance with the requester before investigating | By design |
| KE-05 | User reports a failed scheduled refresh and assumes the bore data is stale | The four page report is composite and only the imported `Date` dimension refreshes on a schedule | Check staleness with the query in section 3, then handle the refresh failure separately | By design, see INC-01 |
| KE-06 | Phase 2 dashboard renders normally during a confirmed Azure SQL outage | That model carries its sample rows inside the M expression and never queries the database | Do not treat it as evidence of platform health. Triage from section 3 instead | By design |
| KE-07 | Phase 2 figures do not update after a new risk snapshot is loaded | Same cause as KE-06. The model does not read `monitoring.water_security_risk_snapshots` | Regenerate the inline rows, or repoint the model at the view and align the seven column names listed in `docs/power-query.md` | Open |

---

## 6. Change and release

No production change reaches Azure SQL or the report outside this path.

The two reports sit at different levels of control, so read the row that matches
the artefact you are changing. The Phase 2 report is a PBIP under
`powerbi/WaterSecurityPhase2/`, so its semantic model and page layout are plain
text and go through the normal pull request path. The four page report is not in
git, and its version history lives in the Power BI workspace.

| Change type | Path | Approval | Rollback |
|---|---|---|---|
| Schema, view, stored procedure | PR, CI (lint, tests, `terraform validate`), merge, deploy | Peer review | Redeploy the previous version from `sql/` |
| Phase 2 measure, model or page layout | Edit the PBIP in Desktop, commit the changed `.tmdl` or `visual.json`, PR, publish | Peer review of the diff | Revert the PR and republish |
| Phase 2 embedded sample rows | Regenerate from `analytics/water_security_risk.py`, replace the `#table` literal, PR | Peer review, and state which snapshot date the rows came from | Revert the PR |
| Four page report layout or visual | Edit in Desktop, publish, commit the updated screenshot in the same PR | Peer review | Restore the previous version from the workspace version history |
| Power Query applied steps | Edit in the Power Query Editor, publish, update `docs/power-query.md` in the same PR | Peer review | Revert the PR for the PBIP, or the workspace version history for the four page report |
| Infrastructure | Terraform plan reviewed on the PR, then apply | Peer review of the plan output | `terraform apply` at the previous commit |
| Detector threshold | Update `docs/anomaly_methodology.md` and the detector in the same PR | Customer sign off, because thresholds change what gets alerted | Revert the PR |
| Emergency fix | Apply, then raise the PR within one business day | Retrospective | As above |

Renaming a Power BI query counts as a breaking change, not a tidy up. The
measures address their tables by name, so put it through the same review as a
schema change.

Before deploying, record four things: what is changing, what breaks if it is
wrong, how you will know within 15 minutes, and the exact rollback command. A
change with no stated rollback does not get deployed.

After deploying, re-run the four triage checks in section 3. A successful
deployment tells you the change applied, and the triage checks tell you the
platform still works. You need both before you close the change.

---

## 7. Escalation

| Trigger | Escalate to | Bring with you |
|---|---|---|
| Azure platform incident confirmed in Service Health | Vendor, and track the advisory | Advisory ID, affected resources, customer impact statement |
| Data loss or corruption in `monitoring.*` | Platform owner, immediately | Affected tables, row counts, last known good timestamp, restore point |
| Source data contract change | Data owner | Endpoint, old and new response shape, which fields broke |
| Licence or extraction figures disputed | Customer data owner | Provenance note, since these are demonstration values (KE-04) |
| P1 unresolved at half the resolution target | Service delivery lead | A timeline of what has been ruled out |

Escalate with findings rather than with symptoms. "The report is down" gives the
next person nothing to work with. "The report is down, SQL is healthy, the
pipeline last succeeded at 05:00, and the risk view has an unresolved dependency
after last night's deploy" tells them where to start.

---

## 8. Closing a ticket

Every resolved incident produces three things.

A root cause in one sentence the customer can read. Not "a schema issue", but "a
column renamed in yesterday's release broke the view the risk page reads".

A permanent fix decision: fixed, worked around, or accepted. If it is a
workaround, raise the problem record and reference it here.

A runbook update. If this document did not contain the answer, add it. If it
contained a wrong answer, correct it. The document is only worth reading if the
people using it keep editing it.

Recurring incidents of the same type get promoted to a knowledge article so the
service desk can resolve them at Level 1 without escalating.
