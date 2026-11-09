"""All-author venue records from CSrankings' filtered DBLP (dblp.xml.xz, top venues only).

CSrankings' own Python is NOT executed. Instead its *data tables* (venue→area dict, TOG/CGF/TVCG/ISMB
volume tables, short-paper page cutoffs) are read from util/csrankings.py with `ast` (literals only), and
the main counting rules are re-implemented here (≥6 pages with CSrankings' exceptions, PACMPL/PACMMOD/TOG/
CGF/TVCG/ISMB mapping, ICSE/SIGMOD/MobiCom/ASE cutoffs, non-research-papers.csv). Omitted for simplicity:
EMSOFT-in-TECS/TCAD, SIGCSE allow-list, DAC too-short list, keyword-based non-research detection.
=> For faculty we keep CSrankings' exact counts (cs_pub); author_pub is used for non-faculty candidates and
author positions. `check_against_csrankings` measures parity.

Stage 1 (parse) writes parquet under data/snapshots/dblp/<sha>/ (no DB lock). Stage 2 (load) → public.duckdb.
"""

from __future__ import annotations

import ast
import csv
import lzma
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from fp import paths
from fp.ingest.csrankings import RAW_BASE, USER_AGENT

FILES = ["dblp.xml.xz", "dblp-aliases.csv", "non-research-papers.csv", "util/csrankings.py"]
TABLES = [
    "TOG_SIGGRAPH_Volume", "TOG_SIGGRAPH_Asia_Volume", "CGF_EUROGRAPHICS_Volume", "TVCG_Vis_Volume",
    "TVCG_VR_Volume", "ISMB_Bioinformatics", "ICSE_ShortPaperStart", "SIGMOD_NonResearchPaperStart",
    "SIGMOD_NonResearchPapersRange", "MobiCom_NonResearchPaperStart", "SC_2006_ResearchArticles",
]
PAGE_THRESHOLD = 6
ASE_LONG = 10
_NORMAL = re.compile(r"([0-9]+)-([0-9]+)")
_COLON = re.compile(r"[0-9]+:([1-9][0-9]*)-[0-9]+:([1-9][0-9]*)")
_ISMB = re.compile(r"i(\d+)-i(\d+)")
# Non-research heuristics (subset of CSrankings' rules, re-implemented): satellite TOCs and obvious front matter.
_TOC_NONRESEARCH = re.compile(r"^db/conf/[^/]+/[a-z]+\d{4}(?:courses|w)\.html")
_TITLE_NONRESEARCH = re.compile(
    r"^position\s*:\s|\b(?:position|vision|perspective) paper\b"
    r"|^(?:front matter|editorial|preface|foreword|sponsors|awards|conference committees|opening remarks)$"
    r"|^session details\s*:|\bchairs?'? message$|^keynote\b|^invited talk\b|^panel\s*:",
    re.I,
)


def snapshot_dir(sha: str) -> Path:
    return paths.SNAPSHOTS / "dblp" / sha[:12]


def raw_dir(sha: str) -> Path:
    return paths.RAW / "csrankings_dblp" / sha[:12]


def download(sha: str) -> Path:
    dest = raw_dir(sha)
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    for remote in FILES:
        target = dest / remote
        if target.exists() and target.stat().st_size > 0:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        r = sess.get(RAW_BASE.format(sha=sha, path=remote), timeout=600)
        r.raise_for_status()
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.rename(target)
    return dest


# ───────────────────────── rules (data read, never executed) ─────────────────────────


@dataclass
class Rules:
    confdict: dict[str, str]
    tables: dict[str, object]
    nonresearch_keys: set[str] = field(default_factory=set)


