# Power Query and M transformation layer

## Purpose

Power Query prepares source data before it reaches the Power BI semantic model.
DAX measures handle reporting calculations after the model is loaded. Keeping
that boundary clear stops the same logic being written twice in two languages
and then drifting apart.

## The two report artefacts

The repository contains two Power BI artefacts, and they do not work the same
way. Almost every support question starts with working out which one the user is
looking at.

| | Water Security Phase 2 | Operational four page report |
|---|---|---|
| Source in git | [`powerbi/WaterSecurityPhase2/`](../powerbi/WaterSecurityPhase2/) as PBIP | not in git, screenshots only |
| Storage mode | Import | DirectQuery, with an imported `Date` dimension |
| Data source | sample rows written directly into the M expression | `monitoring` tables in Azure SQL |
| Tables | `vw_water_security_risk`, `vw_licence_compliance` | four `monitoring` tables plus `Date` |
| Screenshot | `00_water_security.png` | `01_data_model.png`, `03_groundwater_trend.png`, `04_advanced_analytics.png` |

## Water Security Phase 2 (PBIP)

This model is offline by design. Both partitions declare `mode: import` and
build their table from an inline `#table` literal rather than from a connection:

```
partition vw_water_security_risk = m
    mode: import
    source =
        let
            Source = #table(
                type table [ snapshot_date = date, location_name = text, ... ],
                { {#date(2026,6,7), "Virginia NAP-01", ... } }
            )
        in
            Source
```

The rows are the same demonstration scenario that
[`analytics/water_security_risk.py`](../analytics/water_security_risk.py)
produces. Embedding them means the PBIP opens and renders for anyone who clones
the repository, with no Azure subscription, no database and no credentials.
For a portfolio that is the point: a reviewer can read the semantic model as
source and open the report without being granted access to anything.

The trade off is that this model proves the report layout and the DAX, and it
does not prove the SQL connection. Do not describe it as DirectQuery.

Model settings worth knowing: the culture is `en-AU`, time intelligence is
disabled, and there is one bidirectional relationship from
`vw_water_security_risk.licence_id` to `vw_licence_compliance.licence_id`.

Both query names match the SQL view names, which is what lets the measures in
`vw_water_security_risk.tmdl` address their table as
`vw_water_security_risk[risk_status]`. Renaming either query breaks every
measure at once, so treat the names as part of the contract.

### Column alignment before repointing at SQL

Swapping the `#table` literal for `Sql.Database` will not work as a
straight substitution. The embedded columns were named independently of the
views in [`sql/02_indexes_and_views.sql`](../sql/02_indexes_and_views.sql), and
several do not line up.

| PBIP column | `monitoring.vw_water_security_risk` |
|---|---|
| `snapshot_date` | `risk_snapshot_date` |
| not present | `source_dh_no`, `annual_allocation_ml`, `extraction_ytd_ml`, `projected_year_end_extraction_ml`, `latest_anomaly_type`, `latest_anomaly_severity`, `risk_drivers` |

| PBIP column | `monitoring.vw_licence_compliance` |
|---|---|
| `extracted_ytd_ml` | `extraction_to_date_ml` |
| `licence_year` | `licence_start_date` and `licence_end_date` |
| `projected_year_end_ml` | not present in this view |
| not present | `compliance_limit_pct` |

The remaining columns do match, including `risk_score`, `risk_status`,
`allocation_used_pct`, `projected_allocation_pct`, `forecast_change_mbgl` and
`data_completeness_30d_pct`, so the measures themselves would survive the
change. Renaming the seven columns above, in the view or in an applied step, is
the work involved in putting this model on live data.

## Operational four page report

This report reads Azure SQL over DirectQuery, so its transformation layer is
deliberately thin. Anything expensive belongs in a SQL view where it can be
indexed, tested and folded, rather than in an applied step.

It is a composite model. The four `monitoring` tables are DirectQuery and the
`Date` dimension is imported, because `sql/` does not define a date dimension.
A scheduled refresh in the workspace therefore only rebuilds `Date`, and it
never moves groundwater data.

