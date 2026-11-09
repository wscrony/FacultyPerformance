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
    fields: list[str] | None = None  # department fields (FIELDS keys)

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
            elif self.fields:
                conds.append(f"({field_sql(v)}) IN (SELECT unnest(?))")
                params.append(self.fields)
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


MIN_FACULTY_NORM = 3  # per-faculty rank only for institutions with this many active faculty in the filter


def institution_share(con, f: BF, top: int = 60):
    """Rows: grp, institution, country, papers, share% (of all papers in filter); top N institutions."""
    ensure_pinst(con)
    w, prm = f.where("x")
    tot = total_papers(con, f)
    df = con.execute(
        f"""
        SELECT x.grp, x.institution, any_value(x.country) AS country, count(DISTINCT x.key) AS papers,
               count(DISTINCT x.name) AS faculty  -- CSrankings faculty with >= 1 paper in the filter
        FROM pinst x JOIN venue v ON v.venue = x.venue
        WHERE {w} AND {f.credit_sql()} GROUP BY 1, 2
        """,
        prm,
    ).df()
    df["share"] = 100.0 * df.papers / max(tot, 1)
    df["rank"] = df.papers.rank(method="min", ascending=False).astype(int)  # ties share a rank
    df["per_faculty"] = df.papers / df.faculty
    ok = df.faculty >= MIN_FACULTY_NORM
    df["rank_norm"] = df.per_faculty.where(ok).rank(method="min", ascending=False).astype("Int64")
    df = df.sort_values(["papers", "institution"], ascending=[False, True]).head(top).reset_index(drop=True)
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


