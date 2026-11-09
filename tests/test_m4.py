import pytest
from datetime import date

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


def test_pinyin_ai_is_not_auto_korean():
    assert korean.score("Dakai Kang").score < 0.75


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
            ("Seokhee Hong", "sigmod", 2022, 4, 1.5, 0, 3),   # ambiguous (Hong) + Korean spelling → co-authorship
        ],
    )
    c.execute("""CREATE OR REPLACE TABLE dblp_authorship (key VARCHAR, pos SMALLINT, name VARCHAR,
                 raw_name VARCHAR, orcid VARCHAR)""")
    c.executemany("INSERT INTO dblp_authorship VALUES (?,?,?,?,NULL)",
                  [("k1", 1, "Seokhee Hong", "Seokhee Hong"), ("k1", 2, "Young", "Young"),
                   ("k2", 1, "Seokhee Hong", "Seokhee Hong"), ("k2", 2, "Mid", "Mid")])


def test_pool_classify_rules_coauthor_and_judge(con):  # noqa: F811
    _add_author_pub(con)
    calls = []

    def judge(items):
        calls.append(items)
        return {i: (False, 0.9) for i, _, _ in items}

    st = pool.classify(con, since=2016, min_pubs=1, judge=judge)
    got = dict(con.execute("SELECT person, is_korean FROM pii.korean").fetchall())
    assert got["Minji Kim"] is True
    assert got["Seokhee Hong"] is True  # 2 papers with KR faculty (Young, Mid)
    assert "Wei Zhang 0042" not in got
    assert st["by_coauthor"] == 1


def test_candidates_korean_scope_includes_non_faculty(con):  # noqa: F811
    _add_author_pub(con)
    pool.classify(con, since=2016, min_pubs=1, use_llm=False)
    f = metrics.Filter(start=2016, end=2026)
    rows, cols = age.candidates(con, f, scope="korean")
    r = {row[0]: dict(zip(cols, row)) for row in rows}
    assert "Minji Kim" in r and r["Minji Kim"]["role"] == "other · unknown"
    assert r["Minji Kim"]["first_au"] == 3
    assert "Wei Zhang 0042" not in r and "Abroad" not in r
    assert r["Young"]["role"] == "KR faculty"
    rows, _ = age.candidates(con, f, scope="world")
    assert {x[0] for x in rows} == {"Old", "Young", "Mid", "Abroad"}


def test_chinese_surname_not_korean_by_coauthorship_alone(con):  # noqa: F811
    _add_author_pub(con)
    con.execute("INSERT INTO author_pub VALUES ('Jindong Wang', 'sigmod', 2022, 3, 1.0, 1, 0)")
    con.executemany("INSERT INTO dblp_authorship VALUES (?,?,?,?,NULL)",
                    [("k3", 1, "Jindong Wang", "x"), ("k3", 2, "Young", "x"),
                     ("k4", 1, "Jindong Wang", "x"), ("k4", 2, "Mid", "x")])
    seen = []
    pool.classify(con, since=2016, min_pubs=1, judge=lambda items: seen.extend(n for _, n, _ in items) or {})
    assert "Jindong Wang" in seen or "Jindong Wang" not in dict(
        con.execute("SELECT person, is_korean FROM pii.korean WHERE is_korean").fetchall())


def test_llm_verdicts_are_cached(con):  # noqa: F811
    _add_author_pub(con)
    con.execute("INSERT INTO author_pub VALUES ('Jinwoo Song', 'sigmod', 2022, 3, 1.0, 1, 0)")
    calls = []
    j = lambda items: calls.append([n for _, n, _ in items]) or {i: (True, 0.9) for i, _, _ in items}
    pool.classify(con, since=2016, min_pubs=1, judge=j)
    pool.classify(con, since=2016, min_pubs=1, judge=j)
    assert len(calls) == 1


