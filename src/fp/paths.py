"""Filesystem layout. Everything under data/, cache/, exports/ is local-only (gitignored)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("FP_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
RAW = DATA / "raw"
SNAPSHOTS = DATA / "snapshots"
CACHE = ROOT / "cache"
EXPORTS = ROOT / "exports"

PUBLIC_DB = DATA / "public.duckdb"
PII_DB = DATA / "pii.duckdb"


def ensure_dirs() -> None:
    for d in (DATA, RAW, SNAPSHOTS, CACHE, EXPORTS):
        d.mkdir(parents=True, exist_ok=True)
