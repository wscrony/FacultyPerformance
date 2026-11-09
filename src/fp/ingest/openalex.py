"""Affiliation history from OpenAlex, via the DOIs of a person's own DBLP papers.

We never trust OpenAlex author clustering (it merges namesakes). For each tracked person we take their most
recent DOI papers (+ the earliest one, a hint of where they did the PhD), fetch each work by DOI (singleton
lookups: free, no API key), and read the institutions on the authorship at the person's DBLP author position,
accepted only if the OpenAlex author name matches the DBLP name.

Rows land in the encrypted PII DB (pii.affiliation): it is per-person investigation output.
"""

from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests

from fp.ingest.csrankings import USER_AGENT

API = "https://api.openalex.org/works/doi:{doi}?select=doi,publication_year,authorships"
REFRESH_DAYS = 180

PII_SCHEMA = """
CREATE TABLE IF NOT EXISTS pii.oa_work (
    doi        VARCHAR PRIMARY KEY,
    status     VARCHAR,           -- ok / not_found / error
    year       INTEGER,
    authorships JSON,             -- [{pos, name, institutions:[{name, country, ror}]}]
    fetched_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS pii.affiliation (
    person      VARCHAR,
    year        INTEGER,
    institution VARCHAR,
    country     VARCHAR,
    ror         VARCHAR,
    doi         VARCHAR,
    source      VARCHAR,          -- openalex
    fetched_at  TIMESTAMP DEFAULT current_timestamp
);
"""


def _norm(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\s+\d{4}$", "", s)
    return [t for t in re.split(r"[^a-z]+", s) if t]


def name_match(dblp: str, oa: str) -> bool:
    """Same surname and compatible given names: 'Wook-Shin Han' ~ 'Wookshin Han' ~ 'W.-S. Han' ~ 'Han Wook-Shin'."""
    a, b = _norm(dblp), _norm(oa)
    if len(a) < 2 or not b or a[-1] not in b:
        return False
    rest = list(b)
    rest.remove(a[-1])
    ga, gb = "".join(a[:-1]), "".join(rest)
    if not gb:
        return True
    initials = "".join(t[0] for t in a[:-1])
    return ga == gb or gb == initials or (gb[0] == ga[0] and (ga.startswith(gb) or gb.startswith(ga)))


def parse_work(js: dict) -> tuple[int | None, list[dict]]:
    out = []
    for i, a in enumerate(js.get("authorships") or []):
        insts = [
            {"name": x.get("display_name"), "country": (x.get("country_code") or "").lower() or None,
             "ror": x.get("ror")}
            for x in a.get("institutions") or [] if x.get("display_name")
        ]
        out.append({"pos": i + 1, "name": (a.get("author") or {}).get("display_name") or "", "institutions": insts})
    return js.get("publication_year"), out


def fetch_work(doi: str, sess: requests.Session) -> tuple[str, int | None, list[dict]]:
    for attempt in range(4):
        try:
            r = sess.get(API.format(doi=requests.utils.quote(doi, safe="/")), timeout=30)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 404:
            return "not_found", None, []
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "10") or 10))
            continue
        if r.ok:
            y, au = parse_work(r.json())
            return "ok", y, au
        time.sleep(2 * (attempt + 1))
    return "error", None, []


def targets(con, per_person: int = 3, force: bool = False) -> list[tuple[str, int, str]]:
    """(person, dblp author position, doi) for tracked people: Korean pool + KR faculty."""
    recent = f"""SELECT DISTINCT person FROM pii.affiliation
                 WHERE fetched_at > now() - INTERVAL {REFRESH_DAYS} DAY"""
    return con.execute(
        f"""
        WITH people AS (
            SELECT f.name AS person FROM cs_faculty f JOIN institution i ON i.institution = f.affiliation
            WHERE i.country = 'kr'
            UNION SELECT person FROM pii.korean WHERE is_korean),
        papers AS (
            SELECT a.name AS person, a.pos, p.doi, p.year,
                   row_number() OVER (PARTITION BY a.name ORDER BY p.year DESC, p.key) AS rn_new,
                   row_number() OVER (PARTITION BY a.name ORDER BY p.year, p.key) AS rn_old
            FROM dblp_authorship a JOIN dblp_paper p USING (key)
            WHERE p.doi IS NOT NULL AND a.name IN (SELECT person FROM people))
        SELECT person, pos, doi FROM papers
        WHERE (rn_new <= ? OR rn_old = 1)
          AND person NOT IN (SELECT person FROM pii.forget)
          AND (? OR person NOT IN ({recent}))
        ORDER BY person, doi
        """,
        [per_person, force],
    ).fetchall()


def update(
    con,
    per_person: int = 3,
    force: bool = False,
    limit: int | None = None,
    workers: int = 4,
    fetch: Callable[[str, requests.Session], tuple[str, int | None, list[dict]]] = fetch_work,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    import json

    con.execute(PII_SCHEMA)
    rows = targets(con, per_person, force)
    people = sorted({r[0] for r in rows})
    if limit:
        keep = set(people[:limit])
        rows = [r for r in rows if r[0] in keep]
        people = sorted(keep)
    have = {r[0] for r in con.execute("SELECT doi FROM pii.oa_work WHERE status <> 'error'").fetchall()}
    need = sorted({d for _, _, d in rows} - have)

    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    stats = {"people": len(people), "works_needed": len(need), "ok": 0, "not_found": 0, "error": 0}

    def one(doi):
        return doi, *fetch(doi, sess)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for doi, status, year, au in ex.map(one, need):
            stats[status] += 1
            con.execute(
                "INSERT OR REPLACE INTO pii.oa_work (doi, status, year, authorships) VALUES (?, ?, ?, ?)",
                [doi, status, year, json.dumps(au)],
            )
            done += 1
            if progress and (done % 25 == 0 or done == len(need)):
                progress(done, len(need))

    # resolve each (person, pos, doi) to institutions
    works = {d: (y, json.loads(a or "[]")) for d, y, a in con.execute(
        "SELECT doi, year, authorships FROM pii.oa_work WHERE status = 'ok' AND doi IN (SELECT unnest(?))",
        [[d for _, _, d in rows]]).fetchall()}
    out, matched = [], set()
    for person, pos, doi in rows:
        if doi not in works:
            continue
        year, au = works[doi]
        cand = [a for a in au if a["pos"] == pos and name_match(person, a["name"])]
        if not cand:  # author order differs in OpenAlex: fall back to a unique name match
            cand = [a for a in au if name_match(person, a["name"])]
            if len(cand) != 1:
                continue
        matched.add(person)
        for inst in cand[0]["institutions"]:
            out.append((person, year, inst["name"], inst["country"], inst["ror"], doi))
    con.execute("BEGIN")
    con.execute("DELETE FROM pii.affiliation WHERE person IN (SELECT unnest(?))", [people])
    con.executemany(
        "INSERT INTO pii.affiliation (person, year, institution, country, ror, doi, source) "
        "VALUES (?, ?, ?, ?, ?, ?, 'openalex')", out)
    con.execute("COMMIT")
    stats.update({"people_with_affiliation": len({o[0] for o in out}), "people_matched": len(matched)})
    return stats
