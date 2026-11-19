"""Download new Inside Airbnb NYC snapshots into data/raw/<date>/ for bronze ingestion. Local only.

    uv run python -m ingestion.fetch --dry-run             # what would be downloaded, touches nothing
    uv run python -m ingestion.fetch                       # snapshots on the index newer than any local one
    uv run python -m ingestion.fetch --date 2026-12-01     # a specific snapshot (the index lists only the latest)
    uv run python -m ingestion.fetch && uv run python pipeline.py

Safety properties (each covered by tests/test_fetch.py):
- Source validation: only URLs on an allowlisted host matching the NYC path pattern, https only;
  snapshot dates must parse and must not be in the future.
- Integrity: Content-Length must match; every file is fully decompressed (gzip CRC and length
  trailer), so a truncated download is caught; the CSV header must hold the contract's required
  columns; the size is capped; sha256 is recorded.
- Duplicates: snapshots already in data/raw are never re-downloaded or overwritten. A file whose
  sha256 equals one already landed (fetch log or bronze manifest) under another date is a
  re-publication, and that snapshot is rejected: a copy would create a fake snapshot in SCD2.
- All-or-nothing: the three files download into data/raw/.staging/ and the snapshot directory
  appears with one atomic rename only when all three pass. Bronze never sees half a snapshot.
- Failures: transient errors (timeouts, 5xx, 429, truncation) retry with exponential backoff;
  permanent ones (404, bad header, duplicate) do not. One failed snapshot does not stop the others.
  Exit status: 0 ok or nothing to do, 1 a snapshot failed, 3 another fetch holds the lock.
- Idempotent: rerunning skips landed snapshots; a lock file prevents concurrent runs; staging is
  cleaned on every exit path. Every event is appended to data/raw/_fetch_log.jsonl.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import hashlib
import http.client
import io
import json
import logging
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

from ingestion import contract as ct
from ingestion.bronze import SNAPSHOT_DIR, read_manifest

log = logging.getLogger("fetch")

USER_AGENT = "airbnb-analytics-warehouse-fetch/1.0"
MAX_BYTES = 2 * 1024**3  # largest NYC file (reviews) is ~120 MB; anything near this is wrong
LOCKED, FAILED = 3, 1


class PermanentError(Exception):
    """Retrying cannot help: missing file, bad source, contract-breaking header, duplicate."""


class TransientError(Exception):
    """Worth retrying: network trouble, server errors, truncated transfer."""


@dataclass(frozen=True)
class Source:
    index_url: str = "https://insideairbnb.com/get-the-data/"
    data_root: str = "https://data.insideairbnb.com/united-states/ny/new-york-city"
    allowed_hosts: frozenset[str] = frozenset({"insideairbnb.com", "data.insideairbnb.com"})
    require_https: bool = True

    def file_url(self, snapshot: str, dataset: str) -> str:
        return f"{self.data_root}/{snapshot}/data/{dataset}.csv.gz"

    def link_pattern(self) -> re.Pattern:
        return re.compile(
            re.escape(self.data_root) + r"/(\d{4}-\d{2}-\d{2})/data/(" + "|".join(ct.DATASETS) + r")\.csv\.gz"
        )

    def validate_url(self, url: str) -> None:
        u = urlparse(url)
        if self.require_https and u.scheme != "https":
            raise PermanentError(f"refusing non-https source {url}")
        if u.hostname not in self.allowed_hosts:
            raise PermanentError(f"refusing source host {u.hostname!r} (allowed: {sorted(self.allowed_hosts)})")
        if url.startswith(self.data_root) and not self.link_pattern().fullmatch(url):
            raise PermanentError(f"unexpected data path {url}")


@dataclass
class Settings:
    raw: Path
    bronze: Path
    source: Source = field(default_factory=Source)
    attempts: int = 3
    backoff_s: float = 2.0
    timeout_s: float = 60.0
    max_bytes: int = MAX_BYTES
    today: date = field(default_factory=lambda: datetime.now(UTC).date())


# --------------------------------------------------------------------------- logging & state


class FetchLog:
    """Append-only JSON-lines audit trail: data/raw/_fetch_log.jsonl."""

    def __init__(self, raw: Path):
        self.path = raw / "_fetch_log.jsonl"

    def write(self, event: str, **fields) -> None:
        record = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        level = logging.WARNING if event.endswith(("failed", "rejected", "retry")) else logging.INFO
        log.log(level, "%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items() if k != "url"))

    def landed_hashes(self) -> dict[str, str]:
        """sha256 -> 'snapshot/dataset' for every file that ever landed."""
        if not self.path.exists():
            return {}
        out = {}
        for line in self.path.read_text().splitlines():
            r = json.loads(line)
            if r["event"] == "file_verified" and r.get("landed"):
                out[r["sha256"]] = f"{r['snapshot_date']}/{r['dataset']}"
        return out


def known_hashes(settings: Settings, fetch_log: FetchLog) -> dict[str, str]:
    hashes = fetch_log.landed_hashes()
    for e in read_manifest(settings.bronze / "_manifest.jsonl"):  # files that arrived before this tool
        hashes.setdefault(e["file_sha256"], f"{e['snapshot_date']}/{e['dataset']}")
    return hashes


def local_snapshots(raw: Path) -> set[str]:
    return {p.name for p in raw.iterdir() if p.is_dir() and SNAPSHOT_DIR.match(p.name)} if raw.exists() else set()


@contextmanager
def exclusive_lock(raw: Path) -> Iterator[None]:
    raw.mkdir(parents=True, exist_ok=True)
    with (raw / ".fetch.lock").open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(LOCKED) from None
        yield


# --------------------------------------------------------------------------- HTTP


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (scheme validated by Source)
    except urllib.error.HTTPError as e:
        if e.code in (408, 429) or e.code >= 500:
            raise TransientError(f"HTTP {e.code} for {url}") from e
        raise PermanentError(f"HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise TransientError(f"{type(e).__name__}: {e} for {url}") from e


def with_retries(settings: Settings, fetch_log: FetchLog, what: dict, fn):
    for attempt in range(1, settings.attempts + 1):
        try:
            return fn()
        except TransientError as e:
            if attempt == settings.attempts:
                raise
            delay = settings.backoff_s * 2 ** (attempt - 1)
            fetch_log.write("retry", **what, attempt=attempt, delay_s=delay, error=str(e))
            time.sleep(delay)


def discover(settings: Settings, fetch_log: FetchLog) -> list[str]:
    """Snapshot dates the index links to for all three datasets."""
    src = settings.source
    src.validate_url(src.index_url)

    def get() -> str:
        with _open(src.index_url, settings.timeout_s) as r:
            return r.read(20 * 1024**2).decode("utf-8", "replace")

    html = with_retries(settings, fetch_log, {"url": src.index_url}, get)
    found: dict[str, set[str]] = {}
    for snapshot, dataset in src.link_pattern().findall(html):
        found.setdefault(snapshot, set()).add(dataset)
    return sorted(s for s, ds in found.items() if ds == set(ct.DATASETS))


def download(url: str, dest: Path, settings: Settings) -> tuple[int, str]:
    """Stream to dest while hashing; returns (bytes, sha256). Checks Content-Length and the size cap."""
    h, size = hashlib.sha256(), 0
    with _open(url, settings.timeout_s) as r, dest.open("wb") as out:
        expected = r.headers.get("Content-Length")
        try:
            while chunk := r.read(1 << 20):
                size += len(chunk)
                if size > settings.max_bytes:
                    raise PermanentError(f"{url} exceeds {settings.max_bytes} bytes")
                h.update(chunk)
                out.write(chunk)
        except (OSError, http.client.HTTPException) as e:  # timeouts, resets, IncompleteRead
            raise TransientError(f"transfer interrupted after {size} bytes: {e}") from e
    if expected is not None and int(expected) != size:
        raise TransientError(f"short transfer: {size} of {expected} bytes")
    return size, h.hexdigest()


def verify(path: Path, dataset: str) -> int:
    """Full gzip decompress (CRC/length trailer) and contract header check. Returns the data line count."""
    required = list(ct.load(dataset).required_columns)
    try:
        with gzip.open(path, "rb") as gz:
            header = gz.readline().decode("utf-8-sig").strip()
            lines = sum(1 for _ in gz)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        raise TransientError(f"{dataset}: corrupt or truncated gzip ({e})") from e
    columns = next(csv.reader(io.StringIO(header)), [])
    missing = [c for c in required if c not in columns]
    if missing:
        raise PermanentError(f"{dataset}: header lacks contract columns {missing}")
    return lines


# --------------------------------------------------------------------------- orchestration


def fetch_snapshot(snapshot: str, settings: Settings, fetch_log: FetchLog, hashes: dict[str, str]) -> bool:
    staging = settings.raw / ".staging" / f"{snapshot}.{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        verified = []
        for dataset in ct.DATASETS:
            url = settings.source.file_url(snapshot, dataset)
            settings.source.validate_url(url)
            dest = staging / f"{dataset}.csv.gz"
            what = {"snapshot_date": snapshot, "dataset": dataset, "url": url}

            def attempt(url=url, dest=dest, dataset=dataset):
                size, digest = download(url, dest, settings)
                return size, digest, verify(dest, dataset)

            size, digest, lines = with_retries(settings, fetch_log, what, attempt)
            if hashes.get(digest, f"{snapshot}/{dataset}") != f"{snapshot}/{dataset}":
                raise PermanentError(f"{dataset} is byte-identical to already landed {hashes[digest]}")
            verified.append({**what, "bytes": size, "sha256": digest, "lines": lines})
        final = settings.raw / snapshot
        if final.exists():  # appeared meanwhile (e.g. copied in by hand): never overwrite
            raise PermanentError(f"{final} already exists")
        staging.rename(final)  # atomic on one filesystem: the snapshot appears complete or not at all
        for v in verified:
            fetch_log.write("file_verified", landed=True, **v)
            hashes[v["sha256"]] = f"{snapshot}/{v['dataset']}"
        fetch_log.write("snapshot_landed", snapshot_date=snapshot, path=str(final))
        return True
    except (PermanentError, TransientError) as e:
        event = "snapshot_rejected" if isinstance(e, PermanentError) else "snapshot_failed"
        fetch_log.write(event, snapshot_date=snapshot, error=str(e))
        return False
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def plan(settings: Settings, dates: list[str] | None, fetch_log: FetchLog) -> list[str]:
    have = local_snapshots(settings.raw)
    for d in dates or []:
        try:
            parsed = date.fromisoformat(d)
        except ValueError:
            raise PermanentError(f"not a date: {d!r}") from None
        if parsed > settings.today:
            raise PermanentError(f"{d} is in the future")
    if dates:
        wanted = sorted(set(dates))
    else:
        listed = [d for d in discover(settings, fetch_log) if date.fromisoformat(d) <= settings.today]
        latest = max(have, default="")
        wanted = [d for d in listed if d > latest]
        fetch_log.write("discovered", listed=listed, latest_local=latest or None, new=wanted)
    skipped = [d for d in wanted if d in have]
    if skipped:
        fetch_log.write("already_present", snapshot_dates=skipped)
    return [d for d in wanted if d not in have]


def run(settings: Settings, dates: list[str] | None = None, dry_run: bool = False) -> int:
    with exclusive_lock(settings.raw):
        fetch_log = FetchLog(settings.raw)
        shutil.rmtree(settings.raw / ".staging", ignore_errors=True)  # left by a killed run; we hold the lock
        try:
            todo = plan(settings, dates, fetch_log)
        except (PermanentError, TransientError) as e:
            fetch_log.write("plan_failed", error=str(e))
            return FAILED
        if dry_run or not todo:
            fetch_log.write("dry_run" if dry_run else "up_to_date", would_fetch=todo)
            return 0
        hashes = known_hashes(settings, fetch_log)
        ok = [fetch_snapshot(s, settings, fetch_log, hashes) for s in todo]
        return 0 if all(ok) else FAILED


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", type=Path, default=Path("data/raw"))
    p.add_argument("--bronze", type=Path, default=Path("data/bronze"))
    p.add_argument("--date", action="append", dest="dates", help="snapshot date YYYY-MM-DD (repeatable)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(run(Settings(raw=a.raw.resolve(), bronze=a.bronze.resolve()), a.dates, a.dry_run))


if __name__ == "__main__":
    main()
