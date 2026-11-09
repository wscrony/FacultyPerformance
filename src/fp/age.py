"""Academic age + birth-year estimate + cohort percentile. Requires a pii.connect() connection.

Resolution order (docs/SPEC.md §8.3):
  phd_year : manual > orcid > homepage   (else estimated as first top-venue year + PHD_AFTER_FIRST_TOP)
  birth    : manual/homepage birth year  > phd-30 > bs-23 > first top-venue year - 27
All views are TEMP so nothing derived from PII is persisted into public.duckdb.
"""

from __future__ import annotations

from datetime import date

from fp import metrics

PHD_AFTER_FIRST_TOP = 3
BIRTH_BEFORE = {"phd": 30, "bs": 23, "first_top": 27}
SOURCE_RANK = "CASE source WHEN 'manual' THEN 0 WHEN 'orcid' THEN 1 WHEN 'homepage' THEN 2 ELSE 9 END"


def _ensure_author_pub(con) -> None:
    """author_pub comes from `fp update dblp`; before that, use an empty stand-in so views still work."""
    have = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'author_pub' AND database_name = current_database()"
    ).fetchone()[0]
    if not have:
        con.execute(
            """CREATE TEMP TABLE IF NOT EXISTS author_pub (name VARCHAR, venue VARCHAR, year INTEGER, count INTEGER,
               adjustedcount DOUBLE, first_count INTEGER, last_count INTEGER)"""
        )


def create_views(con, this_year: int | None = None) -> None:
    y = this_year or date.today().year
    _ensure_author_pub(con)
    con.execute(
        f"""
    CREATE OR REPLACE TEMP VIEW deg_best AS
    SELECT person, degree, year, institution, source, confidence FROM (
        SELECT *, row_number() OVER (PARTITION BY person, degree
                    ORDER BY {SOURCE_RANK}, year IS NULL, confidence DESC, year) AS rn
        FROM pii.degree WHERE year IS NOT NULL) WHERE rn = 1;

    CREATE OR REPLACE TEMP VIEW birth_best AS
    SELECT person, year, source, confidence FROM (
        SELECT *, row_number() OVER (PARTITION BY person
                    ORDER BY {SOURCE_RANK}, confidence DESC) AS rn FROM pii.birth) WHERE rn = 1;

    CREATE OR REPLACE TEMP VIEW first_top AS
    SELECT name AS person, min(year) AS first_top_year
    FROM (SELECT name, year FROM cs_pub UNION ALL SELECT name, year FROM author_pub) GROUP BY 1;

    -- everyone we track: csrankings faculty + people classified Korean in the candidate pool
    CREATE OR REPLACE TEMP VIEW person_base AS
    SELECT name, first(affiliation) AS affiliation FROM cs_faculty GROUP BY name
    UNION ALL
    SELECT k.person, NULL FROM pii.korean k
    WHERE k.is_korean AND k.person NOT IN (SELECT name FROM cs_faculty);

    CREATE OR REPLACE TEMP VIEW person_role AS
    SELECT b.name AS person, b.affiliation, i.country,
           CASE WHEN i.country = 'kr' THEN 'KR faculty'
                WHEN b.affiliation IS NOT NULL THEN 'faculty abroad'
                ELSE 'non-faculty/unknown' END AS role,
           coalesce(k.is_korean, i.country = 'kr') AS is_korean
    FROM person_base b
    LEFT JOIN institution i ON i.institution = b.affiliation
    LEFT JOIN pii.korean k ON k.person = b.name;

    CREATE OR REPLACE TEMP VIEW person_age AS
    SELECT f.name AS person, f.affiliation,
           phd.year AS phd_year, phd.institution AS phd_institution, phd.source AS phd_source,
           ft.first_top_year,
           coalesce(phd.year, ft.first_top_year + {PHD_AFTER_FIRST_TOP}) AS phd_year_eff,
           CASE WHEN phd.year IS NOT NULL THEN phd.source
                WHEN ft.first_top_year IS NOT NULL THEN 'est:first_top' END AS age_source,
           {y} - coalesce(phd.year, ft.first_top_year + {PHD_AFTER_FIRST_TOP}) AS academic_age,
           coalesce(b.year,
                    phd.year - {BIRTH_BEFORE['phd']},
                    bs.year - {BIRTH_BEFORE['bs']},
                    ft.first_top_year - {BIRTH_BEFORE['first_top']}) AS birth_year,
           CASE WHEN b.year IS NOT NULL THEN b.source
                WHEN phd.year IS NOT NULL THEN 'est:phd'
                WHEN bs.year IS NOT NULL THEN 'est:bs'
                WHEN ft.first_top_year IS NOT NULL THEN 'est:first_top' END AS birth_source
    FROM person_base f
    LEFT JOIN deg_best phd ON phd.person = f.name AND phd.degree = 'phd'
    LEFT JOIN deg_best bs  ON bs.person  = f.name AND bs.degree  = 'bs'
    LEFT JOIN birth_best b ON b.person   = f.name
    LEFT JOIN first_top ft ON ft.person  = f.name
    WHERE f.name NOT IN (SELECT person FROM pii.forget);
    """
    )


