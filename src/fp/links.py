"""Outbound profile links for manual verification (never fetched automatically)."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

SCHOLAR_PROFILE = "https://scholar.google.com/citations?user={id}&hl=en"
SCHOLAR_SEARCH = "https://scholar.google.com/citations?view_op=search_authors&hl=en&mauthors={q}"
DBLP_SEARCH = "https://dblp.org/search?q={q}"

_ID = re.compile(r"^[A-Za-z0-9_-]{12}$")


def clean_name(name: str) -> str:
    return re.sub(r"\s+\d{4}$", "", name).strip()


def scholar(name: str, scholarid: str | None, affiliation: str | None = None) -> tuple[str, str]:
    """(url, kind): the profile when CSrankings has a valid Scholar id, else an author search."""
    sid = (scholarid or "").strip()
    if _ID.match(sid):
        return SCHOLAR_PROFILE.format(id=sid), "profile"
    q = clean_name(name)
    if affiliation and affiliation != "-":
        q += " " + affiliation
    return SCHOLAR_SEARCH.format(q=quote_plus(q)), "search"


def person_links(con, name: str) -> dict[str, tuple[str, str]]:
    row = con.execute(
        """SELECT any_value(f.scholarid), any_value(f.homepage),
                  coalesce(any_value(f.affiliation),
                           (SELECT arg_max(institution, year) FROM pii.affiliation WHERE person = n.name))
           FROM (SELECT ? AS name) n LEFT JOIN cs_faculty f ON f.name = n.name GROUP BY n.name""",
        [name],
    ).fetchone()
    sid, home, affil = row if row else (None, None, None)
    out = {"Google Scholar": scholar(name, sid, affil)}
    if out["Google Scholar"][1] == "profile":
        # CSrankings Scholar ids go stale (e.g. a deleted profile → 404); always offer the search as a fallback
        out["Scholar"] = scholar(name, None, affil)
    if home and home.startswith("http"):
        out["Homepage"] = (home, "homepage")
    out["DBLP"] = (DBLP_SEARCH.format(q=quote_plus(clean_name(name))), "search")
    return out