def test_openalex_affiliation_by_position_and_name(con):  # noqa: F811
    from fp.ingest import openalex

    _add_author_pub(con)
    pool.classify(con, since=2016, min_pubs=1, use_llm=False)
    con.execute("""CREATE OR REPLACE TABLE dblp_paper (key VARCHAR, venue VARCHAR, year INTEGER, n_authors SMALLINT,
                   title VARCHAR, page_count INTEGER, doi VARCHAR)""")
    con.executemany("INSERT INTO dblp_paper VALUES (?, 'sigmod', ?, 2, 't', 12, ?)",
                    [("m1", 2019, "10.1/a"), ("m2", 2024, "10.1/b")])
    con.executemany("INSERT INTO dblp_authorship VALUES (?,?,?,?,NULL)",
                    [("m1", 2, "Minji Kim", "Minji Kim"), ("m2", 1, "Minji Kim", "Minji Kim")])
    inst = lambda n, c: [{"name": n, "country": c, "ror": None}]
    fake = {
        "10.1/a": ("ok", 2019, [{"pos": 1, "name": "X Y", "institutions": inst("Elsewhere", "us")},
                                {"pos": 2, "name": "Min-Ji Kim", "institutions": inst("KAIST", "kr")}]),
        # author order differs in OpenAlex → unique name fallback
        "10.1/b": ("ok", 2024, [{"pos": 1, "name": "Q Z", "institutions": inst("Wrong", "us")},
                                {"pos": 2, "name": "Minji Kim", "institutions": inst("Stanford University", "us")}]),
    }
    st = openalex.update(con, fetch=lambda doi, sess: fake[doi], workers=1)
    assert st["people_with_affiliation"] == 1
    rows, cols = age.candidates(con, metrics.Filter(start=2016, end=2026), scope="korean")
    r = {row[0]: dict(zip(cols, row)) for row in rows}
    assert r["Minji Kim"]["institution"] == "Stanford University"
    assert r["Minji Kim"]["role"] == "other · abroad" and r["Minji Kim"]["affil_year"] == 2024
    assert openalex.name_match("Wook-Shin Han", "W.-S. Han") and not openalex.name_match("Sungjin Lee", "Sangmin Lee")


def test_scholar_links():
    from fp import links

    u, k = links.scholar("Wook-Shin Han", "AbCdEfGhIjKl")
    assert k == "profile" and u.endswith("user=AbCdEfGhIjKl&hl=en")
    u, k = links.scholar("Sungjin Lee 0001", "NOSCHOLARPAGE", "KAIST")
    assert k == "search" and "mauthors=Sungjin+Lee+KAIST" in u


def test_citations_update_and_budget_stop(con):  # noqa: F811
    from fp.ingest import citations

    _add_author_pub(con)
    pool.classify(con, since=2016, min_pubs=1, use_llm=False)
    con.execute("""CREATE OR REPLACE TABLE dblp_paper (key VARCHAR, venue VARCHAR, year INTEGER, n_authors SMALLINT,
                   title VARCHAR, page_count INTEGER, doi VARCHAR)""")
    con.executemany("INSERT INTO dblp_paper VALUES (?, 'sigmod', 2022, 1, 't', 12, ?)",
                    [(f"c{i}", f"10.9/{i:03d}") for i in range(150)] + [("nodoi", None)])
    con.executemany("INSERT INTO dblp_authorship VALUES (?, 1, 'Minji Kim', 'Minji Kim', NULL)",
                    [(f"c{i}",) for i in range(150)] + [("nodoi",)])
    calls = []

    def fake(dois, sess, key):
        calls.append(len(dois))
        if len(calls) == 2:
            raise citations.BudgetExhausted("daily budget")
        return {d: 2 for d in dois if not d.endswith("7")}

    st = citations.update(con, fetch=fake)
    assert calls == [100, 50] and st["stopped"] and st["requests"] == 1
    st = citations.update(con, fetch=lambda d, s, k: {x: 1 for x in d})  # resumes with the remaining 50
    assert st["dois"] == 50
    rows, cols = age.candidates(con, metrics.Filter(start=2016, end=2026), scope="korean")
    r = {row[0]: dict(zip(cols, row)) for row in rows}["Minji Kim"]
    assert r["cites_all"] == 90 * 2 + 50 * 1 and r["cited_papers"] == 140 and r["top_papers"] == 151
    assert r["cites"] == r["cites_all"]  # all papers fall inside the 2016–2026 window
    rows, cols = age.candidates(con, metrics.Filter(start=2023, end=2026), scope="korean")
    assert {row[0]: dict(zip(cols, row)) for row in rows}.get("Minji Kim", {}).get("cites") is None


