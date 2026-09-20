# Bronze layer stores all values as TEXT

Bronze tables hold family rows verbatim with every CSV value as TEXT, plus provenance columns (`_takeout`, `_source_file`, `_loaded_at`). All typing happens in the silver layer.

We deliberately skipped column type inference at landing. The takeout spans 3400+ CSVs with monthly files that drift in shape; mechanical TEXT landing is immune to that drift and keeps "get everything in" reliable. Silver then types, keys, and validates per domain, sending unparseable rows to `silver.rejected_rows` instead of failing the run.