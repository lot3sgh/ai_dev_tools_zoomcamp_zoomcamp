# Hand-rolled psycopg COPY loader; dlt was considered and not adopted

The pipeline loads bronzed data with hand-rolled psycopg `COPY` (streamed CSV, family-per-table, delete-then-insert per source file), not the `dlt` data-loading library.

`dlt` was evaluated and rejected for this architecture. Its strengths — schema inference and evolution, normalized names, row-level incremental state, and a Google Drive verified source — either duplicate what the bronze/silver split already provides (typing deliberately lives in silver, ADR-0002) or don't match the sync model (incrementality is per-archive md5 in `pipeline.processed_files`, ADR-0003, which dlt doesn't model). Its default batched-insert loading is slower than `COPY` at the ~11M-row scale here, and it has no rejected-row audit equivalent to `silver.rejected_rows`.

Revisit if the project gains a second source or destination, a gold/marts layer in need of standard tooling, or team-scale schema governance — at that point dlt-ing the bronze layer while keeping silver bespoke is the natural adoption path.