def test_institution_mapping(con):  # noqa: F811
    from fp import institutions

    assert institutions.norm("The University of Texas at Austin") == "university of texas at austin"
    con.executemany("INSERT INTO institution VALUES (?, 'x', ?, NULL)",
                    [("Purdue University", "us"), ("Korea University", "kr")])
    con.executemany("INSERT INTO pii.affiliation (person, year, institution, country) VALUES (?, 2024, ?, ?)",
                    [("p1", "Purdue University West Lafayette", "us"),
                     ("p2", "Korea University of Technology and Education", "kr"),
                     ("p3", "The Korea University", "kr")])
    institutions.build(con)
    m = dict(con.execute("SELECT oa_name, institution FROM inst_map").fetchall())
    assert m["Purdue University West Lafayette"] == "Purdue University"
    assert m["The Korea University"] == "Korea University"
    assert "Korea University of Technology and Education" not in m


def test_snapshot_diff_report(con, tmp_path):  # noqa: F811
    from fp import report

    _add_author_pub(con)
    pool.classify(con, since=2016, min_pubs=1, use_llm=False)
    assert report.diff(con) == {}
    r1 = report.snapshot(con)
    con.execute("UPDATE cs_faculty SET affiliation = 'Abroad U' WHERE name = 'Mid'")
    con.execute("INSERT INTO author_pub VALUES ('Young', 'sigmod', 2025, 4, 2.0, 0, 4)")
    con.execute("INSERT INTO pii.degree (person, degree, year, source, confidence) VALUES ('Old', 'phd', 1990, 'orcid', 1)")
    r2 = report.snapshot(con)
    d = report.diff(con)
    assert d["runs"] == [r1, r2]
    assert [x[0] for x in d["kr_faculty_left"]] == ["Mid"]
    assert any(x[0] == "Mid" for x in d["moved"])
    assert [x[0] for x in d["output_up"]] == ["Young"]
    assert [x[0] for x in d["age_confirmed"]] == ["Old"]
    md = report.to_markdown(d)
    assert "소속 변경 (1)" in md and "| Mid |" in md


def test_rank_age_and_sort(con):  # noqa: F811
    _add_author_pub(con)
    pool.classify(con, since=2016, min_pubs=1, use_llm=False)
    f = metrics.Filter(start=2016, end=2026)
    rows, cols = age.candidates(con, f, scope="korean")
    r = [dict(zip(cols, x)) for x in rows]
    assert [x["rank"] for x in r] == list(range(1, len(r) + 1))
    assert all(x["age"] == date.today().year - x["birth_year"] for x in r if x["birth_year"])
    by_pubs, _ = age.candidates(con, f, scope="korean", sort="pubs")
    pubs = [dict(zip(cols, x))["pubs"] for x in by_pubs]
    assert pubs == sorted(pubs, reverse=True)
    with pytest.raises(ValueError):
        age.candidates(con, f, sort="nope")


def test_western_or_pinyin_names_never_pass_on_coauthorship(con):  # noqa: F811
    _add_author_pub(con)
    for n in ("Ralf Jung", "Li Yi"):
        con.execute("INSERT INTO author_pub VALUES (?, 'sigmod', 2022, 3, 1.0, 1, 0)", [n])
        con.executemany("INSERT INTO dblp_authorship VALUES (?,?,?,?,NULL)",
                        [(f"{n}1", 1, n, n), (f"{n}1", 2, "Young", "Young"), (f"{n}2", 1, n, n), (f"{n}2", 2, "Mid", "Mid")])
    seen = []
    pool.classify(con, since=2016, min_pubs=1,
                  judge=lambda items: seen.extend(n for _, n, _ in items) or {i: (False, 0.9) for i, _, _ in items})
    got = dict(con.execute("SELECT person, is_korean FROM pii.korean").fetchall())
    assert got.get("Ralf Jung") is not True and got.get("Li Yi") is not True
    assert korean.score("Michael R. Lyu").score < 0.4


