"""Shared fixture: build a warehouse from synthetic snapshots once per test session."""

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from tests.synthetic import generate

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    generate(data / "raw")
    env = {**os.environ, "AIRBNB_DATA_DIR": str(data)}
    for _ in range(2):  # the second run must be an incremental no-op
        subprocess.run([sys.executable, "pipeline.py"], cwd=ROOT, env=env, check=True)
    return data


@pytest.fixture(scope="module")
def warehouse(data_dir):
    con = duckdb.connect(str(data_dir / "warehouse.duckdb"), read_only=True)
    yield con
    con.close()
