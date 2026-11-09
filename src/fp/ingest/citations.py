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


def update_titles(con, force: bool = False, limit: int | None = None, fetch=None, progress=None) -> dict:
    """fetch: batched `fetch_titles` by default; a single-title function (title, year, sess, key) also works."""
    con.execute(TITLE_SCHEMA)
    rows = title_targets(con, force)
    rows = rows[:limit] if limit else rows
    key = api_key()
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    stats = {"papers": len(rows), "matched": 0, "unmatched": 0, "requests": 0, "stopped": None}
    batched = fetch is None or fetch is fetch_titles_ref()
    step = TITLE_BATCH if batched else 1
    for b in range(0, len(rows), step):
        chunk = rows[b : b + step]
        try:
            if batched:
                got = fetch_titles(chunk, sess, key)
            else:
                got = {k: fetch(t, y, sess, key) for k, t, y in chunk}
        except BudgetExhausted as e:
            stats["stopped"] = f"budget exhausted after {b} papers: {e}"
            break
        stats["requests"] += 1
        con.executemany("INSERT OR REPLACE INTO oa_title_cites (key, oa_ids, cited_by, fetched_at) "
                        "VALUES (?, ?, ?, now())", [(k, *got.get(k, (None, None))) for k, _, _ in chunk])
        for k, _, _ in chunk:
            stats["matched" if got.get(k, (None,))[0] else "unmatched"] += 1
        if progress:
            progress(min(b + step, len(rows)), len(rows))
        time.sleep(0.12)
    return stats


def fetch_titles_ref():
    return fetch_titles

TITLE_BATCH = 20  # one OR-ed phrase search per batch: $0.001 per request regardless of size


def _phrase(t: str) -> str:
    import re

    return '"' + re.sub(r"\s+", " ", re.sub(r'[^\w\s-]', " ", t)).strip() + '"'


def fetch_titles(items: list[tuple[str, str, int | None]], sess: requests.Session, key: str | None
                 ) -> dict[str, tuple[str | None, int | None]]:
    """Batched exact-title lookup: search = "t1" OR "t2" ...; match locally (normalized title, year ±1).
    Splits the batch when the result set is too large to see in one page."""
    params = {"search": " OR ".join(_phrase(t) for _, t, _ in items),
              "select": "id,display_name,cited_by_count,publication_year", "per_page": "200"}
    if key:
        params["api_key"] = key
    for attempt in range(5):
        try:
            r = sess.get(API, params=params, timeout=90)
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
        if r.status_code == 400 and len(items) > 1:
            h = len(items) // 2
            return {**fetch_titles(items[:h], sess, key), **fetch_titles(items[h:], sess, key)}
        if r.ok:
            js = r.json()
            if js.get("meta", {}).get("count", 0) > 200 and len(items) > 1:
                h = len(items) // 2
                return {**fetch_titles(items[:h], sess, key), **fetch_titles(items[h:], sess, key)}
            by_title: dict[str, list[dict]] = {}
            for w in js.get("results", []):
                by_title.setdefault(norm_title(w.get("display_name")), []).append(w)
            out = {}
            for k, t, y in items:
                hits = [w for w in by_title.get(norm_title(t), [])
                        if y is None or w.get("publication_year") is None or abs(w["publication_year"] - y) <= 1]
                out[k] = ((",".join(w["id"].rsplit("/", 1)[-1] for w in hits),
                           max(int(w.get("cited_by_count") or 0) for w in hits)) if hits else (None, None))
            return out
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OpenAlex batched title search failed ({r.status_code})")