def read_rules(src_py: str, nonresearch_csv: Path | None = None) -> Rules:
    i = src_py.index("areadict: Dict[Area, List[Conference]] = {")
    j = src_py.index("\n}\n", i)
    confdict: dict[str, str] = {}
    block = src_py[i:j]
    for m in re.finditer(r'Area\("([\w-]+)"\)\s*:\s*(.*?)(?=\n\s*Area\(|\Z)', block, re.S):
        area, body = m.group(1), m.group(2)
        for conf in re.findall(r'Conference\("([^"]+)"\)', body):
            confdict[conf] = area
        # [Conference(f"ECCV ({i})") for i in range(1, 100)]
        for pat, a, b in re.findall(r'Conference\(f"([^"]*\{i\}[^"]*)"\)\s*for i in range\((\d+),\s*(\d+)\)', body):
            for k in range(int(a), int(b)):
                confdict[pat.replace("{i}", str(k))] = area
    if len(confdict) < 80:
        raise ValueError(f"areadict parse looks wrong: {len(confdict)} venues")
    tables: dict[str, object] = {}
    for node in ast.parse(src_py).body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        if target in TABLES and node.value is not None:
            tables[target] = ast.literal_eval(node.value)  # literals only; raises on anything else
    missing = set(TABLES) - set(tables)
    if missing:
        raise ValueError(f"tables not found in csrankings.py: {missing}")
    keys: set[str] = set()
    if nonresearch_csv and nonresearch_csv.exists():
        with open(nonresearch_csv, newline="", encoding="utf-8") as f:
            keys = {row["key"] for row in csv.DictReader(f)}
    return Rules(confdict, tables, keys)


def pagecount(pages: str) -> int:
    m = _NORMAL.match(pages) or _COLON.match(pages)
    return int(m.group(2)) - int(m.group(1)) + 1 if m else 0


def startpage(pages: str) -> int:
    m = _NORMAL.match(pages) or _COLON.match(pages)
    return int(m.group(1)) if m else 0


def _volnum(table: dict, year: int, volume: str, number: str) -> bool:
    if year not in table:
        return False
    vol, num = table[year]
    return volume == str(vol) and number == str(num)


