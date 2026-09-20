# Takeout zips are pulled from Google Drive by a service account; local zips run through the same engine

The pipeline's production source is Google Drive: takeout zips are listed via the Drive API (as the `my-health-sp` service account, `drive.readonly` scope), filtered by file id + `md5Checksum`, and downloaded. Local zip directories (`data/`) are a first-class dev source through the same sync engine, where the file id is the filename and the md5 is the file hash.

A takeout zip is the unit of sync — new takeouts arrive as new/modified archive files, which gives clean incremental detection. We chose this over reading loose extracted files from Drive because it puts the network boundary in one place (download artifact, process locally) and lets development proceed with zero Drive access.