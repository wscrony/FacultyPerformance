"""Board data layer + page smoke tests on the small fixture DB (never the real local DB)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from fp import age, ui
from fp import boards as B
from tests.test_m2 import con as _base  # noqa: F401
from tests.test_m4 import _add_author_pub

@pytest.fixture
def con(_base):  # noqa: F811
    c = _base
    _add_author_pub(c)  # author_pub + dblp_authorship (k1..)
    c.execute("""CREATE OR REPLACE TABLE dblp_paper (key VARCHAR, venue VARCHAR, year INTEGER, n_authors SMALLINT,
                 title VARCHAR, page_count INTEGER, doi VARCHAR)""")
    c.execute("DELETE FROM dblp_authorship")
    papers = [("p1", 2022, ["Old", "Abroad"]), ("p2", 2023, ["Young", "Mid"]), ("p3", 2024, ["Abroad"]),
              ("p4", 2020, ["Young"]), ("p5", 2024, ["Minji Kim", "Old"]), ("p6", 2021, ["Wei Zhang 0042"])]
    for k, y, au in papers:
        c.execute("INSERT INTO dblp_paper VALUES (?, 'sigmod', ?, ?, 't', 12, NULL)", [k, y, len(au)])
        c.executemany("INSERT INTO dblp_authorship VALUES (?,?,?,?,NULL)",
                      [(k, i + 1, n, n) for i, n in enumerate(au)])
    return c

VIEWS = Path(__file__).resolve().parents[1] / "src" / "fp" / "views"


def test_board_queries_run_and_are_consistent(con):  # noqa: F811
    age.create_views(con)
    f = B.BF(start=2000, end=2030)
    inst, total = B.institution_share(con, f, 50)
    assert total == B.total_papers(con, f) and total > 0
    assert (inst.share <= 100).all() and inst["rank"].is_monotonic_increasing
    gs = B.group_share(con, f)
    assert (gs.share <= 100 + 1e-9).all()
    for fn in (B.group_share_by_year, B.country_rank, B.kr_collab_matrix):
        fn(con, f)
    B.kr_school_world_rank_by_year(con, f, B.kr_schools(con))
    for fn in (B.korean_landscape, B.mobility, B.talent_matrix):
        fn(con)
    B.kr_faculty_age_output(con, f)
    if B.kr_schools(con):
        s = B.kr_schools(con)[0]
        B.school_profile(con, s, f)
        B.school_trend(con, s, f)


def test_last_author_credit_is_subset_of_any(con):  # noqa: F811
    age.create_views(con)
    a, _ = B.institution_share(con, B.BF(start=2000, end=2030, credit="any"), 100)
    l, _ = B.institution_share(con, B.BF(start=2000, end=2030, credit="last"), 100)
    m = l.merge(a, on="institution", suffixes=("_l", "_a"))
    assert (m.papers_l <= m.papers_a).all()


@pytest.mark.parametrize("name,expect", [
    ("Univ. of California - San Diego", "UC San Diego"), ("Massachusetts Inst. of Technology", "MIT"),
    ("Korea Advanced Institute of Science and Technology", "Korea Advanced Instit…"),
])
def test_short_names(name, expect):
    assert ui.short(name) == expect


def test_ink_contrast():
    assert ui.ink("#ffffff") == "#1a1a19" and ui.ink("#0d366b") == "#ffffff"


@pytest.mark.parametrize("page", ["world.py", "schools.py", "people.py"])
def test_board_pages_render(con, page):  # noqa: F811
    import streamlit as st

    con.close()
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(VIEWS / page), default_timeout=90).run()
    assert not at.exception, [e.message for e in at.exception]


def test_not_top_override_and_field_mapping(con):  # noqa: F811
    from fp.ingest import csrankings

    con.executemany("INSERT INTO venue VALUES (?,?,?,?,?)",
                    [("aaai", "ai", "AI", "ai", False), ("kdd", "mlmining", "ML", "ai", True),
                     ("www", "inforet", "Web+IR", "ai", False)])
    csrankings.apply_overrides(con)
    assert con.execute("SELECT next_tier FROM venue WHERE venue = 'aaai'").fetchone()[0] is True
    got = dict(con.execute(f"SELECT v.venue, {B.field_sql('v')} FROM venue v").fetchall())
    assert got["kdd"] == "DM" and got["www"] == "DM" and got["sigmod"] == "DB" and got["aaai"] == "ML"
    where, prm = B.BF(start=2000, end=2030, fields=["DB"]).where("p", "v")
    assert "unnest" in where and ["DB"] in prm
