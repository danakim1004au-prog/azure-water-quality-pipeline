# Power BI Dashboard — Build Guide

This guide is everything needed to build the four-page report by hand in Power
BI Desktop. It covers two connection paths (live Azure SQL, or the offline
sample CSVs), the data model, the DAX measures, and a visual-by-visual layout
for each page, including the colour scheme.

Save the finished file as `powerbi/water_monitoring.pbix` and drop page
screenshots into `powerbi/screenshots/` so they can be embedded in the README.

---

## 1. Connect to data

### Option A — Live Azure SQL (DirectQuery)

This is the path to demonstrate for the portfolio: the report queries the cloud
warehouse live.

1. **Home → Get Data → Azure → Azure SQL database.**
2. Server: the `sql_server_fqdn` Terraform output
   (e.g. `sql-waterqlty-dev-ab12c.database.windows.net`).
   Database: `sqldb-water-monitoring`.
3. **Data Connectivity mode: DirectQuery.** (Keeps the report live and avoids
   loading all rows into the .pbix.)
4. Sign in with the SQL admin credentials (or an Entra ID user you granted).
5. Select these tables/views:
   - `monitoring.monitoring_wells`
   - `monitoring.water_level_readings`
   - `monitoring.rainfall_observations`
   - `monitoring.anomaly_events`
   - `monitoring.vw_well_status`
   - `monitoring.vw_rainfall_recharge`

> Make sure your client IP is allowed by the SQL firewall — set
> `allowed_client_ip` in `infra/terraform.tfvars` to the output of
> `curl -s https://api.ipify.org`.

### Option B — Offline sample CSVs (for fast iteration / no Azure)

1. Run `python scripts/generate_sample_data.py` then
   `python scripts/run_detectors_offline.py`.
2. **Get Data → Text/CSV** and load the four files from `sample_data/`:
   `monitoring_wells.csv`, `water_level_readings.csv`,
   `rainfall_observations.csv`, `anomaly_events.csv`.
3. Build the same model and visuals below. (The `vw_*` views become small
   calculated tables — DAX equivalents are noted where needed.)

---

## 2. Data model (relationships)

Create a star-ish model with `monitoring_wells` and a date table as
dimensions.

```
            ┌──────────────────────┐
            │      Date (dim)      │   <- mark as date table
            └──────────┬───────────┘
                       │ (Date → reading_date / obs_date / event_date)
   ┌───────────────────┼─────────────────────┐
   │                   │                     │
water_level_readings  rainfall_observations  anomaly_events
   │                                          │
   │   well_id (many) ──────► well_id (1)     │
   └──────────────► monitoring_wells ◄────────┘
```

Relationships:

| From (many)                          | To (one)            | Key            |
|--------------------------------------|---------------------|----------------|
| `water_level_readings[well_id]`      | `monitoring_wells`  | `well_id`      |
| `anomaly_events[well_id]`            | `monitoring_wells`  | `well_id`      |
| `water_level_readings[reading_date]` | `Date[Date]`        | date           |
| `rainfall_observations[obs_date]`    | `Date[Date]`        | date           |
| `anomaly_events[event_date]`         | `Date[Date]`        | date (inactive)|

Create the date table (New Table):

```DAX
Date =
ADDCOLUMNS (
    CALENDAR ( DATE ( 2019, 1, 1 ), TODAY () ),
    "Year", YEAR ( [Date] ),
    "Month", FORMAT ( [Date], "MMM" ),
    "MonthNo", MONTH ( [Date] ),
    "YearMonth", FORMAT ( [Date], "YYYY-MM" )
)
```

Mark it as a date table on the `Date` column.

---

## 3. DAX measures

Put these in a dedicated `_Measures` table (New Table → `_Measures = {BLANK()}`,
then add measures to it).

```DAX
-- Core
Latest Water Level =
CALCULATE (
    AVERAGE ( water_level_readings[water_level_mbgl] ),
    LASTNONBLANK ( 'Date'[Date], 1 )
)

Avg Water Level = AVERAGE ( water_level_readings[water_level_mbgl] )

-- 7-day moving average (water level)
WL 7d Moving Avg =
AVERAGEX (
    DATESINPERIOD ( 'Date'[Date], MAX ( 'Date'[Date] ), -7, DAY ),
    [Avg Water Level]
)

-- Rainfall
Total Rainfall = SUM ( rainfall_observations[rainfall_mm] )

Rainfall 7d Total =
CALCULATE (
    [Total Rainfall],
    DATESINPERIOD ( 'Date'[Date], MAX ( 'Date'[Date] ), -7, DAY )
)

-- Anomalies
Anomaly Count = COUNTROWS ( anomaly_events )

Critical Count =
CALCULATE ( [Anomaly Count], anomaly_events[severity] = "CRITICAL" )

Warning Count =
CALCULATE ( [Anomaly Count], anomaly_events[severity] = "WARNING" )

-- Status rank for conditional formatting on the map
Well Status =
VAR rank =
    CALCULATE (
        MAXX (
            anomaly_events,
            SWITCH ( anomaly_events[severity], "CRITICAL", 3, "WARNING", 2, 1 )
        ),
        DATESINPERIOD ( 'Date'[Date], MAX ( 'Date'[Date] ), -30, DAY )
    )
RETURN SWITCH ( rank, 3, "Critical", 2, "Watch", "Normal" )
```

