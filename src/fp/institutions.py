"""Map OpenAlex institution names to CSrankings institution names.

1. normalized exact match ('The University of Texas at Austin' → 'University of Texas at Austin')
2. prefix match on a word boundary ('Purdue University West Lafayette' → 'Purdue University')
3. learned from people listed in both sources (KR faculty with OpenAlex affiliations): an OpenAlex name maps to
   the CSrankings institution of >= MIN_N such people with share >= MIN_SHARE ('Korea Advanced Institute of
   Science and Technology' → 'KAIST'). Rare/noisy pairs (one co-affiliated CNRS paper) never qualify.
Only the aggregate name→name table is stored (public DB, inst_map); no person data.
"""

from __future__ import annotations

import re

MIN_N, MIN_SHARE = 3, 0.6
# prefix matches only when the remainder names the main campus / university system / its hospital.
# 'Korea University of Technology and Education', 'Seoul National University of Science and Technology',
# 'UNC Charlotte', 'CUHK Shenzhen', 'Yonsei Mirae Campus' are different institutions and stay unmapped.
_OK_SUFFIX = re.compile(
    r"^(?:west lafayette|bloomington|twin cities|system|main campus|(?:at )?chapel hill|(?:at )?knoxville|lincoln|"
    r"(?:\w+ )*hospital|(?:\w+ )*medical center|health system)$"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS inst_map (
    oa_name     VARCHAR PRIMARY KEY,
    institution VARCHAR,
    method      VARCHAR,   -- exact / prefix / learned
    support     INTEGER
);
"""


def norm(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^the ", "", s.strip())
    s = re.sub(r"\buniv\b", "university", s)
    return re.sub(r"\s+", " ", s).strip()


def build(con) -> dict[str, int]:
    con.execute(SCHEMA)
    insts = [r[0] for r in con.execute("SELECT institution FROM institution").fetchall()]
    by_norm = {norm(i): i for i in insts}
    oa_names = [r[0] for r in con.execute("SELECT DISTINCT institution FROM pii.affiliation").fetchall() if r[0]]
    learned = {
        oa: (inst, n)
        for oa, inst, n, share in con.execute(
            """
            WITH fac AS (SELECT DISTINCT name, affiliation FROM cs_faculty),
            pairs AS (SELECT a.institution AS oa, f.affiliation AS inst, count(DISTINCT a.person) AS n
                      FROM pii.affiliation a JOIN fac f ON f.name = a.person WHERE a.year >= 2018 GROUP BY 1, 2),
            tot AS (SELECT oa, sum(n) AS t FROM pairs GROUP BY 1)
            SELECT p.oa, p.inst, p.n, p.n / t.t FROM pairs p JOIN tot t USING (oa)
            QUALIFY row_number() OVER (PARTITION BY p.oa ORDER BY p.n DESC, p.inst) = 1
            """
        ).fetchall()
        if n >= MIN_N and share >= MIN_SHARE
    }
    prefixes = sorted(by_norm, key=len, reverse=True)
    rows = []
    for oa in oa_names:
        k = norm(oa)
        if k in by_norm:
            rows.append((oa, by_norm[k], "exact", None))
        elif oa in learned:
            rows.append((oa, learned[oa][0], "learned", learned[oa][1]))
        else:
            p = next((p for p in prefixes if len(p) >= 12 and k.startswith(p + " ")
                      and _OK_SUFFIX.match(k[len(p) + 1:])), None)
            if p:
                rows.append((oa, by_norm[p], "prefix", None))
    con.execute("BEGIN")
    con.execute("DELETE FROM inst_map")
    if rows:
        con.executemany("INSERT INTO inst_map VALUES (?, ?, ?, ?)", rows)
    con.execute("COMMIT")
    return {"oa_names": len(oa_names), "mapped": len(rows),
            **{m: sum(r[2] == m for r in rows) for m in ("exact", "prefix", "learned")}}
