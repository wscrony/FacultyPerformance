"""Data for the analysis boards (pure functions over a DuckDB cursor; the Streamlit pages only draw).

Institution credit uses CSrankings' convention: a faculty member's papers count for their *current*
institution. A paper is credited to an institution once if any of its authors is faculty there ('any'), or only
when the last author (usually the PI) is ('last'). Shares are of all papers in the filter, including the ~30%
with no CSrankings-listed faculty author, so shares sum to < 100%.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GROUP_SQL = """CASE WHEN i.country = 'kr' THEN 'Korea' WHEN i.country = 'us' THEN 'United States'
     WHEN i.country = 'cn' THEN 'China' WHEN i.region = 'europe' THEN 'Europe'
     WHEN i.region = 'asia' THEN 'Rest of Asia' ELSE 'Other' END"""


@dataclass
class BF:
    """Board filter."""

    start: int
    end: int
    areas: list[str] | None = None
    venues: list[str] | None = None
    next_tier: bool = False
    credit: str = "any"  # any | last

    def where(self, p: str = "p", v: str = "v") -> tuple[str, list]:
        conds, params = [f"{p}.year BETWEEN ? AND ?"], [self.start, self.end]
        if self.venues:
            conds.append(f"{p}.venue IN (SELECT unnest(?))")
            params.append(self.venues)
        else:
            if not self.next_tier:
                conds.append(f"NOT {v}.next_tier")
            if self.areas:
                conds.append(f"{v}.area IN (SELECT unnest(?))")
                params.append(self.areas)
        return " AND ".join(conds), params

    def credit_sql(self, alias: str = "x") -> str:
        return f"{alias}.is_last" if self.credit == "last" else "TRUE"


def ensure_pinst(con) -> None:
    """Temp table: one row per (paper, faculty author) with the author's institution and region group."""
    have = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'pinst' AND temporary"
    ).fetchone()[0]
    if have:
        return
    con.execute(
        f"""
        CREATE TEMP TABLE pinst AS
        WITH fac AS (SELECT name, any_value(affiliation) AS aff FROM cs_faculty GROUP BY name)
        SELECT p.key, p.venue, p.year, a.pos, a.pos = p.n_authors AS is_last, a.name,
               f.aff AS institution, i.country, {GROUP_SQL} AS grp
        FROM dblp_authorship a JOIN fac f ON f.name = a.name JOIN dblp_paper p USING (key)
        JOIN institution i ON i.institution = f.aff
        """
    )


def total_papers(con, f: BF) -> int:
    w, prm = f.where()
    return con.execute(f"SELECT count(*) FROM dblp_paper p JOIN venue v USING (venue) WHERE {w}", prm).fetchone()[0]


def institution_share(con, f: BF, top: int = 60):
    """Rows: grp, institution, country, papers, share% (of all papers in filter); top N institutions."""
    ensure_pinst(con)
    w, prm = f.where("x")
    tot = total_papers(con, f)
    df = con.execute(
        f"""
        SELECT x.grp, x.institution, any_value(x.country) AS country, count(DISTINCT x.key) AS papers
        FROM pinst x JOIN venue v ON v.venue = x.venue
        WHERE {w} AND {f.credit_sql()} GROUP BY 1, 2 ORDER BY papers DESC, x.institution LIMIT ?
        """,
        prm + [top],
    ).df()
    df["share"] = 100.0 * df.papers / max(tot, 1)
    df["rank"] = range(1, len(df) + 1)
    return df, tot


def group_share(con, f: BF):
    """Distinct-paper share per region group (a paper counts once per group)."""
    ensure_pinst(con)
    w, prm = f.where("x")
    tot = total_papers(con, f)
    df = con.execute(
        f"""SELECT x.grp, count(DISTINCT x.key) AS papers FROM pinst x JOIN venue v ON v.venue = x.venue
            WHERE {w} AND {f.credit_sql()} GROUP BY 1""",
        prm,
    ).df()
    df["share"] = 100.0 * df.papers / max(tot, 1)
    return df


