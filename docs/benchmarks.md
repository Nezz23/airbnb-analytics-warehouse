# Query-performance benchmarks

Measured 2026-11-19T21:01:45+00:00 on Apple M4 Pro (12 cores), macOS-26.6.2-arm64-arm-64bit, DuckDB 2.0.0.
Each query: one warm-up, then 5 timed runs on a fresh connection. Regenerate with `uv run python benchmarks/run.py`.

| Question | Layer | Median (s) | Min (s) | Speed-up vs slowest |
|---|---|---:|---:|---:|
| Median price by borough per snapshot | raw_csv | 1.2457 | 1.2380 | 1x |
| Median price by borough per snapshot | bronze_parquet | 0.0086 | 0.0078 | 146x |
| Median price by borough per snapshot | gold_mart | 0.0007 | 0.0007 | 1723x |
| Unregistered short-term listings per snapshot (LL18) | raw_csv | 1.2452 | 1.2326 | 1x |
| Unregistered short-term listings per snapshot (LL18) | bronze_parquet | 0.0026 | 0.0026 | 474x |
| Unregistered short-term listings per snapshot (LL18) | gold_mart | 0.0006 | 0.0005 | 2053x |
| Point-in-time: every listing's attributes as of 2026-07-01 | bronze_parquet | 0.0608 | 0.0605 | 1x |
| Point-in-time: every listing's attributes as of 2026-07-01 | gold_scd2 | 0.0318 | 0.0315 | 2x |

## Storage

All 10 snapshots. Reviews shrink mostly because bronze drops reviewer names and review text (personal data, unused); listings and calendar keep every column.

| Dataset | Raw CSV gzip (MB) | Bronze Parquet zstd (MB) |
|---|---:|---:|
| listings | 152 | 102 |
| calendar | 240 | 17 |
| reviews | 1,130 | 147 |

DuckDB warehouse (silver + gold, incl. SCD2): 207 MB.

Notes: raw and bronze queries include the quarantined 2026-06-08 partial snapshot; gold excludes it, so answers differ slightly for that date. Gold marts are pre-aggregated, so their speed-up includes work done once at build time (full pipeline rebuild: see README).