def classify(r: Rules, conf: str, year: int, volume: str, number: str, pages: str, url: str, key: str,
             title: str = "") -> tuple[str, int] | None:
    """Return (venue, year) if CSrankings would count this paper (approximately), else None."""
    if conf not in r.confdict or key in r.nonresearch_keys:
        return None
    if _TOC_NONRESEARCH.search(url or "") or _TITLE_NONRESEARCH.search(title.strip().rstrip(". ")):
        return None
    t = r.tables
    area = r.confdict[conf]
    if area in ("pacmpl", "pacmse"):
        conf = number
        if conf not in r.confdict:
            return None
        area = r.confdict[conf]
    elif area == "pacmmod":
        try:
            n = int(number)
        except ValueError:
            return None
        if year == 2023:
            conf, year = ("SIGMOD Conference", 2023) if n in (1, 2) else ("SIGMOD Conference", 2024)
        else:
            conf = "PODS" if n == 2 else "SIGMOD Conference"
        area = r.confdict[conf]
    elif conf == "ACM Trans. Graph.":
        if _volnum(t["TOG_SIGGRAPH_Volume"], year, volume, number):
            conf = "SIGGRAPH"
        elif _volnum(t["TOG_SIGGRAPH_Asia_Volume"], year, volume, number):
            conf = "SIGGRAPH Asia"
        else:
            return None
        area = r.confdict[conf]
    elif conf == "Comput. Graph. Forum":
        if not _volnum(t["CGF_EUROGRAPHICS_Volume"], year, volume, number):
            return None
        conf = "EUROGRAPHICS"
        area = r.confdict[conf]
    elif conf == "IEEE Trans. Vis. Comput. Graph.":
        if _volnum(t["TVCG_Vis_Volume"], year, volume, number):
            area = "vis"
        elif _volnum(t["TVCG_VR_Volume"], year, volume, number):
            conf, area = "VR", "vr"
        else:
            return None
    elif conf in ("ACM Trans. Embedded Comput. Syst.", "ACM Trans. Embed. Comput. Syst.",
                  "IEEE Trans. Comput. Aided Des. Integr. Circuits Syst."):
        return None  # EMSOFT-in-journal special cases: omitted

    pc = pagecount(pages) if pages else -1
    sp = startpage(pages) if pages else -1
    if conf in ("Bioinformatics", "Bioinform."):
        if not _volnum(t["ISMB_Bioinformatics"], year, volume, number):
            return None
        if "i" in pages:
            m = _ISMB.match(pages)
            if not m:
                return None
            sp, pc = int(m.group(1)), int(m.group(2)) - int(m.group(1)) + 1
    elif conf in ("ICSE", "ICSE (1)", "ICSE (2)"):
        if year in t["ICSE_ShortPaperStart"] and sp >= t["ICSE_ShortPaperStart"][year]:
            return None
    elif conf == "SIGMOD Conference":
        if year in t["SIGMOD_NonResearchPaperStart"] and sp >= t["SIGMOD_NonResearchPaperStart"][year]:
            return None
        for lo, hi in t["SIGMOD_NonResearchPapersRange"].get(year, []):
            if sp >= lo and sp + pc - 1 <= hi:
                return None
    elif conf == "ASE" and pc < ASE_LONG:
        return None
    elif conf == "MobiCom":
        if year in t["MobiCom_NonResearchPaperStart"] and sp >= t["MobiCom_NonResearchPaperStart"][year]:
            return None
    elif conf == "ICS" and url and "innovations" in url:
        return None
    elif conf == "SC" and year == 2006 and pages.isdigit():
        if not any(a <= int(pages) <= b for a, b in t["SC_2006_ResearchArticles"]):
            return None

    too_few = pc == -1 and conf == "ACM Conference on Computer and Communications Security"
    if pc != -1 and pc < PAGE_THRESHOLD:
        vol = int(volume) if volume.isdigit() else -1
        exception = (
            (conf == "SC" and (year <= 2012 or year in {2017, 2020, 2021, 2024}))
            or (conf == "SIGSOFT FSE" and year == 2012)
            or (conf == "SIGGRAPH" and 26 <= vol <= 39)
            or conf == "SIGGRAPH Asia"
            or (conf == "EUROGRAPHICS" and year == 2024)
            or (conf == "CHI" and year in (2018, 2019))
            or (conf == "ICCAD" and year in {2016, 2018, 2022})
            or (conf == "FAST" and year == 2012)
            or (conf == "DAC" and year == 2019)
            or (conf in ("SIGMOD Conference", "PODS") and year in {2023, 2024})
            or (conf == "ISCA" and (pc < 0 or pc >= 3))
        )
        too_few = not exception
    if too_few:
        return None
    return area, year


# ───────────────────────── parse / load ─────────────────────────

SCHEMA_PAPER = pa.schema(
    [("key", pa.string()), ("venue", pa.string()), ("year", pa.int32()), ("n_authors", pa.int16()),
     ("title", pa.string()), ("page_count", pa.int32())]
)
SCHEMA_AUTH = pa.schema(
    [("key", pa.string()), ("pos", pa.int16()), ("name", pa.string()), ("raw_name", pa.string()),
     ("orcid", pa.string())]
)


def load_aliases(src: Path) -> dict[str, str]:
    with open(src / "dblp-aliases.csv", newline="", encoding="utf-8") as f:
        return {row["alias"]: row["name"] for row in csv.DictReader(f)}


def _text(elem, tag: str, default: str = "") -> str:
    e = elem.find(tag)
    return "".join(e.itertext()) if e is not None else default