def group_share_by_year(con, f: BF):
    ensure_pinst(con)
    w, prm = f.where("x")
    w2, prm2 = f.where()
    return con.execute(
        f"""
        WITH tot AS (SELECT p.year, count(*) AS n FROM dblp_paper p JOIN venue v USING (venue) WHERE {w2} GROUP BY 1)
        SELECT x.year, x.grp, count(DISTINCT x.key) AS papers, 100.0 * count(DISTINCT x.key) / any_value(t.n) AS share
        FROM pinst x JOIN venue v ON v.venue = x.venue JOIN tot t ON t.year = x.year
        WHERE {w} AND {f.credit_sql()} GROUP BY 1, 2 ORDER BY 1
        """,
        prm2 + prm,
    ).df()


def country_rank(con, f: BF):
    """Countries by distinct papers; Korea's rank is what the KPI row shows."""
    ensure_pinst(con)
    w, prm = f.where("x")
    return con.execute(
        f"""SELECT x.country, count(DISTINCT x.key) AS papers,
                   rank() OVER (ORDER BY count(DISTINCT x.key) DESC) AS rank
            FROM pinst x JOIN venue v ON v.venue = x.venue WHERE {w} AND {f.credit_sql()} GROUP BY 1
            ORDER BY papers DESC""",
        prm,
    ).df()


def kr_school_world_rank_by_year(con, f: BF, schools: list[str]):
    """World rank (by papers) of the given KR schools, per year."""
    ensure_pinst(con)
    w, prm = f.where("x")
    return con.execute(
        f"""
        WITH per AS (SELECT x.year, x.institution, count(DISTINCT x.key) AS papers
                     FROM pinst x JOIN venue v ON v.venue = x.venue WHERE {w} AND {f.credit_sql()} GROUP BY 1, 2),
        r AS (SELECT *, rank() OVER (PARTITION BY year ORDER BY papers DESC) AS rank FROM per)
        SELECT year, institution, papers, rank FROM r WHERE institution IN (SELECT unnest(?)) ORDER BY year
        """,
        prm + [schools],
    ).df()


def kr_area_profile(con, f: BF):
    """Per area: Korea's share of the area's papers, vs Korea's share overall (specialization)."""
    ensure_pinst(con)
    w, prm = f.where("x")
    w2, prm2 = f.where()
    return con.execute(
        f"""
        WITH tot AS (SELECT v.area, any_value(v.area_title) AS title, count(*) AS n
                     FROM dblp_paper p JOIN venue v USING (venue) WHERE {w2} GROUP BY 1),
        kr AS (SELECT v.area, count(DISTINCT x.key) AS kr FROM pinst x JOIN venue v ON v.venue = x.venue
               WHERE {w} AND {f.credit_sql()} AND x.country = 'kr' GROUP BY 1)
        SELECT t.area, t.title, t.n AS papers, coalesce(kr.kr, 0) AS kr_papers,
               100.0 * coalesce(kr.kr, 0) / t.n AS kr_share
        FROM tot t LEFT JOIN kr USING (area) ORDER BY kr_share DESC
        """,
        prm2 + prm,
    ).df()


def kr_venue_year(con, f: BF, top: int = 25):
    """Korea share (%) per venue per year for the venues where Korea publishes most."""
    ensure_pinst(con)
    w, prm = f.where("x")
    w2, prm2 = f.where()
    return con.execute(
        f"""
        WITH tot AS (SELECT p.venue, p.year, count(*) AS n FROM dblp_paper p JOIN venue v USING (venue)
                     WHERE {w2} GROUP BY 1, 2),
        kr AS (SELECT x.venue, x.year, count(DISTINCT x.key) AS kr FROM pinst x JOIN venue v ON v.venue = x.venue
               WHERE {w} AND {f.credit_sql()} AND x.country = 'kr' GROUP BY 1, 2),
        topv AS (SELECT venue FROM kr GROUP BY 1 ORDER BY sum(kr) DESC LIMIT ?)
        SELECT t.venue, t.year, t.n, coalesce(kr.kr, 0) AS kr, 100.0 * coalesce(kr.kr, 0) / t.n AS share
        FROM tot t LEFT JOIN kr USING (venue, year) WHERE t.venue IN (SELECT venue FROM topv)
        """,
        prm2 + prm + [top],
    ).df()


