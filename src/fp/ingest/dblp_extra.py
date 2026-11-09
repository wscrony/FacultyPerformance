"""Venues outside CSrankings (e.g. SoCG, CIKM, COLT) from the full DBLP dump, for the top-venue chooser.

Source: https://dblp.org/xml/dblp.xml.gz (public, CC0). Only the venues of the venue catalog
(data/venue_catalog_csconf.csv) whose DBLP key is not already covered are kept.

Stage 1 (parse, no DB lock) → parquet under data/snapshots/dblp_extra/. Stage 2 (load) inserts into dblp_paper /
dblp_authorship / venue with category 'extra' and next_tier TRUE; extra venues only count once the user marks them
top (topvenues.apply hides unchosen extra venues entirely, so the "확장 학회 포함" toggle is unaffected).
`reapply` re-inserts them after a CSrankings/DBLP reload (those replace the tables).

Area: an extra venue gets the CSrankings area most of its authors publish in (their CSrankings-venue papers),
so field filters (HCI/ML/TC/…) work for it.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path
from typing import Callable

import pyarrow as pa
import pyarrow.parquet as pq

from fp import paths
from fp.ingest.dblp import SCHEMA_AUTH, SCHEMA_PAPER, _doi, _text, pagecount

RAW = paths.RAW / "dblp_full" / "dblp.xml.gz"
OUT = paths.SNAPSHOTS / "dblp_extra"
MIN_PAGES = 4  # drop front matter / 1-3 page abstracts where page info exists


def wanted_keys(con) -> dict[str, str]:
    """DBLP key prefix (e.g. 'conf/compgeom') → venue code, for catalog venues we have no papers for."""
    from fp import topvenues

    cat = topvenues.catalog(con)
    have = {r[0] for r in con.execute(f"SELECT venue FROM {topvenues.base_venue(con)}").fetchall()}
    out = {}
    for k in cat[cat.venue.isna()].dblp_key:
        if not re.fullmatch(r"conf/[\w-]+", k or ""):
            continue  # series/… (Findings) and journals are out of scope
        code = k.split("/", 1)[1].lower()
        out[k] = code if code not in have else f"x_{code}"
    return out


def parse(prefixes: dict[str, str], src: Path = RAW, out: Path = OUT,
          progress: Callable[[int], None] | None = None, batch: int = 50_000) -> dict[str, int]:
    from lxml import etree

    out.mkdir(parents=True, exist_ok=True)
    pw = pq.ParquetWriter(out / "paper.parquet.part", SCHEMA_PAPER)
    aw = pq.ParquetWriter(out / "authorship.parquet.part", SCHEMA_AUTH)
    papers: dict[str, list] = {k: [] for k in SCHEMA_PAPER.names}
    auths: dict[str, list] = {k: [] for k in SCHEMA_AUTH.names}
    seen = kept = 0

    def flush():
        if papers["key"]:
            pw.write_table(pa.table(papers, schema=SCHEMA_PAPER))
            aw.write_table(pa.table(auths, schema=SCHEMA_AUTH))
            for d in (papers, auths):
                for v in d.values():
                    v.clear()

    with gzip.open(src, "rb") as fh:
        ctx = etree.iterparse(fh, events=("end",), tag="inproceedings", huge_tree=True, load_dtd=True,
                              resolve_entities=True, no_network=True, dtd_validation=False)
        for _, elem in ctx:
            seen += 1
            key = elem.get("key", "")
            pref = key.rsplit("/", 1)[0]
            code = prefixes.get(pref)
            authors = [("".join(a.itertext()).strip(), a.get("orcid")) for a in elem.findall("author")]
            year_s = _text(elem, "year", "")
            pages = _text(elem, "pages")
            pc = pagecount(pages) if pages else -1
            if code and authors and year_s.isdigit() and (pc < 0 or pc >= MIN_PAGES):
                kept += 1
                papers["key"].append(key)
                papers["venue"].append(code)
                papers["year"].append(int(year_s))
                papers["n_authors"].append(len(authors))
                papers["title"].append(_text(elem, "title"))
                papers["page_count"].append(pc)
                papers["doi"].append(_doi(elem))
                for i, (raw, orcid) in enumerate(authors):
                    auths["key"].append(key)
                    auths["pos"].append(i + 1)
                    auths["name"].append(raw)  # aliases applied at load (alias table)
                    auths["raw_name"].append(raw)
                    auths["orcid"].append(orcid)
                if len(papers["key"]) >= batch:
                    flush()
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
            if progress and seen % 100_000 == 0:
                progress(seen)
    flush()
    pw.close()
    aw.close()
    for f in ("paper", "authorship"):
        (out / f"{f}.parquet.part").rename(out / f"{f}.parquet")
    pq.write_table(pa.table({"prefix": list(prefixes), "venue": list(prefixes.values())}), out / "venues.parquet")
    return {"seen": seen, "kept": kept, "venues": len(prefixes)}


def reapply(con, out: Path = OUT) -> dict[str, int]:
    """(Re)insert the extra venues' papers into dblp_paper/dblp_authorship/venue; rebuild author_pub."""
    if not (out / "paper.parquet").exists():
        return {"papers": 0}
    from fp import topvenues

    base = topvenues.base_venue(con)
    con.execute("BEGIN")
    try:
        con.execute(f"CREATE OR REPLACE TEMP TABLE _xv AS SELECT * FROM read_parquet('{out}/venues.parquet')")
        con.execute("DELETE FROM dblp_authorship WHERE key IN (SELECT key FROM dblp_paper "
                    "WHERE venue IN (SELECT venue FROM _xv))")
        con.execute("DELETE FROM dblp_paper WHERE venue IN (SELECT venue FROM _xv)")
        con.execute(f"DELETE FROM {base} WHERE venue IN (SELECT venue FROM _xv)")
        con.execute(f"INSERT INTO dblp_paper SELECT * FROM read_parquet('{out}/paper.parquet') "
                    "WHERE key NOT IN (SELECT key FROM dblp_paper)")
        con.execute(f"""INSERT INTO dblp_authorship
                        SELECT a.key, a.pos, coalesce(al.name, a.name), a.raw_name, a.orcid
                        FROM read_parquet('{out}/authorship.parquet') a LEFT JOIN alias al ON al.alias = a.name
                        WHERE a.key IN (SELECT key FROM dblp_paper WHERE venue IN (SELECT venue FROM _xv))""")
        # area = the CSrankings area most of the venue's authors publish in
        con.execute(f"""
            INSERT INTO {base} (venue, area, area_title, category, next_tier)
            WITH auth AS (SELECT DISTINCT p.venue AS xv, a.name FROM dblp_authorship a JOIN dblp_paper p USING (key)
                          WHERE p.venue IN (SELECT venue FROM _xv)),
                 votes AS (SELECT au.xv, v.area, count(*) AS n FROM auth au
                           JOIN author_pub ap ON ap.name = au.name
                           JOIN {base} v ON v.venue = ap.venue AND v.category IS DISTINCT FROM 'extra'
                           GROUP BY ALL),
                 best AS (SELECT xv, arg_max(area, n) AS area FROM votes GROUP BY 1),
                 titles AS (SELECT area, any_value(area_title) AS area_title FROM {base} GROUP BY 1)
            SELECT x.venue, b.area, t.area_title, 'extra', TRUE
            FROM _xv x LEFT JOIN best b ON b.xv = x.venue LEFT JOIN titles t ON t.area = b.area
            WHERE x.venue IN (SELECT DISTINCT venue FROM dblp_paper)""")
        con.execute("""CREATE OR REPLACE TABLE author_pub AS
            SELECT a.name, p.venue, p.year, count(*)::INTEGER AS count, sum(1.0 / p.n_authors) AS adjustedcount,
                   (count(*) FILTER (WHERE a.pos = 1))::INTEGER AS first_count,
                   (count(*) FILTER (WHERE a.pos = p.n_authors AND p.n_authors > 1))::INTEGER AS last_count
            FROM dblp_authorship a JOIN dblp_paper p USING (key) GROUP BY ALL""")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"papers": con.execute("SELECT count(*) FROM dblp_paper WHERE venue IN (SELECT venue FROM _xv)").fetchone()[0],
            "venues": con.execute(f"SELECT count(*) FROM {base} WHERE category = 'extra'").fetchone()[0]}
