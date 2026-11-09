import pytest

from fp import age, korean, metrics, pool
from fp.ingest import dblp
from tests.test_m2 import con  # noqa: F401


@pytest.mark.parametrize(
    "name,lo",
    [
        ("Wook-Shin Han", 0.4), ("Jaegul Choo", 0.75), ("Minsu Cho", 0.75), ("Steven Euijong Whang", 0.75),
        ("Seung-won Hwang", 0.75), ("Kyoung Mu Lee", 0.75), ("Sungjin Lee 0001", 0.4),
        # 0.4 = ambiguous band (surname shared with Chinese) → resolved by co-authorship / LLM
    ],
)
def test_korean_names_high(name, lo):
    assert korean.score(name).score >= lo, korean.score(name)


@pytest.mark.parametrize(
    "name", ["Xiaofeng Wang", "Qiang Yang", "Zhihua Zhou", "Jie Tang", "Wei Wang 0001", "Takashi Yamada",
             "Thi Thanh Nguyen", "Michael Stonebraker", "Hsiao-Wuen Hon"],
)
def test_non_korean_low(name):
    assert korean.score(name).score < 0.4, korean.score(name)


def test_western_given_with_korean_surname_is_ambiguous():
    v = korean.score("Steven Whang")
    assert 0.4 <= v.score < 0.75 and v.reason == korean.WESTERN_ONLY


SRC_PY = '''
areadict: Dict[Area, List[Conference]] = {
    Area("sigmod"): [Conference("SIGMOD Conference")],
    Area("pods"): [Conference("PODS")],
    Area("pacmmod"): [Conference("Proc. ACM Manag. Data")],
    Area("eccv"): [Conference("ECCV")] + [Conference(f"ECCV ({i})") for i in range(1, 4)],
    Area("siggraph"): [Conference("SIGGRAPH")],
    Area("tog"): [Conference("ACM Trans. Graph.")],
''' + "".join(f'    Area("v{i}"): [Conference("V{i}")],\n' for i in range(80)) + '''}
TOG_SIGGRAPH_Volume = {2024: (43, 4)}
TOG_SIGGRAPH_Asia_Volume = {2024: (43, 6)}
CGF_EUROGRAPHICS_Volume = {}
TVCG_Vis_Volume = {}
TVCG_VR_Volume = {}
ISMB_Bioinformatics = {}
ICSE_ShortPaperStart = {}
SIGMOD_NonResearchPaperStart = {2010: 1000}
SIGMOD_NonResearchPapersRange = {}
MobiCom_NonResearchPaperStart = {}
SC_2006_ResearchArticles = []
def countPaper(): raise SystemExit("must never run")
'''


def test_dblp_rules_read_without_executing():
    r = dblp.read_rules(SRC_PY)
    assert r.confdict["ECCV (2)"] == "eccv" and r.confdict["SIGMOD Conference"] == "sigmod"
    c = lambda conf, y, vol="0", num="0", pages="1-12", url="", key="", title="t": dblp.classify(
        r, conf, y, vol, num, pages, url, key, title)
    assert c("SIGMOD Conference", 2015) == ("sigmod", 2015)
    assert c("SIGMOD Conference", 2015, pages="1-4") is None  # < 6 pages
    assert c("SIGMOD Conference", 2010, pages="1001-1012") is None  # non-research section
    assert c("Proc. ACM Manag. Data", 2025, num="2") == ("pods", 2025)
    assert c("Proc. ACM Manag. Data", 2023, num="3") == ("sigmod", 2024)
    assert c("ACM Trans. Graph.", 2024, vol="43", num="4") == ("siggraph", 2024)
    assert c("ACM Trans. Graph.", 2024, vol="43", num="1") is None
    assert c("ECCV (2)", 2024) == ("eccv", 2024)
    assert c("ECCV (2)", 2024, url="db/conf/eccv/eccv2024w.html") is None  # workshop TOC
    assert c("ECCV (2)", 2024, title="Front Matter.") is None
    assert c("Unknown Conf", 2024) is None


def test_dblp_toggraph_short_pages_rule():
    r = dblp.read_rules(SRC_PY)
    # SIGGRAPH short-page exception only for volumes 26..39
    assert dblp.classify(r, "ACM Trans. Graph.", 2024, "43", "4", "1:1-1:3", "", "") is None


def _add_author_pub(c):
    c.execute(
        """CREATE OR REPLACE TABLE author_pub (name VARCHAR, venue VARCHAR, year INTEGER, count INTEGER,
           adjustedcount DOUBLE, first_count INTEGER, last_count INTEGER)"""
    )
    c.executemany(
        "INSERT INTO author_pub VALUES (?,?,?,?,?,?,?)",
        [
            ("Old", "sigmod", 2000, 1, 1.0, 1, 0), ("Old", "sigmod", 2024, 2, 1.0, 0, 2),
            ("Young", "sigmod", 2020, 3, 3.0, 2, 0), ("Mid", "sigmod", 2018, 1, 0.5, 0, 1),
            ("Abroad", "sigmod", 2019, 1, 1.0, 1, 0),
            ("Minji Kim", "sigmod", 2022, 4, 2.0, 3, 0),     # Korean postdoc, not faculty
            ("Wei Zhang 0042", "sigmod", 2022, 5, 2.5, 4, 0),  # not Korean
            ("Steven Whang", "sigmod", 2022, 4, 1.5, 0, 3),   # ambiguous → co-author / judge
        ],
    )
    c.execute("""CREATE OR REPLACE TABLE dblp_authorship (key VARCHAR, pos SMALLINT, name VARCHAR,
                 raw_name VARCHAR, orcid VARCHAR)""")
    c.executemany("INSERT INTO dblp_authorship VALUES (?,?,?,?,NULL)",
                  [("k1", 1, "Steven Whang", "Steven Whang"), ("k1", 2, "Young", "Young"),
                   ("k2", 1, "Steven Whang", "Steven Whang"), ("k2", 2, "Mid", "Mid")])


def test_pool_classify_rules_coauthor_and_judge(con):  # noqa: F811
    _add_author_pub(con)
    calls = []

    def judge(items):
        calls.append(items)
        return {i: (False, 0.9) for i, _, _ in items}

    st = pool.classify(con, since=2016, min_pubs=1, judge=judge)
    got = dict(con.execute("SELECT person, is_korean FROM pii.korean").fetchall())
    assert got["Minji Kim"] is True
    assert got["Steven Whang"] is True  # 2 papers with KR faculty (Young, Mid)
    assert "Wei Zhang 0042" not in got
    assert st["by_coauthor"] == 1


def test_candidates_korean_scope_includes_non_faculty(con):  # noqa: F811
    _add_author_pub(con)
    pool.classify(con, since=2016, min_pubs=1, use_llm=False)
    f = metrics.Filter(start=2016, end=2026)
    rows, cols = age.candidates(con, f, scope="korean")
    r = {row[0]: dict(zip(cols, row)) for row in rows}
    assert "Minji Kim" in r and r["Minji Kim"]["role"] == "non-faculty/unknown"
    assert r["Minji Kim"]["first_au"] == 3
    assert "Wei Zhang 0042" not in r and "Abroad" not in r
    assert r["Young"]["role"] == "KR faculty"
    rows, _ = age.candidates(con, f, scope="world")
    assert {x[0] for x in rows} == {"Old", "Young", "Mid", "Abroad"}
