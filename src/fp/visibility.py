"""Visibility score (0–100) of a researcher within a field, from public data only:

    V = min(100, 0.2·min(P, 100) + 0.001·min(C, 20 000) + 10·(ACM + IEEE) + 10·(n_PC + n_GC + n_SC) + 5·(n_BP + n_KN))

P = papers at the field's top venues (career, DBLP), C = their citations (OpenAlex). ACM / IEEE Fellow are yes/no.
The other five count occurrences (user decision 2026-11-27: "횟수도 중요"): each distinct record — conference + year;
steering per conference — up to MAX_COUNT per item. Records come from pii.vis_item (fp.ingest.visibility);
fellowships count in every field the person is active in, the rest only at a top venue of the field (the user's
current Top 학회 setting). A record not found = 0 (user decision); the table shows the records and evidence.
"""

from __future__ import annotations

import pandas as pd

ITEMS = {  # kind → (column, points)
    "acm_fellow": ("acm", 10), "ieee_fellow": ("ieee", 10), "pc_chair": ("pc", 10), "general_chair": ("gc", 10),
    "steering": ("sc", 10), "best_paper": ("bp", 5), "keynote": ("kn", 5),
}
FELLOWS = ("acm_fellow", "ieee_fellow")
MAX_COUNT = 3  # occurrences counted per item (chair / steering / best paper / keynote)
TOTAL_CAP = 100
P_CAP, P_PTS = 100, 0.2
C_CAP, C_PTS = 20_000, 0.001
KIND_KO = {"acm_fellow": "ACM Fellow", "ieee_fellow": "IEEE Fellow", "pc_chair": "PC Chair",
           "general_chair": "General Chair", "steering": "Steering/Endowment", "best_paper": "Best Paper",
           "keynote": "Keynote"}


def _has(con, table: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name = 'pii' AND table_name = ?",
                            [table]).fetchone()[0])


def _dedupe(it: pd.DataFrame) -> pd.DataFrame:
    """One row per record: the same role found by a list / by hand / by the web search is kept once (hand-entered
    first, then the one with a year); a yearless duplicate of a dated record is dropped; a fellowship is kept once."""
    if it.empty:
        return it
    g = it.assign(_pri=it.source.map({"manual": 0, "wikipedia-acm": 1, "bpa-list": 1}).fillna(2),
                  _noyear=it.year.isna(), _v=it.venue.fillna(""))
    g = g.sort_values(["_pri", "_noyear"])
    fel = g.kind.isin(FELLOWS)
    g = pd.concat([g[fel].drop_duplicates(["person", "kind"]),
                   g[~fel].drop_duplicates(["person", "kind", "_v", "year"])])
    dated = g.groupby(["person", "kind", "_v"]).year.transform("count") > 0
    g = g[g.kind.isin(FELLOWS) | ~(g._noyear & dated)]
    return g.drop(columns=["_pri", "_noyear"])


def _count(g: pd.DataFrame) -> pd.Series:
    """Occurrences per (person, kind): steering = distinct conferences, the rest = distinct records."""
    st = g.kind == "steering"
    a = g[~st].groupby(["person", "kind"]).size()
    b = g[st].groupby(["person", "kind"])._v.nunique()
    return pd.concat([a, b])


def scores(con, people: list[str], venues: list[str]) -> pd.DataFrame:
    """One row per person with P, C, the 7 item counts (fellows 0/1), evidence text and the score. venues = the field's top venues
    (or the chosen sub-field's). Only people with a field paper or a field-specific item are returned."""
    from fp import age, boards as B

    age.create_views(con)
    pc = con.execute(
        """SELECT a.name, count(DISTINCT p.key) AS P, coalesce(sum(c.cited_by), 0)::BIGINT AS C,
                  count(c.cited_by) AS c_known
           FROM dblp_authorship a JOIN dblp_paper p USING (key) LEFT JOIN paper_cites c ON c.key = p.key
           WHERE a.name IN (SELECT unnest(?)) AND p.venue IN (SELECT unnest(?)) GROUP BY 1""",
        [people, venues]).df().set_index("name")
    from fp.ingest.visibility import canonical_names

    canon, pset = canonical_names(con), set(people)
    if _has(con, "vis_item"):
        it = con.execute(
            """SELECT person, kind, venue, venue_raw, year, evidence_url, source FROM pii.vis_item
               WHERE (person IN (SELECT unnest(?))
                      OR person IN (SELECT unnest(?)))
                 AND (kind IN ('acm_fellow', 'ieee_fellow') OR venue IN (SELECT unnest(?)))""",
            [people, [a for a, n in canon.items() if n in pset], venues]).df()
        checked = set(r[0] for r in con.execute("SELECT person FROM pii.vis_checked").fetchall()) \
            if _has(con, "vis_checked") else set()
    else:
        it = pd.DataFrame(columns=["person", "kind", "venue", "venue_raw", "year", "evidence_url", "source"])
        checked = set()
    # records found under another spelling of the same person belong to the kept name
    if len(it):
        it["person"] = it.person.map(lambda p: canon.get(p, p))
        it = it[it.person.isin(pset)]
    checked = {canon.get(p, p) for p in checked}
    names = sorted(set(pc.index) | set(it.person))
    df = pd.DataFrame({"name": names})
    df["P"] = df.name.map(pc.P).fillna(0).astype(int)
    df["C"] = df.name.map(pc.C).fillna(0).astype(int)
    df["c_cov"] = (df.name.map(pc.c_known).fillna(0) / df.P.where(df.P > 0)).round(2)
    it = _dedupe(it)
    cnt = _count(it) if len(it) else pd.Series(dtype=int)
    for kind, (col, _) in ITEMS.items():
        n = df.name.map(cnt.xs(kind, level="kind") if len(cnt) and kind in cnt.index.get_level_values("kind")
                        else {}).fillna(0).astype(int)
        df[col] = n.clip(upper=1) if kind in FELLOWS else n
    item_pts = sum(pts * df[col].clip(upper=MAX_COUNT) for col, pts in ITEMS.values())
    df["score"] = (P_PTS * df.P.clip(upper=P_CAP) + C_PTS * df.C.clip(upper=C_CAP) + item_pts).clip(
        upper=TOTAL_CAP).round(1)

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