def kr_collab_matrix(con, f: BF, top: int = 15):
    """Papers co-authored by faculty of two different KR schools."""
    ensure_pinst(con)
    w, prm = f.where("x")
    return con.execute(
        f"""
        WITH k AS (SELECT DISTINCT x.key, x.institution FROM pinst x JOIN venue v ON v.venue = x.venue
                   WHERE {w} AND x.country = 'kr'),
        topi AS (SELECT institution FROM k GROUP BY 1 ORDER BY count(*) DESC LIMIT ?),
        pairs AS (SELECT a.institution AS a, b.institution AS b, count(*) AS papers
                  FROM k a JOIN k b ON a.key = b.key AND a.institution <> b.institution
                  WHERE a.institution IN (SELECT * FROM topi) AND b.institution IN (SELECT * FROM topi)
                  GROUP BY 1, 2)
        SELECT * FROM pairs
        """,
        prm + [top],
    ).df()


def kr_schools(con) -> list[str]:
    """KR institutions ordered by all-time paper count (stable order → stable colors)."""
    ensure_pinst(con)
    return [r[0] for r in con.execute(
        "SELECT institution FROM pinst WHERE country = 'kr' GROUP BY 1 ORDER BY count(DISTINCT key) DESC, 1"
    ).fetchall()]


# ── people boards (PII-derived, local app only) ────────────────────────────────


def korean_landscape(con):
    """Korean researchers: role, current country group, academic age (needs age.create_views on the cursor)."""
    return con.execute(
        f"""
        WITH cmap AS (SELECT country, any_value(region) AS region FROM institution GROUP BY 1)
        SELECT r.person, r.role, r.affiliation, r.country, a.academic_age, a.age_source,
               CASE WHEN r.country = 'kr' THEN 'Korea' WHEN r.country = 'us' THEN 'United States'
                    WHEN r.country = 'cn' THEN 'China' WHEN cm.region = 'europe' THEN 'Europe'
                    WHEN cm.region = 'asia' THEN 'Rest of Asia' WHEN r.country IS NULL THEN NULL ELSE 'Other' END AS grp
        FROM person_role r LEFT JOIN person_age a ON a.person = r.person LEFT JOIN cmap cm ON cm.country = r.country
        WHERE r.is_korean
        """
    ).df()


def mobility(con, min_gap: int = 3):
    """Earliest vs latest affiliation region of Korean researchers (OpenAlex, own papers)."""
    return con.execute(
        f"""
        WITH cmap AS (SELECT country, any_value(region) AS region FROM institution GROUP BY 1),
        a AS (SELECT af.person, af.year, af.country,
                     CASE WHEN af.country = 'kr' THEN 'Korea' WHEN af.country = 'us' THEN 'United States'
                          WHEN af.country = 'cn' THEN 'China' WHEN cm.region = 'europe' THEN 'Europe'
                          WHEN cm.region = 'asia' THEN 'Rest of Asia' ELSE 'Other' END AS grp
              FROM pii.affiliation af LEFT JOIN cmap cm ON cm.country = af.country
              WHERE af.country IS NOT NULL
                AND af.person IN (SELECT person FROM person_role WHERE is_korean)),
        ends AS (SELECT person, min(year) AS y0, max(year) AS y1 FROM a GROUP BY 1 HAVING max(year) - min(year) >= ?)
        SELECT e.person,
               (SELECT mode(grp) FROM a WHERE a.person = e.person AND a.year = e.y0) AS start_grp,
               (SELECT mode(grp) FROM a WHERE a.person = e.person AND a.year = e.y1) AS now_grp,
               e.y0, e.y1
        FROM ends e
        """,
        [min_gap],
    ).df()


def kr_faculty_age_output(con, f: BF):
    """KR faculty: academic age vs papers in the filter window."""
    w, prm = f.where("p")
    return con.execute(
        f"""
        WITH out AS (SELECT a.name, count(*) AS papers FROM dblp_authorship a JOIN dblp_paper p USING (key)
                     JOIN venue v ON v.venue = p.venue WHERE {w} GROUP BY 1)
        SELECT r.person, r.affiliation, a.academic_age, a.age_source, coalesce(o.papers, 0) AS papers
        FROM person_role r JOIN person_age a ON a.person = r.person LEFT JOIN out o ON o.name = r.person
        WHERE r.role = 'KR faculty' AND a.academic_age IS NOT NULL
        """,
        prm,
    ).df()


