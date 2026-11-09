import pytest

from fp import age
from fp.ingest import homepage as h
from tests.test_m2 import con  # noqa: F401  (fixture reuse)

SNIP = (
    "Biography\nI received my Ph.D. in Computer Science from Stanford University in 2012 and "
    "B.S. from Seoul National University in 2006.\nMy student Kim received his Ph.D. in 2024."
)


def test_validate_keeps_verbatim_quotes():
    raw = {
        "page_is_about_person": True,
        "phd": {"year": 2012, "institution": "Stanford", "quote": "Ph.D. in Computer Science from Stanford University in 2012"},
        "ms": None,
        "bs": {"year": 2006, "quote": "B.S.  from Seoul National University in 2006"},  # whitespace-insensitive
        "birth": None,
    }
    ext = h.validate(raw, SNIP)
    assert ext.degrees["phd"][0] == 2012 and ext.degrees["bs"][0] == 2006


def test_validate_drops_hallucinations():
    raw = {
        "page_is_about_person": True,
        "phd": {"year": 2011, "quote": "Ph.D. in Computer Science from Stanford University in 2012"},  # year not in quote
        "ms": {"year": 2008, "quote": "M.S. from KAIST in 2008"},  # quote not in text
        "bs": None,
        "birth": {"year": 2000, "quote": "in 2006"},  # implausible: after BS - 17
    }
    ext = h.validate(raw, SNIP)
    assert ext.degrees == {} and ext.birth is None


def test_validate_not_person():
    ext = h.validate({"page_is_about_person": False}, SNIP)
    assert not ext.about_person and ext.degrees == {}


def test_regex_extract_skips_students():
    out = h.regex_extract(SNIP.replace(" and B.S.", ".\nB.S."))
    assert out["phd"]["year"] == 2012
    assert out["bs"]["year"] == 2006


def test_snippets_merges_windows():
    text = "x" * 2000 + " Ph.D. 2010 " + "y" * 2000
    s = h.snippets(text, window=50)
    assert "Ph.D. 2010" in s and len(s) < 200
    assert h.snippets("nothing relevant here") == ""


def test_follow_candidates_prefers_cv():
    page = h.Page(
        "https://cs.example.ac.kr/~kim/",
        "",
        [
            ("Publications", "https://cs.example.ac.kr/~kim/pubs.html"),
            ("About", "https://cs.example.ac.kr/~kim/about.html"),
            ("CV", "https://cs.example.ac.kr/~kim/cv.pdf"),
            ("Twitter bio", "https://twitter.com/kim"),
        ],
    )
    assert h.follow_candidates(page) == [
        "https://cs.example.ac.kr/~kim/cv.pdf",
        "https://cs.example.ac.kr/~kim/about.html",
    ]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        return self.pages.get(url)


def test_update_end_to_end(con):  # noqa: F811
    con.execute("UPDATE cs_faculty SET homepage = 'https://a.kr/' || lower(name)")
    con.execute("INSERT INTO pii.degree (person, degree, year, source, confidence) VALUES ('Old','phd',1998,'orcid',0.9)")
    pages = {
        "https://a.kr/young": h.Page("https://a.kr/young", "Home", [("CV", "https://a.kr/young/cv")]),
        "https://a.kr/young/cv": h.Page("https://a.kr/young/cv", SNIP),
        # Mid: no page
    }

    def fake_llm(name, aff, snip):
        return {
            "page_is_about_person": True,
            "phd": {"year": 2012, "quote": "Ph.D. in Computer Science from Stanford University in 2012"},
            "ms": None, "bs": None, "birth": None,
        }

    stats = h.update(con, extractor=fake_llm, fetcher=FakeFetcher(pages), workers=2)
    assert stats["targets"] == 2  # Old already has ORCID PhD; Abroad not kr
    assert stats["ok"] == 1 and stats["no_page"] == 1 and stats["phd"] == 1
    body = con.execute("SELECT body FROM pii.raw WHERE person='Young' AND source='homepage'").fetchone()[0]
    assert "Stanford" in body
    age.create_views(con, this_year=2026)
    assert con.execute("SELECT phd_year, age_source FROM person_age WHERE person='Young'").fetchone() == (2012, "homepage")
    # refetch window: nothing to do now
    assert h.targets(con) == []


class DownFetcher:
    def fetch(self, url):
        raise h.Unreachable("timeout")


def test_unreachable_is_retried_next_run(con):  # noqa: F811
    con.execute("UPDATE cs_faculty SET homepage = 'https://a.kr/' || lower(name)")
    stats = h.update(con, extractor=lambda *a: {}, fetcher=DownFetcher(), workers=1)
    assert stats["unreachable"] == 3
    assert len(h.targets(con)) == 3  # retried
    stats = h.update(con, extractor=lambda *a: {}, fetcher=FakeFetcher({}), workers=1)
    assert stats["no_page"] == 3
    assert h.targets(con) == [] and len(h.targets(con, retry_no_page=True)) == 3