def test_verify_and_manual_override(con):  # noqa: F811
    _add_author_pub(con)
    con.execute("INSERT INTO cs_faculty VALUES ('Minji Kim', 'Z', '', '', '')")  # faculty abroad (Z = de)
    pool.classify(con, since=2016, min_pubs=1, use_llm=False)
    st = pool.verify(con, judge=lambda items: {i: (False, 0.95) for i, _, _ in items})
    assert st["rejected"] >= 1
    age.create_views(con)
    eff = dict(con.execute("SELECT person, is_korean FROM korean_eff").fetchall())
    assert eff["Minji Kim"] is False
    assert pool.verify_targets(con) == []  # verified recently → not re-checked
    con.execute("INSERT OR REPLACE INTO pii.korean (person, is_korean, method) VALUES ('Minji Kim', TRUE, 'manual')")
    age.create_views(con)
    assert dict(con.execute("SELECT person, is_korean FROM korean_eff").fetchall())["Minji Kim"] is True


def test_llm_degree_ranked_below_confirmed_and_shown_as_estimate(con):  # noqa: F811
    from fp.ingest import llm_degree

    _add_author_pub(con)
    age.create_views(con)
    rows = llm_degree.targets(con)
    names = [r[0] for r in rows]
    assert names, "faculty without confirmed PhD should be targets"
    # judge: knows one person (plausible), "knows" another implausibly late, unsure about the rest
    first = {r[0]: r[2] for r in rows}
    a, b = names[0], names[-1]

    def judge(items):
        out = {}
        for i, n, _, ft in items:
            if n == a:
                out[i] = ((ft or 2010) - 3, "Univ X", 0.9)
            elif n == b and n != a:
                out[i] = ((ft or 2010) + 20, "Univ Y", 0.9)
            else:
                out[i] = (None, None, 0.2)
        return out

    st = llm_degree.update(con, judge=judge)
    assert st["asked"] == len(names) and st["found"] == 1
    age.create_views(con)
    src, phd = con.execute("SELECT age_source, phd_year FROM person_age WHERE person = ?", [a]).fetchone()
    assert src == "est:llm" and phd == (first[a] or 2010) - 3
    assert llm_degree.targets(con) == []  # asked recently
    # a confirmed source still wins over the model's answer
    con.execute("INSERT INTO pii.degree (person, degree, year, source, confidence) VALUES (?, 'phd', 1999, 'orcid', 1)", [a])
    age.create_views(con)
    assert con.execute("SELECT age_source, phd_year FROM person_age WHERE person = ?", [a]).fetchone() == ("orcid", 1999)


def test_key_file_roundtrip(tmp_path, monkeypatch):
    import os

    from fp import pii

    kf = tmp_path / "k" / "pii.key"
    monkeypatch.delenv("FP_PII_KEY", raising=False)
    monkeypatch.setenv("FP_PII_KEY_FILE", str(kf))
    pii.write_key_file("ab" * 32)
    assert oct(kf.stat().st_mode & 0o777) == "0o600"
    assert pii._get_key() == "ab" * 32
    os.chmod(kf, 0o644)
    import pytest

    with pytest.raises(RuntimeError, match="chmod 600"):
        pii._get_key()


def test_app_refuses_network_without_password(monkeypatch):
    from typer.testing import CliRunner

    from fp.cli import app

    monkeypatch.delenv("FP_APP_PASSWORD", raising=False)
    r = CliRunner().invoke(app, ["app", "--host", "0.0.0.0"])
    assert r.exit_code == 2
