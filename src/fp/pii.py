"""Encrypted local PII store (data/pii.duckdb). Key lives in the macOS login Keychain.

Never write PII to public.duckdb, git, or non-exports paths. See docs/SPEC.md §3.2.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess

import duckdb

from fp import db, paths

KEYCHAIN_SERVICE = "FacultyPerformance"
KEYCHAIN_ACCOUNT = "pii.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pii.degree (
    person      VARCHAR,     -- csrankings/dblp name (person key until DBLP pid in M4)
    degree      VARCHAR,     -- phd / ms / bs
    year        INTEGER,     -- completion year
    institution VARCHAR,
    source      VARCHAR,     -- orcid / homepage / manual
    confidence  DOUBLE,
    evidence    VARCHAR,
    fetched_at  TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.birth (
    person      VARCHAR,
    year        INTEGER,
    source      VARCHAR,     -- homepage / manual
    confidence  DOUBLE,
    evidence    VARCHAR,
    fetched_at  TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.fetch_log (
    person      VARCHAR,
    source      VARCHAR,     -- orcid / homepage
    status      VARCHAR,     -- ok / empty / invalid_id / error
    fetched_at  TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.raw (
    person      VARCHAR,
    source      VARCHAR,     -- orcid / homepage
    ref         VARCHAR,     -- orcid id / url
    body        VARCHAR,     -- raw response, kept only inside the encrypted DB
    fetched_at  TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.note (
    person      VARCHAR,
    note        VARCHAR,
    created_at  TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.korean (
    person     VARCHAR PRIMARY KEY,
    is_korean  BOOLEAN,           -- NULL = undecided
    score      DOUBLE,
    method     VARCHAR,           -- rule / coauthor / llm / manual
    reason     VARCHAR,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.korean_verify (
    person     VARCHAR PRIMARY KEY,   -- second opinion for high-visibility people (stronger model)
    is_korean  BOOLEAN,
    confidence DOUBLE,
    model      VARCHAR,
    reason     VARCHAR,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.forget (
    person      VARCHAR PRIMARY KEY,   -- never re-collect
    created_at  TIMESTAMP DEFAULT current_timestamp
);
"""


def _get_key() -> str:
    env = os.environ.get("FP_PII_KEY")  # tests / non-macOS
    if env:
        return env
    if not shutil.which("security"):
        raise RuntimeError("macOS `security` not found; set FP_PII_KEY")
    r = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    if paths.PII_DB.exists():
        raise RuntimeError("pii.duckdb exists but its key is missing from the Keychain — refusing to overwrite")
    key = secrets.token_hex(32)
    subprocess.run(
        ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", key],
        check=True,
        capture_output=True,
    )
    return key


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """public.duckdb as main catalog + encrypted pii.duckdb attached as `pii`."""
    con = db.connect(read_only=read_only)
    key = _get_key()
    mode = ", READ_ONLY" if read_only and paths.PII_DB.exists() else ""
    con.execute(f"ATTACH '{paths.PII_DB}' AS pii (ENCRYPTION_KEY '{key}'{mode})")
    if not mode:
        con.execute(SCHEMA)
        from fp.ingest.openalex import PII_SCHEMA as OA_SCHEMA

        con.execute(OA_SCHEMA)
        from fp.report import SCHEMA as RUN_SCHEMA

        con.execute(RUN_SCHEMA)
    return con


def forgotten(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT person FROM pii.forget").fetchall()}


def forget(con, person: str) -> dict[str, int]:
    out = {}
    for t in ("degree", "birth", "fetch_log", "note", "raw", "korean", "korean_verify", "affiliation", "run_person"):
        out[t] = len(con.execute(f"DELETE FROM pii.{t} WHERE person = ? RETURNING 1", [person]).fetchall())
    con.execute("INSERT OR IGNORE INTO pii.forget (person) VALUES (?)", [person])
    return out
