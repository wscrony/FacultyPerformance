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


def create_views(con, this_year: int | None = None) -> None:
    y = this_year or date.today().year
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
    SELECT name AS person, min(year) AS first_top_year FROM cs_pub GROUP BY 1;

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
    FROM (SELECT DISTINCT name, first(affiliation) AS affiliation FROM cs_faculty GROUP BY name) f
    LEFT JOIN deg_best phd ON phd.person = f.name AND phd.degree = 'phd'
    LEFT JOIN deg_best bs  ON bs.person  = f.name AND bs.degree  = 'bs'
    LEFT JOIN birth_best b ON b.person   = f.name
    LEFT JOIN first_top ft ON ft.person  = f.name
    WHERE f.name NOT IN (SELECT person FROM pii.forget);
    """
    )


def candidates(
    con,
    f: metrics.Filter,
    age_min: int | None = None,
    age_max: int | None = None,
    min_adj: float = 0.0,
    cohort_window: int = 2,
    limit: int | None = 100,
):
    """Faculty within filter with age info and percentile vs KR faculty of similar academic age.

    Percentile population: KR faculty with >=1 paper (any year) in the selected areas/venues,
    academic age within ±cohort_window, measured on adj count within the filter's year range.
    """
    create_views(con)
    where, params = f.where()
    pop = metrics.Filter(**{**f.__dict__, "country": "kr", "schools": None})
    pop_where, pop_params = pop.where()
    pop_ever = metrics.Filter(**{**pop.__dict__, "start": 0, "end": 9999})
    ever_where, ever_params = pop_ever.where()
    q = f"""
    WITH sel AS (
        SELECT p.name, p.dept, sum(p.count) AS pubs, sum(p.adjustedcount) AS adj
        {metrics.BASE} WHERE {where} GROUP BY ALL
    ),
    pop_people AS (SELECT DISTINCT p.name {metrics.BASE} WHERE {ever_where}),
    pop AS (
        SELECT pp.name, coalesce(s.adj, 0) AS adj, a.academic_age
        FROM pop_people pp
        LEFT JOIN (SELECT p.name, sum(p.adjustedcount) AS adj {metrics.BASE}
                   WHERE {pop_where} GROUP BY ALL) s USING (name)
        JOIN person_age a ON a.person = pp.name
        WHERE a.academic_age IS NOT NULL
    )
    SELECT s.name, s.dept AS institution, a.academic_age, a.phd_year, a.age_source,
           a.birth_year, a.birth_source, s.pubs, round(s.adj, 2) AS adj,
           (SELECT round(100.0 * avg(CASE WHEN q.adj < s.adj THEN 1
                                          WHEN q.adj = s.adj THEN 0.5 ELSE 0 END), 0)
              FROM pop q WHERE abs(q.academic_age - a.academic_age) <= {int(cohort_window)}) AS cohort_pctl,
           (SELECT count(*) FROM pop q
              WHERE abs(q.academic_age - a.academic_age) <= {int(cohort_window)}) AS cohort_n
    FROM sel s LEFT JOIN person_age a ON a.person = s.name
    WHERE s.adj >= ?
      AND (? IS NULL OR a.academic_age >= ?)
      AND (? IS NULL OR a.academic_age <= ?)
      AND s.name NOT IN (SELECT person FROM pii.forget)
    ORDER BY s.adj DESC, s.name
    {f"LIMIT {int(limit)}" if limit else ""}
    """
    rows = con.execute(
        q,
        params + ever_params + pop_params + [min_adj, age_min, age_min, age_max, age_max],
    ).fetchall()
    cols = [
        "name", "institution", "acad_age", "phd_year", "age_src",
        "birth_year", "birth_src", "pubs", "adj", "cohort_pctl", "cohort_n",
    ]
    return rows, cols


PII_COLUMNS = {"acad_age", "phd_year", "age_src", "birth_year", "birth_src"}
