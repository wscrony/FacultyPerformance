"""Per-run snapshots of tracked people and a diff report between the last two runs.

Snapshots are person-level (affiliation, age, output) → encrypted PII DB. Reports are written to exports/
(gitignored, local only).
"""

from __future__ import annotations

import json
from datetime import date, datetime

from fp import age, paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS pii.run (
    run_id  INTEGER PRIMARY KEY,
    run_at  TIMESTAMP,
    steps   JSON
);
CREATE TABLE IF NOT EXISTS pii.run_person (  -- v2: no adjusted counts
    run_id      INTEGER,
    person      VARCHAR,
    role        VARCHAR,
    affiliation VARCHAR,
    is_korean   BOOLEAN,
    acad_age    INTEGER,
    age_source  VARCHAR,
    pubs_5y     INTEGER,
    cites       BIGINT
);
ALTER TABLE pii.run_person DROP COLUMN IF EXISTS adj_5y;  -- migration from v1
"""


def snapshot(con, steps: dict | None = None) -> int:
    con.execute(SCHEMA)
    age.create_views(con)
    run_id = con.execute("SELECT coalesce(max(run_id), 0) + 1 FROM pii.run").fetchone()[0]
    y = date.today().year
    src = "author_pub" if con.execute("SELECT count(*) > 0 FROM (SELECT 1 FROM author_pub LIMIT 1)").fetchone()[0] \
        else "cs_pub"
    con.execute("BEGIN")
    con.execute("INSERT INTO pii.run VALUES (?, ?, ?)", [run_id, datetime.now(), json.dumps(steps or {}, default=str)])
    con.execute(
        f"""
        INSERT INTO pii.run_person
        SELECT ?, r.person, r.role, r.affiliation, r.is_korean, a.academic_age, a.age_source,
               coalesce(o.pubs, 0), pc.cites
        FROM person_role r
        LEFT JOIN person_age a ON a.person = r.person
        LEFT JOIN person_cites pc ON pc.person = r.person
        LEFT JOIN (SELECT p.name, sum(p.count) AS pubs
                   FROM {src} p JOIN venue v ON v.venue = p.venue
                   WHERE p.year >= ? AND NOT v.next_tier GROUP BY 1) o ON o.name = r.person
        WHERE r.is_korean OR r.role = 'KR faculty'
           OR r.person IN (SELECT person FROM pii.run_person WHERE run_id = ?)  -- keep tracking people who left
        """,
        [run_id, y - 4, run_id - 1],
    )
    con.execute("COMMIT")
    return run_id


def diff(con, new: int | None = None, old: int | None = None) -> dict[str, list]:
    runs = [r[0] for r in con.execute("SELECT run_id FROM pii.run ORDER BY run_id DESC").fetchall()]
    if len(runs) < 2 and (new is None or old is None):
        return {}
    new = new or runs[0]
    old = old or runs[1]
    q = lambda sql: con.execute(sql, [old, new]).fetchall()
    J = """FROM (SELECT * FROM pii.run_person WHERE run_id = ?) o
           FULL JOIN (SELECT * FROM pii.run_person WHERE run_id = ?) n USING (person)"""
    return {
        "runs": [old, new],
        "kr_faculty_joined": q(f"SELECT person, n.affiliation {J} WHERE n.role = 'KR faculty' "
                               "AND o.role IS DISTINCT FROM 'KR faculty' ORDER BY 2, 1"),
        "kr_faculty_left": q(f"SELECT person, o.affiliation, n.affiliation {J} WHERE o.role = 'KR faculty' "
                             "AND n.role IS DISTINCT FROM 'KR faculty' ORDER BY 2, 1"),
        "moved": q(f"SELECT person, o.affiliation, n.affiliation, n.role {J} WHERE o.affiliation IS NOT NULL "
                   "AND n.affiliation IS NOT NULL AND o.affiliation <> n.affiliation ORDER BY 1"),
        "new_candidates": q(f"SELECT person, n.affiliation, n.acad_age, n.pubs_5y {J} "
                            "WHERE o.person IS NULL AND n.is_korean ORDER BY n.pubs_5y DESC, person LIMIT 50"),
        "age_confirmed": q(f"SELECT person, n.acad_age, n.age_source {J} WHERE n.age_source IN "
                           "('orcid','homepage','manual') AND o.age_source NOT IN ('orcid','homepage','manual') "
                           "ORDER BY 1"),
        "output_up": q(f"SELECT person, n.affiliation, o.pubs_5y, n.pubs_5y {J} "
                       "WHERE n.pubs_5y - o.pubs_5y >= 3 ORDER BY n.pubs_5y - o.pubs_5y DESC, person LIMIT 30"),
    }


TITLES = {
    "kr_faculty_joined": ("국내 교수 신규 (CSrankings)", ["이름", "소속"]),
    "kr_faculty_left": ("국내 교수 명단에서 빠짐", ["이름", "이전 소속", "현재 소속"]),
    "moved": ("소속 변경", ["이름", "이전", "현재", "구분"]),
    "new_candidates": ("새 한국인 후보자 (최근 5년 논문 수 상위 50)", ["이름", "소속", "학문적 나이", "논문 5y"]),
    "age_confirmed": ("나이 근거 새로 확인", ["이름", "학문적 나이", "출처"]),
    "output_up": ("최근 5년 논문 3편 이상 증가", ["이름", "소속", "이전", "현재"]),
}


def to_markdown(d: dict) -> str:
    if not d:
        return "# FacultyPerformance 변경 리포트\n\n비교할 이전 실행이 없습니다 (첫 실행).\n"
    out = [f"# FacultyPerformance 변경 리포트\n\n실행 #{d['runs'][0]} → #{d['runs'][1]} · {date.today()}\n"]
    for k, (title, cols) in TITLES.items():
        rows = d.get(k, [])
        out.append(f"\n## {title} ({len(rows)})\n")
        if not rows:
            out.append("없음\n")
            continue
        out.append("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n")
        for r in rows:
            out.append("| " + " | ".join("-" if v is None else str(v) for v in r) + " |\n")
    return "".join(out)


def write(con) -> str:
    paths.ensure_dirs()
    md = to_markdown(diff(con))
    path = paths.EXPORTS / f"report-{date.today()}.md"
    path.write_text(md, encoding="utf-8")
    return str(path)