SCOPES = {
    "kr": "KR faculty",
    "korean": "Korean researchers anywhere (KR faculty + faculty abroad + non-faculty)",
    "world": "all CSrankings faculty",
}


def _pub_where(f: metrics.Filter, alias: str = "p") -> tuple[str, list]:
    """Year/venue/area filter for author_pub (no institution join)."""
    conds, params = [f"{alias}.year BETWEEN ? AND ?"], [f.start, f.end]
    if f.venues:
        conds.append(f"v.venue IN (SELECT unnest(?))")
        params.append(f.venues)
    else:
        if not f.next_tier:
            conds.append("NOT v.next_tier")
        if f.areas:
            conds.append("v.area IN (SELECT unnest(?))")
            params.append(f.areas)
    return " AND ".join(conds), params


def candidates(
    con,
    f: metrics.Filter,
    age_min: int | None = None,
    age_max: int | None = None,
    min_adj: float = 0.0,
    cohort_window: int = 2,
    limit: int | None = 100,
    scope: str = "kr",
):
    """People with output in the filter window, academic age, and percentile vs KR faculty of similar age.

    Counts come from author_pub (all authors, incl. first/last-author counts) when loaded, else cs_pub.
    Percentile population: KR faculty with >=1 paper (any year) in the selected areas, academic age within
    ±cohort_window, measured on adjusted count in the same window.
    """
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {list(SCOPES)}")
    create_views(con)
    have_ap = con.execute("SELECT count(*) > 0 FROM (SELECT 1 FROM author_pub LIMIT 1)").fetchone()[0]
    src = "author_pub" if have_ap else (
        "(SELECT name, venue, year, count, adjustedcount, 0 AS first_count, 0 AS last_count FROM cs_pub)")
    win, wparams = _pub_where(f)
    ever_f = metrics.Filter(**{**f.__dict__, "start": 0, "end": 9999})
    ever, eparams = _pub_where(ever_f)
    scope_sql = {
        "kr": "r.country = 'kr'",
        "korean": "r.is_korean",
        "world": "r.affiliation IS NOT NULL",
    }[scope]
    school_sql, sparams = ("AND r.affiliation IN (SELECT unnest(?))", [f.schools]) if f.schools else ("", [])
    q = f"""
    WITH sel AS (
        SELECT p.name, sum(p.count) AS pubs, sum(p.adjustedcount) AS adj,
               sum(p.first_count) AS first_au, sum(p.last_count) AS last_au
        FROM {src} p JOIN venue v ON v.venue = p.venue WHERE {win} GROUP BY 1
    ),
    kr AS (SELECT person FROM person_role WHERE country = 'kr'),
    pop AS (
        SELECT k.person AS name, coalesce(w.adj, 0) AS adj, a.academic_age
        FROM kr k
        JOIN (SELECT DISTINCT p.name FROM {src} p JOIN venue v ON v.venue = p.venue WHERE {ever}) e ON e.name = k.person
        LEFT JOIN (SELECT p.name, sum(p.adjustedcount) AS adj FROM {src} p JOIN venue v ON v.venue = p.venue
                   WHERE {win} GROUP BY 1) w ON w.name = k.person
        JOIN person_age a ON a.person = k.person
        WHERE a.academic_age IS NOT NULL
    )
    SELECT s.name, coalesce(r.affiliation, '-') AS institution, r.role,
           a.academic_age, a.phd_year, a.age_source, a.birth_year, a.birth_source,
           s.pubs, round(s.adj, 2) AS adj, s.first_au, s.last_au,
           (SELECT round(100.0 * avg(CASE WHEN q.adj < s.adj THEN 1 WHEN q.adj = s.adj THEN 0.5 ELSE 0 END), 0)
              FROM pop q WHERE abs(q.academic_age - a.academic_age) <= {int(cohort_window)}) AS cohort_pctl,
           (SELECT count(*) FROM pop q WHERE abs(q.academic_age - a.academic_age) <= {int(cohort_window)}) AS cohort_n
    FROM sel s
    JOIN person_role r ON r.person = s.name
    LEFT JOIN person_age a ON a.person = s.name
    WHERE {scope_sql} {school_sql}
      AND s.adj >= ?
      AND (? IS NULL OR a.academic_age >= ?)
      AND (? IS NULL OR a.academic_age <= ?)
    ORDER BY s.adj DESC, s.name
    {f"LIMIT {int(limit)}" if limit else ""}
    """
    rows = con.execute(
        q, wparams + eparams + wparams + sparams + [min_adj, age_min, age_min, age_max, age_max]
    ).fetchall()
    cols = [
        "name", "institution", "role", "acad_age", "phd_year", "age_src", "birth_year", "birth_src",
        "pubs", "adj", "first_au", "last_au", "cohort_pctl", "cohort_n",
    ]
    return rows, cols


PII_COLUMNS = {"acad_age", "phd_year", "age_src", "birth_year", "birth_src"}
