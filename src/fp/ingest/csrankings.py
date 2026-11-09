"""Ingest CSrankings data (github.com/emeryberger/CSrankings, CC BY-NC-ND 4.0 — internal use only).

Steps: resolve gh-pages head sha → skip if already loaded → download needed files →
parse venue/area config from src/config.ts → load into public.duckdb → save parquet snapshot.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from fp import db, paths

REPO = "https://github.com/emeryberger/CSrankings"
RAW_BASE = "https://raw.githubusercontent.com/emeryberger/CSrankings/{sha}/{path}"
FILES = [
    "csrankings.csv",
    "generated-author-info.csv",
    "institutions.csv",
    "countries.csv",
    "dblp-aliases.csv",
    "acm-fellows.csv",
    "src/config.ts",
]
USER_AGENT = "FacultyPerformance/0.1 (research analytics)"


@dataclass
class VenueRow:
    venue: str
    area: str
    area_title: str
    category: str
    next_tier: bool


def head_sha() -> str:
    out = subprocess.run(
        ["git", "ls-remote", REPO, "refs/heads/gh-pages"], check=True, capture_output=True, text=True
    ).stdout
    sha = out.split()[0]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"unexpected ls-remote output: {out!r}")
    return sha


def download(sha: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    for f in FILES:
        target = dest / Path(f).name
        if target.exists() and target.stat().st_size > 0:
            continue
        r = sess.get(RAW_BASE.format(sha=sha, path=f), timeout=120)
        r.raise_for_status()
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.rename(target)


def _block(src: str, name: str, open_ch: str, close_ch: str) -> str:
    m = re.search(rf"export const {name}\b[^=]*=\s*{re.escape(open_ch)}", src)
    if not m:
        raise ValueError(f"{name} not found in config.ts")
    start, depth = m.end(), 1
    i = start
    while depth:
        c = src[i]
        depth += c == open_ch
        depth -= c == close_ch
        i += 1
    return src[start : i - 1]


def parse_config(src: str) -> list[VenueRow]:
    """Extract venue→area mapping from CSrankings src/config.ts."""
    src = re.sub(r"//[^\n]*", "", src)
    parent = dict(re.findall(r"'([\w-]+)'\s*:\s*'([\w-]+)'", _block(src, "parentMap", "{", "}")))
    next_tier = set(re.findall(r"'([\w-]+)'\s*:\s*true", _block(src, "nextTier", "{", "}")))
    titles = dict(
        re.findall(r'area:\s*"([\w-]+)"\s*,\s*title:\s*"([^"]+)"', _block(src, "areaMap", "[", "]"))
    )
    cat_of: dict[str, str] = {}
    for var, cat in [
        ("aiAreas", "ai"),
        ("systemsAreas", "systems"),
        ("theoryAreas", "theory"),
        ("interdisciplinaryAreas", "interdisciplinary"),
    ]:
        m = re.search(rf"export const {var}\s*=\s*\[([^\]]*)\]", src)
        if not m:
            raise ValueError(f"{var} not found in config.ts")
        for a in re.findall(r'"([\w-]+)"', m.group(1)):
            cat_of[a] = cat
    rows = []
    for venue, area in parent.items():
        rows.append(
            VenueRow(venue, area, titles.get(area, area), cat_of.get(area, "other"), venue in next_tier)
        )
    if len(rows) < 50:
        raise ValueError(f"config.ts parse looks wrong: only {len(rows)} venues")
    return rows


def load(con, sha: str, src_dir: Path) -> dict[str, int]:
    venues = parse_config((src_dir / "config.ts").read_text())
    con.execute("BEGIN")
    try:
        for t in ("venue", "institution", "cs_faculty", "cs_pub"):
            con.execute(f"DELETE FROM {t}")
        con.executemany(
            "INSERT INTO venue VALUES (?,?,?,?,?)",
            [(v.venue, v.area, v.area_title, v.category, v.next_tier) for v in venues],
        )
        d = str(src_dir)
        con.execute(
            f"""INSERT INTO institution SELECT institution, region, countryabbrv, homepage
                FROM read_csv('{d}/institutions.csv', header=true, all_varchar=true)"""
        )
        con.execute(
            f"""INSERT INTO cs_faculty SELECT name, affiliation, homepage, scholarid, orcid
                FROM read_csv('{d}/csrankings.csv', header=true, all_varchar=true)"""
        )
        con.execute(
            f"""INSERT INTO cs_pub
                SELECT name, dept, area, CAST(year AS INTEGER), CAST(CAST(count AS DOUBLE) AS INTEGER),
                       CAST(adjustedcount AS DOUBLE)
                FROM read_csv('{d}/generated-author-info.csv', header=true, all_varchar=true)"""
        )
        con.execute(
            "INSERT OR REPLACE INTO snapshot VALUES ('csrankings', ?, ?)",
            [sha, datetime.now(timezone.utc).replace(tzinfo=None)],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    snap = paths.SNAPSHOTS / "csrankings" / sha[:12]
    snap.mkdir(parents=True, exist_ok=True)
    for t in ("cs_faculty", "cs_pub"):
        con.execute(f"COPY {t} TO '{snap}/{t}.parquet' (FORMAT parquet)")
    return {
        t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("venue", "institution", "cs_faculty", "cs_pub")
    }


def update(force: bool = False) -> tuple[str, dict[str, int] | None]:
    """Returns (sha, row counts) or (sha, None) if already up to date."""
    sha = head_sha()
    con = db.connect()
    try:
        seen = con.execute(
            "SELECT 1 FROM snapshot WHERE source='csrankings' AND version=?", [sha]
        ).fetchone()
        if seen and not force:
            return sha, None
        src_dir = paths.RAW / "csrankings" / sha[:12]
        download(sha, src_dir)
        counts = load(con, sha, src_dir)
    finally:
        con.close()
    _prune_raw(keep=src_dir)
    return sha, counts


def _prune_raw(keep: Path) -> None:
    """Raw CSVs are ~30MB per commit; keep only the latest (parquet snapshots keep history)."""
    root = paths.RAW / "csrankings"
    for d in root.iterdir():
        if d.is_dir() and d != keep:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
