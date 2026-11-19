# Automated snapshot ingestion

`ingestion/fetch.py` downloads new Inside Airbnb NYC snapshots into `data/raw/<date>/`, where the
existing pipeline picks them up. It uses only the Python standard library and needs no cloud service.

```bash
uv run python -m ingestion.fetch --dry-run           # report what would be fetched; downloads nothing
uv run python -m ingestion.fetch                     # snapshots on the index newer than the newest local one
uv run python -m ingestion.fetch --date 2026-12-01   # a specific date (repeatable)
uv run python -m ingestion.fetch && uv run python pipeline.py
```

## Source

The fetcher discovers snapshots from `https://insideairbnb.com/get-the-data/`, which links the
three files per snapshot under `https://data.insideairbnb.com/united-states/ny/new-york-city/<date>/data/`.
**The index lists only the latest NYC snapshot**: on 2026-11-19 it showed 2026-09-14 while this
warehouse already held 2026-10-04 and 2026-11-01. Older or unlisted dates therefore need `--date`.
That builds the URL from the same pattern and goes through the same validation.

## Guarantees

| Concern | Behavior |
|---|---|
| Source validation | https only; host must be `insideairbnb.com` or `data.insideairbnb.com`; data URLs must match the exact NYC path pattern (no `..`); dates must parse and not be in the future |
| Integrity | Content-Length must match the bytes received; each file is fully decompressed, so the gzip CRC and length trailer catch truncation; the CSV header must contain the data contract's required columns; 2 GB size cap; sha256 recorded |
| Duplicates | Snapshots already in `data/raw` are never downloaded or overwritten. A file byte-identical to one already landed (fetch log or bronze manifest) under a different date rejects the snapshot, because a copied file would create a fake snapshot in SCD2 |
| All-or-nothing | Files download into `data/raw/.staging/`; the snapshot directory appears with one atomic rename only after all three pass |
| Failure handling | Timeouts, connection resets, HTTP 5xx/408/429 and truncated transfers get up to 3 attempts with exponential backoff (2 s, then 4 s). A 404, bad source, bad header or duplicate fails immediately. One failed snapshot does not stop the others |
| Idempotency | Reruns skip landed snapshots; a lock file (`data/raw/.fetch.lock`) stops concurrent runs; staging left by a killed run is cleared at the next start |
| Logging | Console log plus an append-only audit trail, `data/raw/_fetch_log.jsonl` (discovered, retry, file_verified, snapshot_landed / rejected / failed) |
| Exit status | `0` success or nothing to do, `1` a snapshot failed or the plan was invalid, `3` another fetch is running |

Contract validation of row contents (types, domains, volume) still happens in bronze, where a bad
snapshot is quarantined rather than dropped. The fetcher only guarantees that complete, genuine,
correctly-shaped files arrive.

`tests/test_fetch.py` covers each guarantee against a local HTTP server with injected faults
(500s, 404s, truncated gzip, short transfers, bad headers, republished files, locks, stale staging).

## Scheduling locally (optional, free, not enabled)

Inside Airbnb publishes NYC roughly monthly, so a weekly check is plenty. On macOS, save the plist below
as `~/Library/LaunchAgents/com.airbnb-warehouse.fetch.plist`, adjust the paths, then
`launchctl load` it. It runs only while you are logged in, and costs nothing.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.airbnb-warehouse.fetch</string>
  <key>WorkingDirectory</key><string>/path/to/AirbnbWarehouse</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>uv run python -m ingestion.fetch &amp;&amp; uv run python pipeline.py</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>7</integer></dict>
  <key>StandardOutPath</key><string>/path/to/AirbnbWarehouse/data/fetch.out.log</string>
  <key>StandardErrorPath</key><string>/path/to/AirbnbWarehouse/data/fetch.err.log</string>
</dict></plist>
```

The cron equivalent is `0 7 * * 1 cd /path/to/AirbnbWarehouse && uv run python -m ingestion.fetch && uv run python pipeline.py`.
