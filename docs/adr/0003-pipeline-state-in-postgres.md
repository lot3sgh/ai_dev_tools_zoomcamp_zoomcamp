# Sync state lives in Postgres with md5 change detection

`pipeline.processed_files` records `file_id, name, modified_time, md5, status, row_count, error, processed_at` for every Drive/local file. A file is processed when new (unknown id) or changed (md5 differs); failures leave `status=error` and are retried on the next run; deleting a file from Drive does not delete its data.

State in Postgres (rather than a local cache) was chosen so it survives container restarts and is inspectable via SQL alongside the data it describes. Drive's `md5Checksum` makes change detection cheap — no re-download to check.