def parse(src: Path, out: Path, progress: Callable[[int], None] | None = None, batch: int = 50_000) -> dict[str, int]:
    from lxml import etree

    rules = read_rules((src / "util" / "csrankings.py").read_text(encoding="utf-8"), src / "non-research-papers.csv")
    aliases = load_aliases(src)
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

    with lzma.open(src / "dblp.xml.xz", "rb") as xz:
        for _, elem in etree.iterparse(xz, events=("end",), tag=("inproceedings", "article"), huge_tree=True,
                                       load_dtd=False, resolve_entities=False, no_network=True):
            seen += 1
            authors = [("".join(a.itertext()).strip(), a.get("orcid")) for a in elem.findall("author")]
            conf = _text(elem, "booktitle") or _text(elem, "journal")
            year_s = _text(elem, "year", "-1")
            res = None
            if authors and conf and year_s.lstrip("-").isdigit():
                res = classify(rules, conf, int(year_s), _text(elem, "volume", "0"), _text(elem, "number", "0"),
                               _text(elem, "pages"), _text(elem, "url"), elem.get("key", ""), _text(elem, "title"))
            if res:
                venue, year = res
                kept += 1
                key = elem.get("key", "")
                pages = _text(elem, "pages")
                papers["key"].append(key)
                papers["venue"].append(venue)
                papers["year"].append(year)
                papers["n_authors"].append(len(authors))
                papers["title"].append(_text(elem, "title"))
                papers["page_count"].append(pagecount(pages) if pages else -1)
                for i, (raw, orcid) in enumerate(authors):
                    auths["key"].append(key)
                    auths["pos"].append(i + 1)
                    auths["name"].append(aliases.get(raw, raw))
                    auths["raw_name"].append(raw)
                    auths["orcid"].append(orcid)
                if len(papers["key"]) >= batch:
                    flush()
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
            if progress and seen % 50_000 == 0:
                progress(seen)
    flush()
    pw.close()
    aw.close()
    for f in ("paper", "authorship"):
        (out / f"{f}.parquet.part").rename(out / f"{f}.parquet")
    return {"seen": seen, "kept": kept}


LOAD_SQL = """
CREATE OR REPLACE TABLE dblp_paper AS SELECT * FROM read_parquet('{d}/paper.parquet');
CREATE OR REPLACE TABLE dblp_authorship AS SELECT * FROM read_parquet('{d}/authorship.parquet');
CREATE OR REPLACE TABLE alias AS
    SELECT alias, name FROM read_csv('{raw}/dblp-aliases.csv', header=true, all_varchar=true);
CREATE OR REPLACE TABLE author_pub AS
SELECT a.name, p.venue, p.year,
       count(*)::INTEGER AS count,
       sum(1.0 / p.n_authors) AS adjustedcount,
       (count(*) FILTER (WHERE a.pos = 1))::INTEGER AS first_count,
       (count(*) FILTER (WHERE a.pos = p.n_authors AND p.n_authors > 1))::INTEGER AS last_count
FROM dblp_authorship a JOIN dblp_paper p USING (key)
GROUP BY ALL;
CREATE OR REPLACE TABLE author_orcid AS
SELECT name, mode(orcid) AS orcid FROM dblp_authorship WHERE orcid IS NOT NULL GROUP BY name;
"""


def load(con, sha: str) -> dict[str, int]:
    con.execute("BEGIN")
    try:
        con.execute(LOAD_SQL.format(d=snapshot_dir(sha), raw=raw_dir(sha)))
        con.execute("INSERT OR REPLACE INTO snapshot VALUES ('dblp', ?, now())", [sha])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("dblp_paper", "dblp_authorship", "author_pub", "author_orcid", "alias")}


def check_against_csrankings(con, country: str = "kr") -> dict[str, float]:
    """Faculty totals: author_pub (ours) vs cs_pub (CSrankings generated-author-info)."""
    row = con.execute(
        """
        WITH fac AS (SELECT DISTINCT f.name FROM cs_faculty f JOIN institution i ON i.institution = f.affiliation
                     WHERE i.country = ?),
        a AS (SELECT name, sum(count) c FROM author_pub WHERE name IN (SELECT name FROM fac) GROUP BY 1),
        b AS (SELECT name, sum(count) c FROM cs_pub WHERE name IN (SELECT name FROM fac) GROUP BY 1)
        SELECT count(*), count(*) FILTER (WHERE coalesce(a.c, 0) = coalesce(b.c, 0)),
               sum(coalesce(a.c, 0)), sum(coalesce(b.c, 0))
        FROM a FULL JOIN b USING (name)
        """,
        [country],
    ).fetchone()
    return dict(zip(["people", "exact_match", "ours_total", "csrankings_total"], row))
