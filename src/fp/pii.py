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


KEY_FILE_DEFAULT = "~/.config/faculty-performance/pii.key"


def _key_file():
    from pathlib import Path

    return Path(os.environ.get("FP_PII_KEY_FILE") or KEY_FILE_DEFAULT).expanduser()


def _read_key_file() -> str | None:
    """Key file for servers without a macOS Keychain. Must be private (mode 600) — refuse otherwise."""
    kf = _key_file()
    if not kf.exists():
        return None
    if os.name == "posix" and kf.stat().st_mode & 0o077:
        raise RuntimeError(f"{kf} is readable by other users; run: chmod 600 {kf}")
    return kf.read_text().strip() or None


def write_key_file(key: str, path=None):
    from pathlib import Path

    kf = Path(path).expanduser() if path else _key_file()
    kf.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key + "\n")
    os.chmod(kf, 0o600)
    return kf


def keychain_key() -> str | None:
    if not shutil.which("security"):
        return None
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _get_key() -> str:
    """Key lookup: FP_PII_KEY env → key file (FP_PII_KEY_FILE or ~/.config/faculty-performance/pii.key)
    → macOS Keychain. A new key is only generated when no pii.duckdb exists yet."""
    env = os.environ.get("FP_PII_KEY")  # tests / non-macOS
    if env:
        return env
    fk = _read_key_file()
    if fk:
        return fk
    if not shutil.which("security"):
        if paths.PII_DB.exists():
            raise RuntimeError(f"pii.duckdb is encrypted: put its key in {_key_file()} (chmod 600) "
                               "or FP_PII_KEY. On the original Mac: fp pii export-key")
        raise RuntimeError("macOS `security` not found; set FP_PII_KEY or create a key file")
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
    if read_only and not paths.PII_DB.exists() and paths.PII_OPEN_DB.exists():
        # hosted app (streamlit.app): the open-data bundle ships this DB unencrypted (user decision 2026-11-10:
        # all of it is built from public sources)
        con.execute(f"ATTACH '{paths.PII_OPEN_DB}' AS pii (READ_ONLY)")
        return con
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
