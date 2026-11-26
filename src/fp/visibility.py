"""Visibility score (0–100) of a researcher within a field, from public data only:

    V = 0.2·min(P, 100) + 0.001·min(C, 20 000) + 10·(ACM + IEEE + PC + GC + SC) + 5·(BP + KN)

P = papers at the field's top venues (career, DBLP), C = their citations (OpenAlex). The seven yes/no items come
from pii.vis_item (fp.ingest.visibility): fellowships count in every field the person is active in; chair /
steering / best paper / keynote count only at a top venue of the field (the user's current Top 학회 setting). A record not found = 0 (user decision);
the table shows which items were found and the evidence.
"""

from __future__ import annotations

import pandas as pd

ITEMS = {  # kind → (column, points)
    "acm_fellow": ("acm", 10), "ieee_fellow": ("ieee", 10), "pc_chair": ("pc", 10), "general_chair": ("gc", 10),
    "steering": ("sc", 10), "best_paper": ("bp", 5), "keynote": ("kn", 5),
}
P_CAP, P_PTS = 100, 0.2
C_CAP, C_PTS = 20_000, 0.001
KIND_KO = {"acm_fellow": "ACM Fellow", "ieee_fellow": "IEEE Fellow", "pc_chair": "PC Chair",
           "general_chair": "General Chair", "steering": "Steering/Endowment", "best_paper": "Best Paper",
           "keynote": "Keynote"}


def _has(con, table: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name = 'pii' AND table_name = ?",
                            [table]).fetchone()[0])


def scores(con, people: list[str], venues: list[str]) -> pd.DataFrame:
    """One row per person with P, C, the 7 items (0/1), evidence text and the score. venues = the field's top venues
    (or the chosen sub-field's). Only people with a field paper or a field-specific item are returned."""
    from fp import age, boards as B

    age.create_views(con)
    pc = con.execute(
        """SELECT a.name, count(DISTINCT p.key) AS P, coalesce(sum(c.cited_by), 0)::BIGINT AS C,
                  count(c.cited_by) AS c_known
           FROM dblp_authorship a JOIN dblp_paper p USING (key) LEFT JOIN paper_cites c ON c.key = p.key
           WHERE a.name IN (SELECT unnest(?)) AND p.venue IN (SELECT unnest(?)) GROUP BY 1""",
        [people, venues]).df().set_index("name")
    if _has(con, "vis_item"):
        it = con.execute(
            """SELECT person, kind, venue, venue_raw, year, evidence_url, source FROM pii.vis_item
               WHERE person IN (SELECT unnest(?))
                 AND (kind IN ('acm_fellow', 'ieee_fellow') OR venue IN (SELECT unnest(?)))""",
            [people, venues]).df()
        checked = set(r[0] for r in con.execute("SELECT person FROM pii.vis_checked").fetchall()) \
            if _has(con, "vis_checked") else set()
    else:
        it = pd.DataFrame(columns=["person", "kind", "venue", "venue_raw", "year", "evidence_url", "source"])
        checked = set()
    names = sorted(set(pc.index) | set(it.person))
    df = pd.DataFrame({"name": names})
    df["P"] = df.name.map(pc.P).fillna(0).astype(int)
    df["C"] = df.name.map(pc.C).fillna(0).astype(int)
    df["c_cov"] = (df.name.map(pc.c_known).fillna(0) / df.P.where(df.P > 0)).round(2)
    for kind, (col, _) in ITEMS.items():
        df[col] = df.name.isin(set(it[it.kind == kind].person)).astype(int)
    df["score"] = (P_PTS * df.P.clip(upper=P_CAP) + C_PTS * df.C.clip(upper=C_CAP)
                   + sum(pts * df[col] for col, pts in ITEMS.values())).round(1)

    def ev(g):
        g = g.sort_values(["kind", "year"])
        return " · ".join(f"{KIND_KO[k]}" + (f" {B.venue_label(v)}" if isinstance(v, str) and v else "")
                          + (f" {int(y)}" if pd.notna(y) else "") + (" (수동)" if src == "manual" else "")
                          for k, v, y, src in zip(g.kind, g.venue, g.year, g.source))

    evid = it.groupby("person").apply(ev, include_groups=False) if len(it) else pd.Series(dtype=str)
    url = it.dropna(subset=["evidence_url"]).groupby("person").evidence_url.first() if len(it) else pd.Series(dtype=str)
    df["evidence"] = df.name.map(evid).fillna("")
    df["evidence_url"] = df.name.map(url).fillna("")
    df["searched"] = df.name.isin(checked)
    # fellowships count in every field, but only for people active in it: a paper or a field-specific record
    df = df[(df.P > 0) | (df[["pc", "gc", "sc", "bp", "kn"]].sum(axis=1) > 0)]
    return df.sort_values(["score", "P", "name"], ascending=[False, False, True]).reset_index(drop=True)
