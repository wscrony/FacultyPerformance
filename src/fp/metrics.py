"""Aggregations over cs_pub. School score replicates CSrankings computeStats():
score(dept) = (Π_{selected parent areas} (paper_count(area, dept) + 1)) ^ (1 / #selected areas).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Filter:
    start: int = 2016
    end: int = 2026
    areas: list[str] | None = None  # parent areas ('mod', 'mlmining', ...); None = all
    next_tier: bool = False  # include next-tier venues (off by default, like CSrankings)
    country: str | None = "kr"  # institution.country; None = world
    schools: list[str] | None = None
    venues: list[str] | None = field(default=None)  # explicit venue override

    def where(self, alias_pub: str = "p", alias_venue: str = "v", alias_inst: str = "i") -> tuple[str, list]:
        conds = [f"{alias_pub}.year BETWEEN ? AND ?"]
        params: list = [self.start, self.end]
        if self.venues:
            conds.append(f"{alias_venue}.venue IN (SELECT unnest(?))")
            params.append(self.venues)
        else:
            if not self.next_tier:
                conds.append(f"NOT {alias_venue}.next_tier")
            if self.areas:
                conds.append(f"{alias_venue}.area IN (SELECT unnest(?))")
                params.append(self.areas)
        if self.country:
            conds.append(f"{alias_inst}.country = ?")
            params.append(self.country)
        if self.schools:
            conds.append(f"{alias_pub}.dept IN (SELECT unnest(?))")
            params.append(self.schools)
        return " AND ".join(conds), params


BASE = """
FROM cs_pub p
JOIN venue v ON v.venue = p.venue
JOIN institution i ON i.institution = p.dept
"""


def all_areas(con) -> list[tuple[str, str, str]]:
    return con.execute(
        "SELECT DISTINCT area, area_title, category FROM venue ORDER BY category, area"
    ).fetchall()


def school_ranking(con, f: Filter):
    """Rows: institution, score, faculty, pubs_total, {area: pubs}.

    score = geometric mean over selected areas of (paper count + 1) — CSrankings' formula with raw paper counts
    instead of adjusted counts (user decision 2026-11-09). A paper co-authored by two faculty of the same school
    counts for each of them.
    """
    where, params = f.where()
    selected = f.areas or [a for a, _, _ in all_areas(con)]
    n = len(selected)
    q = f"""
    WITH agg AS (
        SELECT p.dept, v.area, sum(p.count) AS n, count(DISTINCT p.name) AS fac
        {BASE} WHERE {where} GROUP BY ALL
    ),
    grid AS (
        SELECT d.dept, a.area, coalesce(agg.n, 0) AS n
        FROM (SELECT DISTINCT dept FROM agg) d
        CROSS JOIN (SELECT unnest(?) AS area) a
        LEFT JOIN agg USING (dept, area)
    ),
    fac AS (
        SELECT p.dept, count(DISTINCT p.name) AS faculty {BASE} WHERE {where} GROUP BY ALL
    )
    SELECT g.dept AS institution,
           exp(sum(ln(g.n + 1)) / {n}) AS score,
           any_value(fac.faculty) AS faculty,
           sum(g.n)::BIGINT AS pubs_total,
           map_from_entries(list((g.area, g.n::BIGINT) ORDER BY g.n DESC)
                            FILTER (WHERE g.n > 0)) AS by_area
    FROM grid g JOIN fac USING (dept)
    GROUP BY g.dept
    ORDER BY score DESC, institution
    """
    return con.execute(q, params + [selected] + params).fetchall(), [
        "institution",
        "score",
        "faculty",
        "pubs_total",
        "by_area",
    ]


def faculty_table(con, f: Filter, limit: int | None = None):
    """Per-faculty totals within filter."""
    where, params = f.where()
    q = f"""
    WITH per AS (
        SELECT p.name, p.dept, v.area_title, sum(p.count) AS cnt
        {BASE} WHERE {where} GROUP BY ALL
    )
    SELECT name, dept AS institution, sum(cnt) AS pubs,
           arg_max(area_title, cnt) AS top_area,
           map_from_entries(list((area_title, cnt) ORDER BY cnt DESC)) AS by_area
    FROM per GROUP BY name, dept
    ORDER BY pubs DESC, name
    {f"LIMIT {int(limit)}" if limit else ""}
    """
    return con.execute(q, params).fetchall(), ["name", "institution", "pubs", "top_area", "by_area"]


def person_profile(con, name: str, start: int = 1970, end: int = 2100, next_tier: bool = True):
    """Area × year counts for one person (all regions)."""
    f = Filter(start=start, end=end, next_tier=next_tier, country=None)
    where, params = f.where()
    q = f"""
    SELECT v.area_title, p.venue, p.year, sum(p.count) AS cnt
    {BASE} WHERE {where} AND p.name = ?
    GROUP BY ALL ORDER BY p.year, v.area_title, p.venue
    """
    return con.execute(q, params + [name]).fetchall(), ["area", "venue", "year", "pubs"]


def find_people(con, pattern: str, limit: int = 20) -> list[tuple[str, str]]:
    return con.execute(
        """SELECT DISTINCT name, affiliation FROM cs_faculty
           WHERE name ILIKE ? ORDER BY name LIMIT ?""",
        [f"%{pattern}%", limit],
    ).fetchall()


def school_area_matrix(con, f: Filter):
    """Long form: institution, area_title, pubs (within filter)."""
    where, params = f.where()
    q = f"""
    SELECT p.dept AS institution, v.area_title AS area, sum(p.count) AS pubs
    {BASE} WHERE {where} GROUP BY ALL
    """
    return con.execute(q, params).df()


def _has_author_pub(con) -> bool:
    return con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'author_pub'"
    ).fetchone()[0] > 0 and con.execute("SELECT count(*) > 0 FROM (SELECT 1 FROM author_pub LIMIT 1)").fetchone()[0]


def person_series(con, names: list[str], f: Filter):
    """Per person per year per area — whole record (author_pub when loaded, so non-faculty work too)."""
    src = "author_pub" if _has_author_pub(con) else "cs_pub"
    conds, params = ["p.year BETWEEN ? AND ?"], [f.start, f.end]
    if not f.next_tier:
        conds.append("NOT v.next_tier")
    if f.venues:
        conds.append("v.venue IN (SELECT unnest(?))")
        params.append(f.venues)
    elif f.areas:
        conds.append("v.area IN (SELECT unnest(?))")
        params.append(f.areas)
    q = f"""
    SELECT p.name, p.year, v.area_title AS area, sum(p.count) AS pubs, sum(p.adjustedcount) AS adj
    FROM {src} p JOIN venue v ON v.venue = p.venue
    WHERE {" AND ".join(conds)} AND p.name IN (SELECT unnest(?)) GROUP BY ALL ORDER BY p.year
    """
    return con.execute(q, params + [names]).df()


def people(con, country: str | None = "kr") -> list[tuple[str, str]]:
    q = """SELECT DISTINCT f.name, f.affiliation FROM cs_faculty f
           JOIN institution i ON i.institution = f.affiliation
           WHERE (? IS NULL OR i.country = ?) ORDER BY f.name"""
    return con.execute(q, [country, country]).fetchall()


def snapshots(con):
    return con.execute("SELECT source, version, fetched_at FROM snapshot ORDER BY fetched_at DESC").df()
