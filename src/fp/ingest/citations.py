"""Citation counts for top-venue papers (by DOI) from OpenAlex, 100 DOIs per list request.

Paper-level citations are public, not person data → public DB table oa_cites.
Budget: anonymous list requests share a $0.10/day budget per IP (~1000 requests = ~100k DOIs).
A free key (env FP_OPENALEX_KEY, or Keychain service 'FacultyPerformance' account 'openalex') raises it to $1/day.
On budget exhaustion we stop cleanly; the next run resumes (cached DOIs are skipped).
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable

import requests

from fp.ingest.csrankings import USER_AGENT

API = "https://api.openalex.org/works"
CHUNK = 100
REFRESH_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_cites (
    doi        VARCHAR PRIMARY KEY,
    cited_by   INTEGER,           -- NULL = DOI not in OpenAlex
    fetched_at TIMESTAMP DEFAULT current_timestamp
);
"""


class BudgetExhausted(Exception):
    pass


def api_key() -> str | None:
    if os.environ.get("FP_OPENALEX_KEY"):
        return os.environ["FP_OPENALEX_KEY"]
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "FacultyPerformance", "-a", "openalex", "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def fetch_chunk(dois: list[str], sess: requests.Session, key: str | None) -> dict[str, int]:
    params = {"filter": "doi:" + "|".join(dois), "select": "doi,cited_by_count", "per_page": str(CHUNK)}
    if key:
        params["api_key"] = key
    for attempt in range(5):
        try:
            r = sess.get(API, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if body.get("dailyRemainingUsd") == 0 and body.get("prepaidRemainingUsd", 0) == 0 and \
                    "per second" not in body.get("message", ""):
                raise BudgetExhausted(body.get("message", "daily budget exhausted"))
            time.sleep(max(1.0, float(body.get("retryAfter") or 1)))
            continue
        if r.ok:
            out = {}
            for w in r.json().get("results", []):
                d = (w.get("doi") or "").lower().removeprefix("https://doi.org/")
                if d:
                    out[d] = int(w.get("cited_by_count") or 0)
            return out
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OpenAlex list request failed ({r.status_code})")


def targets(con, force: bool = False) -> list[str]:
    """DOIs of all top-venue papers by tracked people (KR faculty + Korean pool)."""
    return [r[0] for r in con.execute(
        f"""
        WITH people AS (
            SELECT f.name AS person FROM cs_faculty f JOIN institution i ON i.institution = f.affiliation
            WHERE i.country = 'kr'
            UNION SELECT person FROM pii.korean WHERE is_korean)
        SELECT DISTINCT p.doi FROM dblp_authorship a JOIN dblp_paper p USING (key)
        WHERE p.doi IS NOT NULL AND a.name IN (SELECT person FROM people)
          AND (? OR p.doi NOT IN (SELECT doi FROM oa_cites
                                  WHERE fetched_at > now() - INTERVAL {REFRESH_DAYS} DAY))
        ORDER BY 1
        """,
        [force],
    ).fetchall()]


def update(
    con,
    force: bool = False,
    limit: int | None = None,
    fetch: Callable[[list[str], requests.Session, str | None], dict[str, int]] = fetch_chunk,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    con.execute(SCHEMA)
    dois = targets(con, force)
    if limit:
        dois = dois[:limit]
    key = api_key()
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    stats = {"dois": len(dois), "found": 0, "missing": 0, "requests": 0, "api_key": bool(key), "stopped": None}
    for b in range(0, len(dois), CHUNK):
        chunk = dois[b : b + CHUNK]
        try:
            got = fetch(chunk, sess, key)
        except BudgetExhausted as e:
            stats["stopped"] = f"budget exhausted after {b} DOIs: {e}"
            break
        stats["requests"] += 1
        con.executemany(
            "INSERT OR REPLACE INTO oa_cites (doi, cited_by, fetched_at) VALUES (?, ?, now())",
            [(d, got.get(d)) for d in chunk],
        )
        stats["found"] += sum(d in got for d in chunk)
        stats["missing"] += sum(d not in got for d in chunk)
        if progress:
            progress(min(b + CHUNK, len(dois)), len(dois))
        time.sleep(0.15)
    return stats


# ── papers without a DOI (NeurIPS, most ICLR/ICML, USENIX): match by title on OpenAlex ──
TITLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_title_cites (
    key        VARCHAR PRIMARY KEY,   -- dblp key
    oa_ids     VARCHAR,               -- matched OpenAlex works (arXiv / proceedings versions)
    cited_by   INTEGER,               -- max over matched versions; NULL = no exact title match
    fetched_at TIMESTAMP DEFAULT current_timestamp
);
"""


def norm_title(t: str | None) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def fetch_title(title: str, year: int | None, sess: requests.Session, key: str | None) -> tuple[str | None, int | None]:
    """Exact (normalized) title match within ±1 year; versions are separate OpenAlex works → take the max
    (never sum: that would double count)."""
    import re

    q = re.sub(r"[^\w\s-]", " ", title).strip()
    flt = f"title.search:{q}" + (f",publication_year:{year - 1}-{year + 1}" if year else "")
    params = {"filter": flt, "select": "id,display_name,cited_by_count", "per_page": "10"}
    if key:
        params["api_key"] = key
    for attempt in range(5):
        try:
            r = sess.get(API, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if body.get("dailyRemainingUsd") == 0 and body.get("prepaidRemainingUsd", 0) == 0 and \
                    "per second" not in body.get("message", ""):
                raise BudgetExhausted(body.get("message", "daily budget exhausted"))
            time.sleep(max(1.0, float(body.get("retryAfter") or 1)))
            continue
        if r.status_code == 400:
            return None, None
        if r.ok:
            want = norm_title(title)
            hits = [w for w in r.json().get("results", []) if norm_title(w.get("display_name")) == want]
            if not hits:
                return None, None
            return ",".join(w["id"].rsplit("/", 1)[-1] for w in hits), max(int(w.get("cited_by_count") or 0)
                                                                          for w in hits)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OpenAlex title search failed ({r.status_code})")


def title_targets(con, force: bool = False) -> list[tuple[str, str, int]]:
    return con.execute(
        f"""
        WITH people AS (
            SELECT f.name AS person FROM cs_faculty f JOIN institution i ON i.institution = f.affiliation
            WHERE i.country = 'kr'
            UNION SELECT person FROM pii.korean WHERE is_korean)
        SELECT DISTINCT p.key, p.title, p.year FROM dblp_authorship a JOIN dblp_paper p USING (key)
        WHERE p.doi IS NULL AND p.title IS NOT NULL AND a.name IN (SELECT person FROM people)
          AND (? OR p.key NOT IN (SELECT key FROM oa_title_cites
                                  WHERE fetched_at > now() - INTERVAL {REFRESH_DAYS} DAY))
        ORDER BY p.year DESC, p.key
        """,
        [force],
    ).fetchall()


def update_titles(con, force: bool = False, limit: int | None = None, fetch=fetch_title, progress=None) -> dict:
    con.execute(TITLE_SCHEMA)
    rows = title_targets(con, force)[:limit] if limit else title_targets(con, force)
    key = api_key()
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    stats = {"papers": len(rows), "matched": 0, "unmatched": 0, "stopped": None}
    buf = []
    for i, (k, title, year) in enumerate(rows):
        try:
            ids, cites = fetch(title, year, sess, key)
        except BudgetExhausted as e:
            stats["stopped"] = f"budget exhausted after {i} papers: {e}"
            break
        buf.append((k, ids, cites))
        stats["matched" if ids else "unmatched"] += 1
        if len(buf) >= 50 or i == len(rows) - 1:
            con.executemany("INSERT OR REPLACE INTO oa_title_cites (key, oa_ids, cited_by, fetched_at) "
                            "VALUES (?, ?, ?, now())", buf)
            buf = []
            if progress:
                progress(i + 1, len(rows))
        time.sleep(0.12)
    if buf:
        con.executemany("INSERT OR REPLACE INTO oa_title_cites (key, oa_ids, cited_by, fetched_at) "
                        "VALUES (?, ?, ?, now())", buf)
    return stats