def school_profile(con, school: str, f: BF):
    """Faculty table for one school: age, papers in window, career papers, main area."""
    return con.execute(
        f"""
        WITH fac AS (SELECT DISTINCT name FROM cs_faculty WHERE affiliation = ?),
        pubs AS (SELECT a.name, v.area_title, p.year FROM dblp_authorship a JOIN dblp_paper p USING (key)
                 JOIN venue v ON v.venue = p.venue WHERE a.name IN (SELECT name FROM fac) AND NOT v.next_tier)
        SELECT f.name, pa.academic_age, pa.age_source,
               count(*) FILTER (WHERE pubs.year BETWEEN ? AND ?) AS papers_window,
               count(pubs.year) AS papers_career,
               mode(pubs.area_title) AS main_area
        FROM fac f LEFT JOIN pubs ON pubs.name = f.name LEFT JOIN person_age pa ON pa.person = f.name
        GROUP BY ALL ORDER BY papers_window DESC, f.name
        """,
        [school, f.start, f.end],
    ).df()


def school_trend(con, school: str, f: BF):
    ensure_pinst(con)
    whole = BF(start=0, end=9999, areas=f.areas, venues=f.venues, next_tier=f.next_tier, credit=f.credit)
    w, prm = whole.where("x")
    return con.execute(
        f"""SELECT x.year, any_value(v.area_title) AS area, v.area AS area_code, count(DISTINCT x.key) AS papers
            FROM pinst x JOIN venue v ON v.venue = x.venue
            WHERE {w} AND x.institution = ? GROUP BY x.year, v.area ORDER BY 1""",
        prm + [school],
    ).df()


def area_strategy(con, f: BF, window: int = 3):
    """Per area: world growth (last `window` years vs the `window` before), Korea share in the recent window."""
    ensure_pinst(con)
    end = f.end
    recent, prev = (end - window + 1, end), (end - 2 * window + 1, end - window)
    nt = "" if f.next_tier else "AND NOT v.next_tier"
    return con.execute(
        f"""
        WITH w AS (SELECT v.area, any_value(v.area_title) AS title, any_value(v.category) AS category,
                          count(*) FILTER (WHERE p.year BETWEEN ? AND ?) AS recent,
                          count(*) FILTER (WHERE p.year BETWEEN ? AND ?) AS prev
                   FROM dblp_paper p JOIN venue v USING (venue) WHERE TRUE {nt} GROUP BY 1),
        kr AS (SELECT v.area, count(DISTINCT x.key) AS kr FROM pinst x JOIN venue v ON v.venue = x.venue
               WHERE x.country = 'kr' AND x.year BETWEEN ? AND ? {nt} GROUP BY 1)
        SELECT w.area, w.title, w.category, w.recent, w.prev,
               100.0 * (w.recent - w.prev) / nullif(w.prev, 0) AS growth,
               coalesce(kr.kr, 0) AS kr, 100.0 * coalesce(kr.kr, 0) / nullif(w.recent, 0) AS kr_share
        FROM w LEFT JOIN kr USING (area) WHERE w.recent > 0
        """,
        [*recent, *prev, *recent],
    ).df()


def talent_matrix(con, years: int = 5):
    """Korean researchers active in the last `years`: main area × career stage × location."""
    return con.execute(
        f"""
        WITH act AS (SELECT a.name, v.area_title, sum(a.count) AS n FROM author_pub a JOIN venue v ON v.venue = a.venue
                     WHERE a.year >= year(current_date) - ? AND NOT v.next_tier GROUP BY 1, 2),
        main AS (SELECT name, arg_max(area_title, n) AS area, sum(n) AS papers FROM act GROUP BY 1)
        SELECT r.person, r.role, r.country, m.area, m.papers, a.academic_age,
               CASE WHEN a.academic_age < 0 THEN '① 박사 전 (추정)'
                    WHEN a.academic_age < 5 THEN '② 0–4년' WHEN a.academic_age < 10 THEN '③ 5–9년'
                    WHEN a.academic_age < 20 THEN '④ 10–19년' ELSE '⑤ 20년+' END AS stage
        FROM person_role r JOIN main m ON m.name = r.person LEFT JOIN person_age a ON a.person = r.person
        WHERE r.is_korean AND a.academic_age IS NOT NULL
        """,
        [years],
    ).df()
