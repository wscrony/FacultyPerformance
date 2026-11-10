"""DuckDB connections. public.duckdb holds rebuildable public data; PII lives in pii.duckdb (M2)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from fp import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot (
    source      VARCHAR,
    version     VARCHAR,      -- git commit sha / release id
    fetched_at  TIMESTAMP,
    PRIMARY KEY (source, version)
);
CREATE TABLE IF NOT EXISTS venue (
    venue       VARCHAR PRIMARY KEY,   -- csrankings raw area key, e.g. 'sigmod'
    area        VARCHAR,               -- parent area, e.g. 'mod'
    area_title  VARCHAR,               -- e.g. 'DB'
    category    VARCHAR,               -- ai / systems / theory / interdisciplinary
    next_tier   BOOLEAN
);
CREATE TABLE IF NOT EXISTS institution (
    institution VARCHAR PRIMARY KEY,
    region      VARCHAR,
    country     VARCHAR,
    homepage    VARCHAR
);
CREATE TABLE IF NOT EXISTS cs_faculty (
    name        VARCHAR,
    affiliation VARCHAR,
    homepage    VARCHAR,
    scholarid   VARCHAR,
    orcid       VARCHAR
);
CREATE TABLE IF NOT EXISTS cs_pub (
    name          VARCHAR,
    dept          VARCHAR,
    venue         VARCHAR,
    year          INTEGER,
    count         INTEGER,
    adjustedcount DOUBLE
);
"""


def connect(path: Path | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = path or paths.PUBLIC_DB
    paths.ensure_dirs()
    if read_only:
        # the app: DuckDB's default memory_limit is 80% of the *host* RAM, far above a streamlit.app container
        import os

        cfg = {"memory_limit": os.environ.get("FP_DUCKDB_MEMORY", "1GB"),
               "threads": os.environ.get("FP_DUCKDB_THREADS", "2")}
        con = duckdb.connect(str(path), read_only=True, config=cfg)
    else:
        con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con