def _has_view(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM duckdb_views() WHERE view_name = ?", [name]).fetchone()[0])


def kr_faculty_age_output(con, f: BF):
    """KR faculty: academic age vs papers in the filter window."""
    w, prm = f.where("p")
    return con.execute(
        f"""
        WITH out AS (SELECT a.name, count(*) AS papers, sum(c.cited_by) AS cites,
                            count(*) FILTER (WHERE a.pos = 1) AS first_au
                     FROM dblp_authorship a JOIN dblp_paper p USING (key)
                     JOIN venue v ON v.venue = p.venue {"LEFT JOIN paper_cites c ON c.key = a.key" if _has_view(con, "paper_cites") else "LEFT JOIN (SELECT NULL::VARCHAR AS key, NULL::BIGINT AS cited_by) c ON FALSE"}
                     WHERE {w} GROUP BY 1)
        SELECT r.person, r.affiliation, a.academic_age, a.age_source, coalesce(o.papers, 0) AS papers,
               coalesce(o.cites, 0)::BIGINT AS cites, coalesce(o.first_au, 0) AS first_au
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
        pubs AS (SELECT a.name, {_FIELD_SQL} AS field, p.year FROM dblp_authorship a JOIN dblp_paper p USING (key)
                 JOIN venue v ON v.venue = p.venue WHERE a.name IN (SELECT name FROM fac) AND (? OR NOT v.next_tier))
        SELECT f.name, pa.academic_age, pa.age_source,
               count(*) FILTER (WHERE pubs.year BETWEEN ? AND ?) AS papers_window,
               count(pubs.year) AS papers_career,
               mode(pubs.field) AS main_area
        FROM fac f LEFT JOIN pubs ON pubs.name = f.name LEFT JOIN person_age pa ON pa.person = f.name
        GROUP BY ALL ORDER BY papers_window DESC, f.name
        """,
        [school, f.next_tier, f.start, f.end],
    ).df()


def school_trend(con, school: str, f: BF):
    ensure_pinst(con)
    whole = BF(start=0, end=9999, areas=f.areas, venues=f.venues, next_tier=f.next_tier, credit=f.credit,
               fields=f.fields)
    w, prm = whole.where("x")
    return con.execute(
        f"""SELECT x.year, {_FIELD_SQL} AS field, count(DISTINCT x.key) AS papers
            FROM pinst x JOIN venue v ON v.venue = x.venue
            WHERE {w} AND x.institution = ? GROUP BY 1, 2 ORDER BY 1""",
        prm + [school],
    ).df()




def talent_matrix(con, f: BF):
    """Korean researchers active in the filter window: main department field × career stage × location."""
    w, prm = f.where("a", "v")
    return con.execute(
        f"""
        WITH act AS (SELECT a.name, {_FIELD_SQL} AS field, sum(a.count) AS n
                     FROM author_pub a JOIN venue v ON v.venue = a.venue WHERE {w} GROUP BY 1, 2),
        main AS (SELECT name, arg_max(field, n) AS area, sum(n) AS papers FROM act GROUP BY 1)
        SELECT r.person, r.role, r.country, m.area, m.papers, a.academic_age,
               CASE WHEN a.academic_age < 0 THEN '① 박사 전 (추정)'
                    WHEN a.academic_age < 5 THEN '② 0–4년' WHEN a.academic_age < 10 THEN '③ 5–9년'
                    WHEN a.academic_age < 20 THEN '④ 10–19년' ELSE '⑤ 20년+' END AS stage
        FROM person_role r JOIN main m ON m.name = r.person LEFT JOIN person_age a ON a.person = r.person
        WHERE r.is_korean AND a.academic_age IS NOT NULL
        """,
        prm,
    ).df()


# Department field taxonomy (Applied / Foundational Computing), mapped from CSrankings areas.
FIELDS = {
    "HCI": ("Human-Computer Interaction", "Applied", ["chi"]),
    "NLP": ("Natural Language Processing", "Applied", ["nlp"]),
    "VC": ("Visual Computing", "Applied", ["vision", "graph", "visualization"]),
    "DB": ("Databases (SIGMOD, VLDB · ICDE, PODS)", "Applied", ["mod"]),
    "DM": ("Data Mining & IR (WWW, SIGIR · KDD)", "Applied", ["inforet"]),
    "ML": ("Machine Learning (NeurIPS, ICML, ICLR · AAAI, IJCAI)", "Applied", ["mlmining", "ai"]),
    "TC": ("Theory of Computing", "Foundational", ["act", "crypt", "log"]),
    "SS": ("Secure Systems", "Foundational", ["sec"]),
    "CS": ("Computing Systems", "Foundational", ["arch", "bed", "da", "hpc", "ops", "metrics"]),
    "NS": ("Network Systems", "Foundational", ["comm", "mobile"]),
    "SP": ("Software Eng. & Prog. Languages", "Foundational", ["soft", "plan"]),
    "기타": ("Robotics, Comp. Bio, ECom, CSEd", "Other", ["robotics", "bio", "ecom", "csed"]),
}
AREA_FIELD = {a: k for k, (_, _, areas) in FIELDS.items() for a in areas}
# venue-level exceptions: CSrankings files KDD under 'ML'; here it is data mining
VENUE_FIELD = {"kdd": "DM", "cikm": "DM"}  # user decisions: KDD, CIKM are Data Mining & IR


def field_sql(v: str = "v") -> str:
    exc = " ".join(f"WHEN {v}.venue = '{x}' THEN '{k}'" for x, k in VENUE_FIELD.items())
    by_area = " ".join(f"WHEN {v}.area = '{a}' THEN '{k}'" for a, k in AREA_FIELD.items())
    return f"CASE {exc} {by_area} ELSE '기타' END"


_FIELD_SQL = field_sql("v")


def kr_field_matrix(con, f: BF):
    """KR institutions × department field: distinct papers and rank among KR institutions per field."""
    ensure_pinst(con)
    w, p = f.where("x")
    return con.execute(
        f"""
        WITH t AS (SELECT x.institution, {_FIELD_SQL} AS field, count(DISTINCT x.key) AS papers,
                          count(DISTINCT x.name) AS faculty
                   FROM pinst x JOIN venue v ON v.venue = x.venue
                   WHERE {w} AND x.country = 'kr' GROUP BY 1, 2)
        SELECT *, rank() OVER (PARTITION BY field ORDER BY papers DESC) AS kr_rank,
               100.0 * papers / sum(papers) OVER (PARTITION BY institution) AS pct
        FROM t
        """,
        p,
    ).df()


def school_faculty_fields(con, school: str, f: BF):
    """Per faculty × department field: papers in the window and over the career (one school)."""
    return con.execute(
        f"""
        WITH fac AS (SELECT DISTINCT name FROM cs_faculty WHERE affiliation = ?)
        SELECT a.name, {_FIELD_SQL} AS field,
               count(*) FILTER (WHERE p.year BETWEEN ? AND ?) AS papers_window, count(*) AS papers_career
        FROM dblp_authorship a JOIN dblp_paper p USING (key) JOIN venue v ON v.venue = p.venue
        WHERE a.name IN (SELECT name FROM fac) AND (? OR NOT v.next_tier)
        GROUP BY 1, 2
        """,
        [school, f.start, f.end, f.next_tier],
    ).df()


def kr_faculty_field_rank(con, f: BF):
    """Rank of every KR faculty member among all KR faculty, per department field and overall ('전체')."""
    return con.execute(
        f"""
        WITH fac AS (SELECT DISTINCT c.name FROM cs_faculty c JOIN institution i ON i.institution = c.affiliation
                     WHERE i.country = 'kr'),
        pp AS (SELECT a.name, {_FIELD_SQL} AS field, count(*) AS papers
               FROM dblp_authorship a JOIN dblp_paper p USING (key) JOIN venue v ON v.venue = p.venue
               WHERE a.name IN (SELECT name FROM fac) AND p.year BETWEEN ? AND ? AND (? OR NOT v.next_tier)
               GROUP BY GROUPING SETS ((1, 2), (1)))
        SELECT name, coalesce(field, '전체') AS field, papers,
               rank() OVER (PARTITION BY coalesce(field, '전체') ORDER BY papers DESC) AS kr_rank,
               count(*) OVER (PARTITION BY coalesce(field, '전체')) AS kr_n
        FROM pp
        """,
        [f.start, f.end, f.next_tier],
    ).df()


def person_field_counts(con, names: list[str], f: BF):
    """Papers per person × department field in the filter window (any names, e.g. faculty abroad)."""
    if not names:
        import pandas as pd

        return pd.DataFrame(columns=["name", "field", "papers"])
    w, prm = f.where("p", "v")
    return con.execute(
        f"""
        SELECT a.name, {_FIELD_SQL} AS field, count(*) AS papers
        FROM dblp_authorship a JOIN dblp_paper p USING (key) JOIN venue v ON v.venue = p.venue
        WHERE a.name IN (SELECT unnest(?)) AND {w}
        GROUP BY 1, 2
        """,
        [names, *prm],
    ).df()


def field_venues(con, fields: list[str], next_tier: bool = False) -> list[str]:
    """Venues belonging to the given department fields (top venues only unless next_tier)."""
    return [r[0] for r in con.execute(
        f"SELECT v.venue FROM venue v WHERE ({_FIELD_SQL}) IN (SELECT unnest(?)) AND (? OR NOT v.next_tier) ORDER BY 1",
        [fields, next_tier],
    ).fetchall()]


def institution_faculty(con, institution: str, f: BF):
    """Faculty counted for an institution under the filter (>= 1 paper), with papers and main field."""
    ensure_pinst(con)
    w, prm = f.where("x")
    df = con.execute(
        f"""
        WITH t AS (SELECT x.name, {_FIELD_SQL} AS field, count(DISTINCT x.key) AS n
                   FROM pinst x JOIN venue v ON v.venue = x.venue
                   WHERE {w} AND {f.credit_sql()} AND x.institution = ? GROUP BY 1, 2)
        SELECT name, sum(n)::INTEGER AS papers, arg_max(field, n) AS main_field, max(n) AS main_papers,
               string_agg(field || ' ' || n, ' · ' ORDER BY n DESC) AS by_field
        FROM t GROUP BY 1 ORDER BY papers DESC, name
        """,
        prm + [institution],
    ).df()
    df.insert(0, "no", range(1, len(df) + 1))
    dept = _departments(con)
    if dept is not None and len(df):
        df = df.merge(dept, how="left", left_on="name", right_on="person").drop(columns="person")
        df.insert(2, "dept_group", df.pop("dept_group"))
        df.insert(3, "department", df.pop("department"))
    return df


def _departments(con):
    """LLM-estimated department per KR faculty member (pii.department), or None when not collected."""
    if not con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name = 'pii' AND table_name = 'department'"
                       ).fetchone()[0]:
        return None
    return con.execute("SELECT person, department, dept_group FROM pii.department").df()


def institution_dept_summary(df) -> str:
    """'컴퓨터/소프트웨어 70 · 전기·전자 25 · …' for an institution_faculty frame."""
    if "dept_group" not in df:
        return ""
    vc = df.dept_group.fillna("미상").value_counts()
    return " · ".join(f"{k} {v}명" for k, v in vc.items())
