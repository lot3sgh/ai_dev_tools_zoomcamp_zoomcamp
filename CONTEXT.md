# Health Data Pipeline

Ingests the user's Google Health (Fitbit) takeout archives from Google Drive into Postgres.

## Language

**Takeout**:
A zip archive produced by Google's export of the user's Google Health data (Google Takeout). The pipeline's unit of ingestion — one takeout = one archive.
_Avoid_: Export, zip file, backup

**Service Account (SA)**:
The Google identity whose credentials (`my-health-sp@wise-weaver-509208-b9`) the pipeline uses to authenticate to Google Drive. The takeout contents are Google's own export of this user's data, not the SA's.
_Avoid_: Service principal, bot account, key file

**Drive File**:
A single file listed in Google Drive (in practice, a takeout zip). Identified by file id + `modifiedTime`; the metadata that determines what to download.
_Avoid_: Drive object, document

**Family**:
A group of CSVs under one subfolder of the takeout that share a row shape (e.g. all `Active Zone Minutes - YYYY-MM-DD.csv` files). Each family becomes exactly one raw table; inward schema drift is absorbed by widening with nullable columns.
_Avoid_: Dataset, folder, metric group

**Bronze Table**:
A Postgres table in the `bronze` schema holding a family's rows verbatim — every column as TEXT, plus provenance columns (`_takeout`, `_source_file`, `_loaded_at`). No types, no business meaning; that happens in the silver layer.
_Avoid_: Landing table, staging table, raw table

**Silver Table**:
A typed, keyed Postgres table in the `silver` schema derived from bronze tables for a domain entity (sleep, active zone minutes, device, profile). Upserts by natural key; rows that fail type conversion go to `silver.rejected_rows` with the reason instead of failing the run.
_Avoid_: Curated table, gold table, model table

**Gold Table**:
A future aggregation/reporting table (daily metric totals, trends). Deliberately not built yet; the name exists so the deferred layer is explicit as `gold`.
_Avoid_: Report, dashboard table