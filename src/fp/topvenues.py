"""User choice of which venues count as "top" (overrides CSrankings' next-tier flags).

Stored in data/top_venues.json (no person data). Applied per DuckDB cursor as a TEMP table `venue` that shadows
main.venue, so every query (boards, explorer, age views) sees the same choice without writing to the DB —
the app connects read-only.
"""

from __future__ import annotations

import json

from fp import paths

FILE = paths.DATA / "top_venues.json"
DEFAULT = {"top": [], "not_top": ["aaai", "ijcai"]}  # user decision (2026-11): AAAI/IJCAI are not top


def load() -> dict:
    try:
        d = json.loads(FILE.read_text())
        return {"top": sorted(set(d.get("top") or [])), "not_top": sorted(set(d.get("not_top") or []))}
    except (FileNotFoundError, ValueError):
        return {k: list(v) for k, v in DEFAULT.items()}


def save(top: list[str], not_top: list[str]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps({"top": sorted(set(top)), "not_top": sorted(set(not_top))}, indent=1))


def version() -> float:
    try:
        return FILE.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def csrankings_default(con) -> dict[str, bool]:
    """venue → next_tier as CSrankings has it (before any override)."""
    from fp.ingest.csrankings import NOT_TOP

    rows = con.execute(f"SELECT venue, next_tier FROM {base_venue(con)}").fetchall()
    return {v: (bool(nt) and v not in NOT_TOP) or False for v, nt in rows} | {v: True for v in NOT_TOP}


def base_venue(con) -> str:
    """Fully qualified persistent venue table ("main" alone would resolve to the temp catalog first)."""
    db = con.execute("SELECT current_database()").fetchone()[0]
    return f'"{db}".main.venue'


def apply(con) -> None:
    """TEMP venue table with the user's top/not-top choice."""
    s = load()
    base = base_venue(con)
    con.execute(
        """CREATE OR REPLACE TEMP TABLE venue AS
           SELECT venue, area, area_title, category,
                  CASE WHEN venue IN (SELECT unnest(?::VARCHAR[])) THEN FALSE
                       WHEN venue IN (SELECT unnest(?::VARCHAR[])) THEN TRUE
                       ELSE next_tier END AS next_tier
           FROM {base}""".replace("{base}", base),
        [s["top"], s["not_top"]],
    )


# ── venue catalog: "CS 분야 우수 학술대회 목록" (gist Pusnow/6eb933355b5cb8d31ef1abcb3c3e1206, public) ──
CATALOG = paths.DATA / "venue_catalog_csconf.csv"
CATALOG_URL = "https://gist.github.com/Pusnow/6eb933355b5cb8d31ef1abcb3c3e1206"


def catalog(con):
    """Gist list joined to our data: venue code (if we have DBLP papers under that DBLP key) and paper count."""
    import pandas as pd

    if not CATALOG.exists():
        return pd.DataFrame()
    cat = pd.read_csv(CATALOG, dtype=str).fillna("")
    cat = cat.rename(columns={"약자": "abbr", "한국정보과학회 (2024)": "kiise", "BK21플러스 IF (2018)": "bk21",
                              "KAIST CS (2025)": "kaist", "SNU CSE (2024.4)": "snu", "POSTECH CSE (2026.1)": "postech",
                              "평균 (정규화)": "avg", "학회명": "full_name", "DBLP Key": "dblp_key"})
    have = con.execute(
        """SELECT regexp_extract(key, '^([^/]+/[^/]+)/', 1) AS dblp_key, mode(venue) AS venue, count(*) AS papers
           FROM dblp_paper GROUP BY 1"""
    ).df()
    cat = cat.merge(have, on="dblp_key", how="left")
    cat["avg"] = pd.to_numeric(cat["avg"], errors="coerce")
    # oral/spotlight/poster rows share one DBLP key → one row, best rating, plain name
    cat["abbr"] = cat.abbr.str.replace(r"\s*\((oral|spotlight|poster)\)$", "", regex=True)
    return cat.sort_values("avg", ascending=False).drop_duplicates("dblp_key")