Query names follow the SQL Server navigator default, which combines the schema
name and the object name. The list below is read from `01_data_model.png`, since
this report is not in source control.

| Query | Source | Storage mode |
|---|---|---|
| `monitoring water_level_readings` | `monitoring.water_level_readings` | DirectQuery |
| `monitoring rainfall_observations` | `monitoring.rainfall_observations` | DirectQuery |
| `monitoring monitoring_wells` | `monitoring.monitoring_wells` | DirectQuery |
| `monitoring anomaly_events` | `monitoring.anomaly_events` | DirectQuery |
| `Date` | generated in M | Import |

`monitoring monitoring_wells` sits on the one side of `well_id` for both
`monitoring water_level_readings` and `monitoring anomaly_events`. `Date` joins
`reading_date` on the readings and `obs_date` on the rainfall observations. Its
relationship to `monitoring anomaly_events` on `event_date` is inactive, so a
measure that needs to slice anomalies by the calendar has to invoke it with
`USERELATIONSHIP`.

## Transformation principles

Apply explicit data types before joins and calculations. An implicit type is a
future bug that surfaces on a visual rather than in a deployment.

Reject or isolate invalid dates and numeric values instead of coercing them. A
quietly coerced null becomes a gap in the completeness metric, and completeness
feeds the bore risk score in
[`analytics/water_security_risk.py`](../analytics/water_security_risk.py).

Preserve the source keys used for incremental loading. Dropping a key during a
tidy up is one of the easier ways to break a future incremental refresh.

Keep business measures in DAX where they depend on filter context, and keep
reusable cleansing and shaping in Power Query. If a calculation gives a
different answer depending on what the user has sliced, it belongs in DAX.

Validate row counts and null rates after each major transformation, and record
the expected values so the next person can tell a regression from normal
variation.

## Query folding

This section applies to the four page report only. Folding is not a concept in
the Phase 2 PBIP, because there is no database for a query to fold back to.

Filters, projections and aggregations should fold back to Azure SQL wherever
possible. In DirectQuery this is not a nicety. A step that breaks folding forces
evaluation outside the database and shows up directly as a slow visual.

Check folding by viewing the native query on the final applied step. If the
option is greyed out, folding stopped somewhere earlier, and the step where it
stopped is the one to review. The usual causes are custom columns written in M,
index columns, and merges against a source that cannot fold.

Where a step genuinely cannot fold, push the logic into the SQL view instead and
let the query read a prepared column.

## Failure checks

When a query fails:

1. Establish which artefact it is. The Phase 2 PBIP cannot fail on a connection,
   so a connection error there means someone has repointed it.
2. Check the failing applied step and read the error against that step rather
   than against the query as a whole.
3. Confirm the source schema has not changed. A renamed or dropped column in
   `monitoring.*` is the most common cause, and a renamed reporting view is the
   most disruptive one.
4. Compare source and output row counts.
5. Check data types and null values at the step before the failure.
6. Confirm query folding was preserved, since a change that breaks folding can
   turn a working query into a timeout without ever raising an error.
7. Record the change and update the relevant test or runbook entry.

Steps 2 to 6 also apply when the query succeeds but the numbers look wrong,
which is the harder version of the same problem.

## Known gaps

The Phase 2 PBIP holds its data inline, so the figures in it only change when
somebody regenerates the literal. It will not pick up a new snapshot written by
`analytics/water_security_risk.py` on its own.

Seven column names diverge between the PBIP and the SQL views, listed above.

The four page report is not in source control. Its layout, queries and
relationships exist only as screenshots and in the workspace, which is why the
table above is transcribed rather than read from a definition file. Bringing it
across to PBIP would put both reports under the same review path.

## Related documents

- [`docs/runbook.md`](runbook.md), specifically INC-01 for connection and visual
  query failures and INC-03 for DirectQuery performance
- [`sql/02_indexes_and_views.sql`](../sql/02_indexes_and_views.sql) for the view
  definitions the Phase 2 model is named after
- [`powerbi/phase2_water_security_measures.dax`](../powerbi/phase2_water_security_measures.dax)
  for the measures as plain text, mirroring those in the PBIP semantic model
