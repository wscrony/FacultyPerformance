"""ORCID public API → degree years (PhD/MS/BS). Raw responses stay inside encrypted pii.duckdb.

One call per person: GET https://pub.orcid.org/v3.0/{id}/activities (educations + qualifications).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import requests

API = "https://pub.orcid.org/v3.0/{orcid}/activities"
USER_AGENT = "FacultyPerformance/0.1 (research analytics)"
REFRESH_DAYS = 90

_PHD = re.compile(r"\bph\.?\s?d|doctor|\bd\.?phil|\bdr\.|박사|\bsc\.?d\b", re.I)
_MS = re.compile(r"master|\bm\.?\s?s\.?c?\b|\bm\.?eng\b|\bm\.?phil\b|석사|\bm\.a\.?|\bma\b", re.I)
_BS = re.compile(r"bachelor|\bb\.?\s?s\.?c?\b|\bb\.?eng\b|\bb\.a\.?|\bba\b|학사|undergrad", re.I)
_NOT_DEGREE = re.compile(r"post-?doc|visiting|exchange|researcher|fellow", re.I)


def valid_orcid(s: str | None) -> bool:
    """Format + ISO 7064 MOD 11-2 checksum; rejects placeholders like 0000-0000-0000-0000."""
    if not s or not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", s.strip()):
        return False
    digits = s.replace("-", "")
    if set(digits[:-1]) == {"0"}:
        return False
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


def classify(title: str | None) -> str | None:
    if not title or _NOT_DEGREE.search(title):
        return None
    if _PHD.search(title):
        return "phd"
    if _MS.search(title):
        return "ms"
    if _BS.search(title):
        return "bs"
    return None


class _Unavailable(Exception):
    pass


@dataclass
class Degree:
    degree: str
    year: int | None
    institution: str | None
    evidence: str


def parse_activities(body: dict) -> list[Degree]:
    out: list[Degree] = []
    for section, key in (("educations", "education-summary"), ("qualifications", "qualification-summary")):
        groups = ((body.get(section) or {}).get("affiliation-group")) or []
        for g in groups:
            for s in g.get("summaries", []):
                e = s.get(key) or {}
                title = e.get("role-title")
                deg = classify(title) or classify(e.get("department-name"))
                if not deg:
                    continue
                end = ((e.get("end-date") or {}).get("year") or {}).get("value")
                org = (e.get("organization") or {}).get("name")
                out.append(Degree(deg, int(end) if end else None, org, f"{section}: {title} @ {org} end={end}"))
    return out


def best_per_degree(degs: Iterable[Degree]) -> dict[str, Degree]:
    """Earliest dated completion per degree type (first PhD counts)."""
    best: dict[str, Degree] = {}
    for d in degs:
        cur = best.get(d.degree)
        if cur is None or (d.year and (cur.year is None or d.year < cur.year)):
            best[d.degree] = d
    return best


def targets(con, scope: str, force: bool) -> list[tuple[str, str]]:
    """(person, orcid) pairs to fetch.

    scope kr     = faculty at KR institutions (ORCID from csrankings)
    scope korean = Korean candidate pool incl. non-faculty (ORCID from csrankings, else DBLP's author attribute)
    scope world  = all csrankings faculty
    """
    recent = f"""SELECT person FROM pii.fetch_log WHERE source = 'orcid'
                 AND fetched_at > now() - INTERVAL {REFRESH_DAYS} DAY"""
    if scope == "korean":
        have_ao = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'author_orcid'").fetchone()[0]
        ao = "author_orcid" if have_ao else "(SELECT NULL::VARCHAR AS name, NULL::VARCHAR AS orcid WHERE false)"
        q = f"""
        WITH people AS (
            SELECT f.name FROM cs_faculty f JOIN institution i ON i.institution = f.affiliation WHERE i.country = 'kr'
            UNION SELECT person FROM pii.korean WHERE is_korean)
        SELECT p.name, coalesce(nullif(trim(any_value(f.orcid)), ''), any_value(a.orcid))
        FROM people p LEFT JOIN cs_faculty f ON f.name = p.name LEFT JOIN {ao} a ON a.name = p.name
        WHERE p.name NOT IN (SELECT person FROM pii.forget) AND (? OR p.name NOT IN ({recent}))
        GROUP BY p.name ORDER BY 1"""
    else:
        where = "i.country = 'kr'" if scope == "kr" else "TRUE"
        q = f"""SELECT DISTINCT f.name, trim(f.orcid) FROM cs_faculty f
            JOIN institution i ON i.institution = f.affiliation
            WHERE {where} AND f.name NOT IN (SELECT person FROM pii.forget)
              AND (? OR f.name NOT IN ({recent}))
            ORDER BY 1"""
    rows = con.execute(q, [force]).fetchall()
    # people with no ORCID at all are skipped silently (nothing to fetch)
    return [(n, o or "") for n, o in rows if scope != "korean" or o]


def update(
    con,
    scope: str = "kr",
    force: bool = False,
    limit: int | None = None,
    sleep: float = 0.25,
    fetch: Callable[[str], dict] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _fetch(oid: str) -> dict:
        for attempt in range(4):
            r = sess.get(API.format(orcid=oid), timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2**attempt * 2)
                continue
            if r.status_code in (404, 409, 410):  # missing / deactivated / deprecated record
                return {"_unavailable": r.status_code}
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return {}

    fetch = fetch or _fetch
    todo = targets(con, scope, force)[: limit or None]
    stats = {"targets": len(todo), "unavailable": 0, "invalid_id": 0, "ok": 0, "empty": 0, "error": 0, "phd": 0}
    for n, (person, oid) in enumerate(todo, 1):
        if progress:
            progress(n, len(todo))
        if not valid_orcid(oid):
            status = "invalid_id"
        else:
            try:
                body = fetch(oid)
                if "_unavailable" in body:
                    raise _Unavailable()
                degs = best_per_degree(parse_activities(body))
                con.execute("BEGIN")
                con.execute("DELETE FROM pii.raw WHERE person=? AND source='orcid'", [person])
                con.execute(
                    "INSERT INTO pii.raw (person, source, ref, body) VALUES (?, 'orcid', ?, ?)",
                    [person, oid, json.dumps(body)],
                )
                con.execute("DELETE FROM pii.degree WHERE person=? AND source='orcid'", [person])
                for d in degs.values():
                    con.execute(
                        """INSERT INTO pii.degree (person, degree, year, institution, source, confidence, evidence)
                           VALUES (?, ?, ?, ?, 'orcid', ?, ?)""",
                        [person, d.degree, d.year, d.institution, 0.9 if d.year else 0.3, d.evidence],
                    )
                con.execute("COMMIT")
                status = "ok" if degs else "empty"
                stats["phd"] += int("phd" in degs and degs["phd"].year is not None)
            except _Unavailable:
                status = "unavailable"
            except Exception:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
                status = "error"
            if sleep:
                time.sleep(sleep)
        stats[status] += 1
        con.execute("INSERT INTO pii.fetch_log (person, source, status) VALUES (?, 'orcid', ?)", [person, status])
    return stats
