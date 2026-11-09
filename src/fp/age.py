"""Academic age + birth-year estimate + cohort percentile. Requires a pii.connect() connection.

Resolution order (docs/SPEC.md §8.3):
  phd_year : manual > orcid > homepage > llm (model knowledge, shown as estimate)   (else estimated as first top-venue year + PHD_AFTER_FIRST_TOP, i.e. +2)
  birth    : manual/homepage birth year  > phd-30 > bs-23 > first top-venue year - 27
All views are TEMP so nothing derived from PII is persisted into public.duckdb.
"""

from __future__ import annotations

from datetime import date

from fp import metrics

# Calibrated on people with known degrees (2026-11-09): first top-venue paper is a median 2 years BEFORE the PhD
# (IQR −4…+2, n=610); PhD − BS median 7 years (IQR 6–9, n=388). With BS at ~23 → PhD at ~30.
PHD_AFTER_FIRST_TOP = 2  # median(phd - first top-venue paper) = +2 on 608 confirmed people
BIRTH_BEFORE = {"phd": 30, "bs": 23, "first_top": 28}
SOURCE_RANK = "CASE source WHEN 'manual' THEN 0 WHEN 'orcid' THEN 1 WHEN 'homepage' THEN 2 WHEN 'llm' THEN 3 ELSE 9 END"


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


def _has_table(con, name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ? AND database_name = current_database()", [name]
    ).fetchone()[0] > 0


def _create_cites_view(con) -> None:
    """person_cites: career citations of the person's top-venue DOI papers (OpenAlex), + DOI coverage."""
    if all(_has_table(con, t) for t in ("oa_cites", "dblp_paper", "dblp_authorship")):
        con.execute(
            """CREATE OR REPLACE TEMP VIEW person_cites AS
               SELECT a.name AS person, sum(c.cited_by)::BIGINT AS cites,
                      count(c.cited_by) AS cited_papers, count(*) AS papers
               FROM dblp_authorship a JOIN dblp_paper p USING (key) LEFT JOIN oa_cites c ON c.doi = p.doi
               GROUP BY 1"""
        )
    else:
        con.execute(
            """CREATE OR REPLACE TEMP VIEW person_cites AS
               SELECT NULL::VARCHAR AS person, NULL::BIGINT AS cites, 0 AS cited_papers, 0 AS papers WHERE false"""
        )


def create_views(con, this_year: int | None = None) -> None:
    y = this_year or date.today().year
    _ensure_author_pub(con)
    _create_cites_view(con)
    if not _has_table(con, "inst_map"):
        con.execute("CREATE TEMP TABLE IF NOT EXISTS inst_map (oa_name VARCHAR, institution VARCHAR, "
                    "method VARCHAR, support INTEGER)")
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

    -- effective Korean flag: manual > second-opinion verify > rules/co-author/LLM
    CREATE OR REPLACE TEMP VIEW korean_eff AS
    SELECT k.person, CASE WHEN k.method = 'manual' THEN k.is_korean ELSE coalesce(v.is_korean, k.is_korean) END
           AS is_korean
    FROM pii.korean k LEFT JOIN pii.korean_verify v USING (person)
    UNION ALL
    SELECT v.person, v.is_korean FROM pii.korean_verify v WHERE v.person NOT IN (SELECT person FROM pii.korean);

    -- everyone we track: csrankings faculty + people classified Korean in the candidate pool
    CREATE OR REPLACE TEMP VIEW person_base AS
    SELECT name, first(affiliation) AS affiliation FROM cs_faculty GROUP BY name
    UNION ALL
    SELECT k.person, NULL FROM korean_eff k
    WHERE k.is_korean AND k.person NOT IN (SELECT name FROM cs_faculty);

    -- latest affiliation seen on the person's own papers (OpenAlex): most frequent institution in the latest year
    CREATE OR REPLACE TEMP VIEW cur_affil AS
    SELECT person, institution, country, year FROM (
        SELECT a.person, a.institution, a.country, a.year,
               row_number() OVER (PARTITION BY a.person ORDER BY a.year DESC, n_year DESC, n_all DESC, a.institution) rn
        FROM (SELECT person, institution, any_value(country) AS country, year, count(*) AS n_year,
                     sum(count(*)) OVER (PARTITION BY person, institution) AS n_all
              FROM pii.affiliation GROUP BY person, institution, year) a)
    WHERE rn = 1;

    CREATE OR REPLACE TEMP VIEW person_role AS
    SELECT b.name AS person,
           coalesce(b.affiliation, im.institution, ca.institution) AS affiliation,
           ca.institution AS affil_raw,
           coalesce(i.country, ca.country) AS country,
           CASE WHEN i.country = 'kr' THEN 'KR faculty'
                WHEN b.affiliation IS NOT NULL THEN 'faculty abroad'
                WHEN ca.country = 'kr' THEN 'other · KR'
                WHEN ca.country IS NOT NULL THEN 'other · abroad'
                ELSE 'other · unknown' END AS role,
           b.affiliation IS NOT NULL AS listed_faculty,
           ca.year AS affil_year,
           coalesce(k.is_korean, i.country = 'kr') AS is_korean
    FROM person_base b
    LEFT JOIN institution i ON i.institution = b.affiliation
    LEFT JOIN cur_affil ca ON ca.person = b.name
    LEFT JOIN inst_map im ON im.oa_name = ca.institution
    LEFT JOIN korean_eff k ON k.person = b.name;

    CREATE OR REPLACE TEMP VIEW person_age AS
    SELECT f.name AS person, f.affiliation,
           phd.year AS phd_year, phd.institution AS phd_institution, phd.source AS phd_source,
           ft.first_top_year,
           coalesce(phd.year, ft.first_top_year + {PHD_AFTER_FIRST_TOP}) AS phd_year_eff,
           CASE WHEN phd.year IS NOT NULL AND phd.source = 'llm' THEN 'est:llm'  -- model knowledge: not "confirmed"
                WHEN phd.year IS NOT NULL THEN phd.source
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
    _create_stage_view(con, y)