(If you used **Option A**, `vw_well_status` already provides `status` and you
can skip the `Well Status` measure.)

---

## 4. Colour scheme

A calm, water-utility palette. Set these as the report theme
(View → Themes → Customize current theme).

| Use            | Hex       |
|----------------|-----------|
| Primary        | `#1B6CA8` (deep water blue) |
| Secondary      | `#5FB0D4` (sky blue)        |
| Background card | `#F4F8FB`                  |
| Normal status  | `#2E9E5B` (green)          |
| Watch status   | `#E8A33D` (amber)          |
| Critical status | `#C0392B` (red)           |
| Text           | `#1F2D3D`                  |

Use the same three status colours everywhere a status appears (map, table,
KPIs) so the eye learns them once.

---

## 5. Page-by-page layout

### Page 1 — Regional Overview

The "at a glance" page. The goal: a reviewer sees it and instantly recognises
an operational monitoring view.

- **Title bar** (top): "Regional Groundwater Overview" + a date slicer
  (relative date: last 90 days) on the right.
- **KPI cards** (row under title): `Wells Monitored`
  (`DISTINCTCOUNT(monitoring_wells[well_id])`), `Critical Count`,
  `Warning Count`, `Avg Water Level`. Colour the Critical card red.
- **Map** (large, centre-left): **Azure Map** visual.
  - Location: `latitude`, `longitude`.
  - Bubble colour: `Well Status` / `vw_well_status[status]` with the status
    colours above.
  - Bubble size: optional, `Latest Water Level`.
  - Tooltip: well name, management area, aquifer, last reading date, status.
- **Status breakdown** (right): a donut of well count by status, plus a
  slicer for `management_area`.

### Page 2 — Groundwater Trend Analysis

Drill into a single well's behaviour.

- **Slicers** (top): single-select `location_name`; date range.
- **Main line chart** (large): X = `Date`; Y = `Avg Water Level` and
  `WL 7d Moving Avg` as two lines.
  - Invert the Y-axis (larger mBGL = deeper) **or** add a note, so "down on the
    chart = deeper water table". Inverting reads most intuitively.
  - Add an **analytics trend line** (Analytics pane → Trend line) and a
    **forecast** (Analytics → Forecast, 90 days) to satisfy the "predictive"
    requirement.
- **Seasonality** (bottom): a matrix/heat of `Avg Water Level` by
  `Date[Year]` (rows) × `Date[Month]` (columns), conditional-formatted, to show
  the seasonal recharge cycle.
- **Salinity sparkline** (corner): line of `AVERAGE(tds_mg_per_l)` for the
  selected well.

### Page 3 — Rainfall–Recharge Correlation

The page that shows hydrological understanding.

- **Combo chart** (large): X = `Date`.
  - Columns: `Total Rainfall` (secondary Y-axis, mm).
  - Line: `Avg Water Level` (primary Y-axis, mBGL, inverted).
  - Visually, rainfall bars should be followed by the level line moving
    shallower — the recharge response.
- **Recharge-lag comparison** (right): a bar chart of average recharge lag
  (days) by `management_area`. If using Option A, compute lag in SQL; for a
  simpler version, show `Rainfall 7d Total` vs `Avg Water Level` scatter by
  area.
- **Slicer**: `management_area` (multi-select) and a season slicer.
- **Callout card**: count of `LowRechargeResponse` events in the period — ties
  this page to detector #2.

### Page 4 — Anomaly Event Log

The operational log.

- **Table/matrix** (large): `anomaly_events` columns — event date, well name,
  management area, anomaly type, severity, actual value, detail. Conditional-
  format the severity column with the status colours.
- **Slicers** (left): `severity`, `anomaly_type`, date range, `management_area`.
- **Heatmap** (top): matrix of `Anomaly Count` by `Date[YearMonth]` (rows) ×
  `anomaly_type` (columns), colour-scaled — shows when and what kind of issues
  cluster.
- **KPI row**: `Critical Count`, `Warning Count`, and a card for "most
  affected area" (`TOPN` by anomaly count).

---

## 6. Publishing & sharing (free)

- Power BI **Desktop** is free; build and screenshot here.
- To share without a Pro licence, use **File → Export → PowerPoint / PDF**, or
  publish to a workspace and share a read-only link where your tenant allows.
- For the README, export each page as PNG (Export → ... or screenshot) into
  `powerbi/screenshots/` named `01_overview.png` … `04_anomaly_log.png`.

---

## 7. Checklist before you call it done

- [ ] Map shows wells coloured by status with working tooltips.
- [ ] Trend page has a 7-day moving average **and** a forecast/trend line.
- [ ] Rainfall–recharge combo chart visibly shows the lag.
- [ ] Anomaly log filters by severity and type; heatmap renders.
- [ ] Status colours are identical across all pages.
- [ ] Four screenshots exported into `powerbi/screenshots/`.
