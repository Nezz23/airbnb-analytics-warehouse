"""Snapshot downloader against a local HTTP server serving synthetic snapshots. No internet access."""

import gzip
import json
import shutil
import threading
from collections import Counter
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ingestion import fetch
from tests.synthetic import generate

CITY = "/united-states/ny/new-york-city"
DATASETS = ("listings", "calendar", "reviews")


class Server:
    """Serves {date}/data/{dataset}.csv.gz from `files`, with injectable faults."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.index_dates: list[str] = []
        self.fail_5xx: Counter = Counter()  # path -> number of 500s before success
        self.missing: set[str] = set()
        self.short: set[str] = set()  # advertise more bytes than sent
        self.hits: Counter = Counter()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.hits[self.path] += 1
                if self.path == "/get-the-data/":
                    links = "".join(
                        f'<a href="{server.root}/{d}/data/{ds}.csv.gz">{ds}</a>'
                        for d in server.index_dates
                        for ds in DATASETS
                    )
                    links += f'<a href="{server.root}/2025-12-01/data/listings.csv.gz">incomplete</a>'
                    return self._send(200, links.encode())
                if server.fail_5xx[self.path] > 0:
                    server.fail_5xx[self.path] -= 1
                    return self._send(500, b"busy")
                body = server.files.get(self.path)
                if body is None or self.path in server.missing:
                    return self._send(404, b"not found")
                if self.path in server.short:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body) + 1000))
                    self.end_headers()
                    self.wfile.write(body)
                    return None
                return self._send(200, body)

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.root = self.base + CITY
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def path(self, snapshot, dataset):
        return f"{CITY}/{snapshot}/data/{dataset}.csv.gz"

    def publish(self, snapshot, files: dict[str, bytes], listed=True):
        for dataset, body in files.items():
            self.files[self.path(snapshot, dataset)] = body
        if listed:
            self.index_dates.append(snapshot)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    raw = tmp_path_factory.mktemp("synthetic")
    generate(raw)
    return {d.name: {ds: (d / f"{ds}.csv.gz").read_bytes() for ds in DATASETS} for d in raw.iterdir()}


@pytest.fixture
def server():
    s = Server()
    yield s
    s.httpd.shutdown()


@pytest.fixture
def settings(tmp_path, server):
    source = fetch.Source(
        index_url=server.base + "/get-the-data/",
        data_root=server.root,
        allowed_hosts=frozenset({"127.0.0.1"}),
        require_https=False,  # the local test server only; production default is https-only
    )
    return fetch.Settings(
        raw=tmp_path / "raw", bronze=tmp_path / "bronze", source=source, backoff_s=0, today=date(2026, 6, 1)
    )


def events(settings):
    path = settings.raw / "_fetch_log.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def landed(settings):
    return sorted(p.name for p in settings.raw.iterdir() if p.is_dir() and not p.name.startswith("."))


def test_discovers_only_complete_snapshots_newer_than_local(settings, server, synthetic):
    (settings.raw / "2026-01-01").mkdir(parents=True)
    for d in ("2026-01-01", "2026-02-01", "2026-03-01"):
        server.publish(d, synthetic[d])
    assert fetch.run(settings) == 0
    assert landed(settings) == ["2026-01-01", "2026-02-01", "2026-03-01"]
    for ds in DATASETS:
        assert (settings.raw / "2026-03-01" / f"{ds}.csv.gz").read_bytes() == synthetic["2026-03-01"][ds]
    discovered = next(e for e in events(settings) if e["event"] == "discovered")
    assert discovered["listed"] == ["2026-01-01", "2026-02-01", "2026-03-01"]  # 2025-12-01 lacks two datasets
    assert server.hits[server.path("2026-01-01", "listings")] == 0  # local snapshot never re-downloaded


def test_rerun_is_an_idempotent_no_op(settings, server, synthetic):
    server.publish("2026-02-01", synthetic["2026-02-01"])
    assert fetch.run(settings) == 0
    before = dict(server.hits)
    assert fetch.run(settings) == 0
    data_hits = {p: n for p, n in server.hits.items() if p.endswith(".gz")}
    assert data_hits == {p: n for p, n in before.items() if p.endswith(".gz")}
    assert events(settings)[-1]["event"] == "up_to_date"


def test_dry_run_downloads_nothing(settings, server, synthetic):
    server.publish("2026-02-01", synthetic["2026-02-01"])
    assert fetch.run(settings, dry_run=True) == 0
    assert landed(settings) == []
    assert events(settings)[-1] == {**events(settings)[-1], "event": "dry_run", "would_fetch": ["2026-02-01"]}


def test_transient_server_errors_are_retried(settings, server, synthetic):
    server.publish("2026-02-01", synthetic["2026-02-01"])
    server.fail_5xx[server.path("2026-02-01", "calendar")] = 2
    assert fetch.run(settings, ["2026-02-01"]) == 0
    assert landed(settings) == ["2026-02-01"]
    assert sum(e["event"] == "retry" for e in events(settings)) == 2


def test_missing_file_rejects_the_snapshot_without_retrying_and_lands_nothing(settings, server, synthetic):
    server.publish("2026-02-01", synthetic["2026-02-01"])
    server.missing.add(server.path("2026-02-01", "reviews"))
    assert fetch.run(settings, ["2026-02-01"]) == fetch.FAILED
    assert landed(settings) == []  # listings and calendar were fine, but a snapshot is all-or-nothing
    assert not (settings.raw / ".staging").exists() or not any((settings.raw / ".staging").iterdir())
    assert server.hits[server.path("2026-02-01", "reviews")] == 1
    assert events(settings)[-1]["event"] == "snapshot_rejected"


@pytest.mark.parametrize("fault", ["truncated_gzip", "short_transfer"])
def test_incomplete_downloads_fail_after_retries(settings, server, synthetic, fault):
    files = dict(synthetic["2026-02-01"])
    if fault == "truncated_gzip":
        files["calendar"] = files["calendar"][: len(files["calendar"]) // 2]
    server.publish("2026-02-01", files)
    if fault == "short_transfer":
        server.short.add(server.path("2026-02-01", "calendar"))
    assert fetch.run(settings, ["2026-02-01"]) == fetch.FAILED
    assert landed(settings) == []
    assert server.hits[server.path("2026-02-01", "calendar")] == settings.attempts
    assert events(settings)[-1]["event"] == "snapshot_failed"


def test_one_failed_snapshot_does_not_block_the_others(settings, server, synthetic):
    server.publish("2026-02-01", synthetic["2026-02-01"])
    server.publish("2026-03-01", synthetic["2026-03-01"])
    server.missing.add(server.path("2026-02-01", "listings"))
    assert fetch.run(settings, ["2026-02-01", "2026-03-01"]) == fetch.FAILED
    assert landed(settings) == ["2026-03-01"]


def test_header_without_contract_columns_is_rejected(settings, server, synthetic):
    files = dict(synthetic["2026-02-01"])
    files["listings"] = gzip.compress(b"id,name\n1,flat\n")
    server.publish("2026-02-01", files)
    assert fetch.run(settings, ["2026-02-01"]) == fetch.FAILED
    assert "header lacks contract columns" in events(settings)[-1]["error"]


def test_republished_identical_file_under_a_new_date_is_rejected(settings, server, synthetic):
    server.publish("2026-03-01", synthetic["2026-03-01"])
    server.publish("2026-03-15", synthetic["2026-03-01"])  # same bytes, new date
    assert fetch.run(settings, ["2026-03-01"]) == 0
    assert fetch.run(settings, ["2026-03-15"]) == fetch.FAILED
    assert landed(settings) == ["2026-03-01"]
    assert "byte-identical to already landed 2026-03-01/listings" in events(settings)[-1]["error"]


def test_bronze_manifest_hashes_also_count_as_landed(settings, server, synthetic):
    import hashlib

    digest = hashlib.sha256(synthetic["2026-02-01"]["listings"]).hexdigest()
    settings.bronze.mkdir(parents=True)
    (settings.bronze / "_manifest.jsonl").write_text(
        json.dumps({"dataset": "listings", "snapshot_date": "2026-01-15", "file_sha256": digest}) + "\n"
    )
    server.publish("2026-02-01", synthetic["2026-02-01"])
    assert fetch.run(settings, ["2026-02-01"]) == fetch.FAILED
    assert "2026-01-15/listings" in events(settings)[-1]["error"]


def test_existing_snapshot_is_never_overwritten(settings, server, synthetic):
    server.publish("2026-02-01", synthetic["2026-02-01"])
    (settings.raw / "2026-02-01").mkdir(parents=True)
    (settings.raw / "2026-02-01" / "listings.csv.gz").write_bytes(b"hand-placed")
    assert fetch.run(settings, ["2026-02-01"]) == 0
    assert (settings.raw / "2026-02-01" / "listings.csv.gz").read_bytes() == b"hand-placed"
    assert server.hits[server.path("2026-02-01", "listings")] == 0


@pytest.mark.parametrize(
    "url, message",
    [
        ("http://insideairbnb.com/get-the-data/", "non-https"),
        ("https://evil.example.com/get-the-data/", "refusing source host"),
        (
            "https://data.insideairbnb.com/united-states/ny/new-york-city/2026-01-01/data/../../x.csv.gz",
            "unexpected data path",
        ),
    ],
)
def test_production_source_rejects_untrusted_urls(url, message):
    with pytest.raises(fetch.PermanentError, match=message):
        fetch.Source().validate_url(url)


def test_production_source_accepts_the_real_layout():
    src = fetch.Source()
    src.validate_url(src.index_url)
    src.validate_url(src.file_url("2026-09-14", "listings"))


@pytest.mark.parametrize("bad", ["2026-13-01", "yesterday", "2026-07-01"])
def test_invalid_or_future_dates_are_refused(settings, bad):
    assert fetch.run(settings, [bad]) == fetch.FAILED  # today is 2026-06-01 in these settings
    assert events(settings)[-1]["event"] == "plan_failed"


def test_concurrent_run_is_locked_out(settings):
    with fetch.exclusive_lock(settings.raw), pytest.raises(SystemExit) as exc:
        fetch.run(settings)
    assert exc.value.code == fetch.LOCKED


def test_stale_staging_from_a_killed_run_is_cleared(settings, server, synthetic):
    stale = settings.raw / ".staging" / "2026-02-01.999"
    stale.mkdir(parents=True)
    server.publish("2026-02-01", synthetic["2026-02-01"])
    assert fetch.run(settings) == 0
    assert not stale.exists()
    shutil.rmtree(settings.raw / "2026-02-01")