STUDENT, POSTDOC, SENIOR, INDUSTRY, UNKNOWN = ("박사과정 (추정)", "포닥 (추정)", "연구원·명단 외 교수 (추정)",
                                               "산업체", "미상")
POSTDOC_MAX_ACAD_AGE = 6
STUDENT_MAX_YEARS, POSTDOC_MAX_YEARS = 4, 9  # years since first top paper, when the PhD year is unknown


def _create_stage_view(con, y: int) -> None:
    """Career stage for people not on the CSrankings faculty list: institution type of the latest affiliation
    (OpenAlex) + academic age. Heuristic — always labelled (추정) except industry."""
    if _has_table(con, "oa_inst_type"):
        typ = """(SELECT a.person, any_value(t.type) AS inst_type FROM cur_affil a
                   JOIN pii.affiliation x ON x.person = a.person AND x.institution = a.institution
                   JOIN oa_inst_type t ON t.ror = x.ror GROUP BY 1)"""
    else:
        typ = "(SELECT NULL::VARCHAR AS person, NULL::VARCHAR AS inst_type WHERE FALSE)"
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW person_stage AS
        SELECT r.person, t.inst_type,
               CASE WHEN r.role IN ('KR faculty', 'faculty abroad') THEN NULL
                    WHEN t.inst_type = 'company' THEN '{INDUSTRY}'
                    -- PhD year known (orcid/homepage/manual/llm): academic age decides
                    WHEN pa.phd_year IS NOT NULL AND {y} < pa.phd_year THEN '{STUDENT}'
                    WHEN pa.phd_year IS NOT NULL AND {y} - pa.phd_year <= {POSTDOC_MAX_ACAD_AGE} THEN '{POSTDOC}'
                    WHEN pa.phd_year IS NOT NULL THEN '{SENIOR}'
                    -- otherwise years since the first top-venue paper (PhD ≈ 5–6 years in KR):
                    WHEN pa.first_top_year IS NULL THEN '{UNKNOWN}'
                    WHEN {y} - pa.first_top_year <= {STUDENT_MAX_YEARS} THEN '{STUDENT}'
                    WHEN {y} - pa.first_top_year <= {POSTDOC_MAX_YEARS} THEN '{POSTDOC}'
                    ELSE '{SENIOR}' END AS stage
        FROM person_role r LEFT JOIN {typ} t ON t.person = r.person
        LEFT JOIN person_age pa ON pa.person = r.person
        """
    )


SORTS = {
    "pubs": "논문 수",
    "cites": "피인용 (선택 분야·기간, DOI 있는 대상 학회 논문)",
    "pctl": "같은 나이대 국내 교수 대비 백분위",
}

SORT_EXPR = {"pubs": "s.pubs", "cites": "wc.cites", "pctl": "cohort_pctl"}

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
    min_pubs: int = 0,
    cohort_window: int = 2,
    limit: int | None = 100,
    scope: str = "kr",
    sort: str = "pubs",
):
    """People with output in the filter window, academic age, and percentile vs KR faculty of similar age.

    Counts come from author_pub (all authors, incl. first/last-author counts) when loaded, else cs_pub.
    Percentile population: KR faculty with >=1 paper (any year) in the selected areas, academic age within
    ±cohort_window, measured on paper count in the same window.
    """
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {list(SORTS)}")
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
        "kr": "r.role = 'KR faculty'",
        "korean": "r.is_korean",
        "world": "r.listed_faculty",
    }[scope]
    # citations of the papers inside the filter (areas + years), not career totals
    if all(_has_table(con, t) for t in ("oa_cites", "dblp_paper", "dblp_authorship")):
        wcite_sql = f"""SELECT a.name AS person, sum(c.cited_by)::BIGINT AS cites
            FROM dblp_authorship a JOIN dblp_paper p USING (key) JOIN venue v ON v.venue = p.venue
            JOIN oa_cites c ON c.doi = p.doi WHERE {win} GROUP BY 1"""
        cparams = list(wparams)
    else:
        wcite_sql, cparams = "SELECT NULL::VARCHAR AS person, NULL::BIGINT AS cites WHERE false", []
    school_sql, sparams = ("AND r.affiliation IN (SELECT unnest(?))", [f.schools]) if f.schools else ("", [])
    q = f"""
    WITH wcite AS ({wcite_sql}),
    sel AS (
        SELECT p.name, sum(p.count) AS pubs,
               sum(p.first_count) AS first_au, sum(p.last_count) AS last_au
        FROM {src} p JOIN venue v ON v.venue = p.venue WHERE {win} GROUP BY 1
    ),
    kr AS (SELECT person FROM person_role WHERE role = 'KR faculty'),
    pop AS (
        SELECT k.person AS name, coalesce(w.pubs, 0) AS pubs, a.academic_age
        FROM kr k
        JOIN (SELECT DISTINCT p.name FROM {src} p JOIN venue v ON v.venue = p.venue WHERE {ever}) e ON e.name = k.person
        LEFT JOIN (SELECT p.name, sum(p.count) AS pubs FROM {src} p JOIN venue v ON v.venue = p.venue
                   WHERE {win} GROUP BY 1) w ON w.name = k.person
        JOIN person_age a ON a.person = k.person
        WHERE a.academic_age IS NOT NULL
    )
    SELECT * EXCLUDE (sort_key), row_number() OVER (ORDER BY sort_key DESC NULLS LAST, pubs DESC, name) AS rank
    FROM (
    SELECT s.name, coalesce(r.affiliation, '-') AS institution, r.role, r.affil_year,
           a.academic_age, a.phd_year, a.age_source, a.birth_year, a.birth_source,
           {date.today().year} - a.birth_year AS age,
           s.pubs, s.first_au, s.last_au, wc.cites, pc.cites AS cites_all, pc.cited_papers, pc.papers AS top_papers,
           (SELECT round(100.0 * avg(CASE WHEN q.pubs < s.pubs THEN 1 WHEN q.pubs = s.pubs THEN 0.5 ELSE 0 END), 0)
              FROM pop q WHERE abs(q.academic_age - a.academic_age) <= {int(cohort_window)}) AS cohort_pctl,
           (SELECT count(*) FROM pop q WHERE abs(q.academic_age - a.academic_age) <= {int(cohort_window)}) AS cohort_n,
           {SORT_EXPR[sort]} AS sort_key
    FROM sel s
    JOIN person_role r ON r.person = s.name
    LEFT JOIN person_age a ON a.person = s.name
    LEFT JOIN person_cites pc ON pc.person = s.name
    LEFT JOIN wcite wc ON wc.person = s.name
    WHERE {scope_sql} {school_sql}
      AND s.pubs >= ?
      AND (? IS NULL OR a.academic_age >= ?)
      AND (? IS NULL OR a.academic_age <= ?)
    )
    ORDER BY rank
    {f"LIMIT {int(limit)}" if limit else ""}
    """
    rows = con.execute(
        q, cparams + wparams + eparams + wparams + sparams + [min_pubs, age_min, age_min, age_max, age_max]
    ).fetchall()
    cols = [
        "name", "institution", "role", "affil_year", "acad_age", "phd_year", "age_src", "birth_year", "birth_src",
        "age", "pubs", "first_au", "last_au", "cites", "cites_all", "cited_papers", "top_papers", "cohort_pctl", "cohort_n",
        "rank",
    ]
    return rows, cols


PII_COLUMNS = {"acad_age", "phd_year", "age_src", "birth_year", "birth_src", "age"}


def person_papers(con, name: str, f: metrics.Filter):
    """Papers counted for `name` under the filter (same rule as candidates' pubs), with OpenAlex citations."""
    w, prm = _pub_where(f, "p")
    from fp.boards import field_sql

    cites = "c.cited_by" if con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'oa_cites'").fetchone()[0] else "NULL::INTEGER"
    join = "LEFT JOIN oa_cites c ON c.doi = p.doi" if cites != "NULL::INTEGER" else ""
    return con.execute(
        f"""
        SELECT p.year, upper(p.venue) AS venue, {field_sql('v')} AS field, p.title,
               CASE WHEN a.pos = 1 THEN '1저자' WHEN a.pos = p.n_authors AND p.n_authors > 1 THEN '마지막'
                    ELSE a.pos || '/' || p.n_authors END AS position,
               {cites} AS cites,
               CASE WHEN p.doi IS NOT NULL THEN 'https://doi.org/' || p.doi END AS link
        FROM dblp_authorship a JOIN dblp_paper p USING (key) JOIN venue v ON v.venue = p.venue {join}
        WHERE a.name = ? AND {w}
        ORDER BY p.year DESC, cites DESC NULLS LAST, p.title
        """,
        [name, *prm],
    ).df()
