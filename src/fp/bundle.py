"""Open-data bundle for the hosted app (streamlit.app): public.duckdb + pii_open.duckdb (unencrypted) + settings.

`fp export app-bundle` writes exports/app_bundle/fp_data.tar.gz; upload it (e.g. a GitHub release asset) and set
FP_DATA_URL in the app's secrets. Internal crawl dumps (raw homepage text, fetch log, notes) are not included:
the app never shows them.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
from pathlib import Path

from fp import paths

SKIP_PII = {"raw", "note"}  # crawled page text, free-form notes
FILES = ["public.duckdb", "pii_open.duckdb", "top_venues.json", "venue_catalog_csconf.csv"]


def export(out_dir: Path = paths.EXPORTS / "app_bundle") -> Path:
    import duckdb

    from fp import pii

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=out_dir))
    try:
        src = duckdb.connect()  # in-memory hub: sources read-only, outputs writable
        src.execute(f"ATTACH '{paths.PUBLIC_DB}' AS public (READ_ONLY)")
        src.execute(f"ATTACH '{paths.PII_DB}' AS pii (ENCRYPTION_KEY '{pii._get_key()}', READ_ONLY)")
        # public: compact copy (COPY FROM DATABASE rewrites without the free blocks of past reloads)
        src.execute(f"ATTACH '{tmp / 'public.duckdb'}' AS outp")
        src.execute("COPY FROM DATABASE public TO outp")
        # paper × faculty-author table the boards need: built once here so the app doesn't build a ~100 MB temp
        # copy per cursor (that blew the streamlit.app memory limit)
        from fp.boards import PINST_SQL

        src.execute("USE outp")
        src.execute(f"CREATE OR REPLACE TABLE pinst AS {PINST_SQL}")
        src.execute("USE memory")
        src.execute("DETACH outp")
        src.execute(f"ATTACH '{tmp / 'pii_open.duckdb'}' AS outq")
        for (t,) in src.execute("SELECT table_name FROM duckdb_tables() WHERE database_name = 'pii'").fetchall():
            if t not in SKIP_PII:
                src.execute(f'CREATE TABLE outq."{t}" AS SELECT * FROM pii."{t}"')
        src.execute("DETACH outq")
        src.close()
        for f in ("top_venues.json", "venue_catalog_csconf.csv"):
            if (paths.DATA / f).exists():
                shutil.copy(paths.DATA / f, tmp / f)
        check = duckdb.connect(str(tmp / "pii_open.duckdb"), read_only=True)
        left = {r[0] for r in check.execute("SELECT table_name FROM duckdb_tables()").fetchall()} & SKIP_PII
        check.close()
        assert not left, left
        tar = out_dir / "fp_data.tar.gz"
        with tarfile.open(tar, "w:gz") as tf:
            for f in FILES:
                if (tmp / f).exists():
                    tf.add(tmp / f, arcname=f)
        return tar
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure(url: str) -> None:
    """Download and unpack the bundle into data/ unless it is already there (once per container)."""
    import fcntl

    marker = paths.DATA / ".bundle_url"
    if paths.PUBLIC_DB.exists() and marker.exists() and marker.read_text() == url:
        return
    paths.DATA.mkdir(parents=True, exist_ok=True)
    with open(paths.DATA / ".bundle.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)  # other sessions wait here, then see the marker
        if paths.PUBLIC_DB.exists() and marker.exists() and marker.read_text() == url:
            return
        _download(url, marker)


def _download(url: str, marker: Path) -> None:
    import requests

    part = paths.DATA / "bundle.tar.gz.part"
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(part, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    with tarfile.open(part, "r:gz") as tf:
        for m in tf.getmembers():
            if m.name in FILES and m.isfile():  # only the known files, no paths
                tf.extract(m, paths.DATA, filter="data")
    part.unlink()
    marker.write_text(url)
