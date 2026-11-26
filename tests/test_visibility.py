import duckdb
import pandas as pd

from fp import visibility as VS
from fp.ingest import visibility as V


def test_venue_key_maps_common_spellings():
    al = {"SIGMOD": "sigmod", "VLDB": "vldb", "CVPR": "cvpr", "NEURIPS": "nips", "CHI": "chiconf", "ICCV": "iccv"}
    assert V.venue_key("SIGMOD 2023", al) == "sigmod"
    assert V.venue_key("ACM SIGMOD", al) == "sigmod"
    assert V.venue_key("IEEE/CVF CVPR 2019", al) == "cvpr"
    assert V.venue_key("NeurIPS", al) == "nips"
    assert V.venue_key("CHI '22", al) == "chiconf"
    assert V.venue_key("DASFAA", al) is None
    assert V.venue_key(None, al) is None


def test_parse_lists():
    h = ('<table class="t" id="2024"><thead></thead><tbody><tr><th class="category-name" scope="row" rowspan=1>'
         '<a href="./conferences.html#sigmod">SIGMOD</a><span> Databases</span></th>'
         '<td><a href="x">A Great Query &amp; Engine Paper</a></td><td class="authors">A, B</td></tr></tbody></table>')
    assert V.parse_bpa(h) == [("SIGMOD", 2024, "A Great Query & Engine Paper")]
    w = "===2013===\n*[[Kyuseok Shim]]\n*[[Peter Lee (computer scientist)|Peter Lee]]\n===2015===\n*[[Naehyuck Chang]]"
    assert V.parse_acm(w) == [("Kyuseok Shim", 2013), ("Peter Lee", 2013), ("Naehyuck Chang", 2015)]
    assert V.norm_name("Kyu-Young Whang 0001") == V.norm_name("Kyu Young Whang")


def test_acm_items_needs_unique_senior_name():
    w = "===2013===\n*[[Jiwon Kim]]\n*[[Kyuseok Shim]]"
    got = V.acm_items({"Kyuseok Shim": 30, "Jiwon Kim": 2}, wiki=w)
    assert [g["person"] for g in got] == ["Kyuseok Shim"]


def _db():
    con = duckdb.connect()
    con.execute("CREATE TABLE dblp_paper (key VARCHAR, venue VARCHAR, year INT, title VARCHAR)")
    con.execute("CREATE TABLE dblp_authorship (key VARCHAR, name VARCHAR)")
    con.execute("CREATE TABLE paper_cites (key VARCHAR, cited_by INT)")
    con.execute("CREATE TABLE alias (alias VARCHAR, name VARCHAR)")
    con.execute("ATTACH ':memory:' AS pii")
    con.execute(V.PII_SCHEMA)
    for i in range(120):  # A: 120 SIGMOD papers, 300 cites each → P capped at 100, C capped at 20 000
        con.execute("INSERT INTO dblp_paper VALUES (?, 'sigmod', 2020, 't')", [f"k{i}"])
        con.execute("INSERT INTO dblp_authorship VALUES (?, 'A')", [f"k{i}"])
        con.execute("INSERT INTO paper_cites VALUES (?, 300)", [f"k{i}"])
    con.execute("INSERT INTO dblp_paper VALUES ('b1', 'cvpr', 2020, 't')")
    con.execute("INSERT INTO dblp_authorship VALUES ('b1', 'B')")
    items = [("A", "acm_fellow", None, None), ("A", "general_chair", "SIGMOD", "sigmod"),
             ("A", "general_chair", "VLDB", "vldb"), ("A", "pc_chair", "DASFAA", None),
             ("A", "keynote", "ICDE", "icde"), ("B", "acm_fellow", None, None)]
    for p, k, raw, v in items:
        con.execute("INSERT INTO pii.vis_item VALUES (?, ?, ?, ?, 2020, 'u', 's')", [p, k, raw, v])
    return con


def test_scores_formula_caps_and_field_rules(monkeypatch):
    from fp import age

    monkeypatch.setattr(age, "create_views", lambda con: None)
    df = VS.scores(_db(), ["A", "B"], ["sigmod", "vldb", "icde"]).set_index("name")
    a = df.loc["A"]
    # 20 (P cap) + 20 (C cap) + ACM 10 + GC 2×10 (SIGMOD, VLDB) + keynote 5; DASFAA (not top) ignored
    assert (a.P, a.C, a.acm, a.gc, a.pc, a.kn) == (120, 36000, 1, 2, 0, 1)
    assert a.score == 75.0
    assert "B" not in df.index  # a fellow with no paper / record in the field is not listed there
    assert isinstance(df, pd.DataFrame)


def test_counts_dedupe_and_caps(monkeypatch):
    from fp import age

    monkeypatch.setattr(age, "create_views", lambda con: None)
    con = _db()
    rows = [("A", "general_chair", "VLDB", "vldb", None, "manual"),  # yearless duplicate of VLDB 2020 → dropped
            ("A", "general_chair", "VLDB", "vldb", 2020, "manual"),  # same record as the web one → once
            ("A", "general_chair", "ICDE", "icde", 2021, "s"), ("A", "general_chair", "ICDE", "icde", 2022, "s"),
            ("A", "steering", "VLDB", "vldb", 2001, "s"), ("A", "steering", "VLDB", "vldb", 2005, "s"),
            ("A", "acm_fellow", None, None, 2015, "wikipedia-acm")]
    for p, k, raw, v, y, src in rows:
        con.execute("INSERT INTO pii.vis_item VALUES (?, ?, ?, ?, ?, 'u', ?)", [p, k, raw, v, y, src])
    a = VS.scores(con, ["A"], ["sigmod", "vldb", "icde"]).set_index("name").loc["A"]
    # GC: SIGMOD 2020, VLDB 2020, ICDE 2021, ICDE 2022 = 4 → capped at 3; steering: VLDB twice = 1 conference
    assert (a.gc, a.sc, a.acm) == (4, 1, 1)
    assert a.score == min(100, 20 + 20 + 10 + 30 + 10 + 5)


def test_manual_alias_merges_split_dblp_names(monkeypatch):
    from fp.ingest import dblp

    monkeypatch.setattr(dblp, "MANUAL_ALIASES", {"Myoung-Ho Kim": "Myoung Ho Kim"})
    con = duckdb.connect()
    con.execute("CREATE TABLE dblp_paper (key VARCHAR, venue VARCHAR, year INT, n_authors INT)")
    con.execute("CREATE TABLE dblp_authorship (key VARCHAR, pos INT, name VARCHAR, raw_name VARCHAR, orcid VARCHAR)")
    con.execute("CREATE TABLE alias (alias VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO dblp_paper VALUES ('a', 'vldb', 2006, 1), ('b', 'cikm', 2021, 1)")
    con.execute("INSERT INTO dblp_authorship VALUES ('a', 1, 'Myoung-Ho Kim', 'Myoung-Ho Kim', NULL), "
                "('b', 1, 'Myoung Ho Kim', 'Myoung Ho Kim', NULL)")
    assert dblp.apply_manual_aliases(con) == {"renamed_authorships": 1}
    assert con.execute("SELECT name, sum(count) FROM author_pub GROUP BY 1").fetchall() == [("Myoung Ho Kim", 2)]
    assert con.execute("SELECT name FROM alias WHERE alias = 'Myoung-Ho Kim'").fetchone() == ("Myoung Ho Kim",)


def test_remove_rules():
    from fp.ingest.visibility import _removed

    r = {"person": "A", "kind": "pc_chair", "venue": "icde", "venue_raw": "ICDE", "year": 2006, "evidence_url": "u1"}
    assert _removed(r, [{"person": "A"}])  # whole person
    assert _removed(r, [{"person": "A", "evidence_url": "u1"}])
    assert not _removed(r, [{"person": "A", "evidence_url": "u2"}])
    assert not _removed(r, [{"person": "B"}])
    assert _removed(r, [{"person": "A", "kind": "pc_chair", "venue": "ICDE", "year": 2006}])
    assert not _removed(r, [{"person": "A", "kind": "pc_chair", "year": 2007}])


def test_alias_spelling_records_merge_into_one_person(monkeypatch):
    from fp import age

    monkeypatch.setattr(age, "create_views", lambda con: None)
    con = _db()
    con.execute("INSERT INTO alias VALUES ('A-x', 'A')")
    # the web search ran for both spellings and found the same PC chair; plus one only under the alias
    for p, y in [("A", 2020), ("A-x", 2020), ("A-x", 2021)]:
        con.execute("INSERT INTO pii.vis_item VALUES (?, 'pc_chair', 'SIGMOD', 'sigmod', ?, 'u', 's')", [p, y])
    d = VS.scores(con, ["A"], ["sigmod", "vldb"])
    assert list(d.name).count("A") == 1 and "A-x" not in set(d.name)
    assert d.set_index("name").loc["A"].pc == 2


def test_dedupe_faculty_alias_and_scholar_id():
    from fp.ingest import csrankings

    con = duckdb.connect()
    con.execute("CREATE TABLE cs_faculty (name VARCHAR, affiliation VARCHAR, homepage VARCHAR, scholarid VARCHAR, "
                "orcid VARCHAR)")
    con.execute("CREATE TABLE cs_pub (name VARCHAR, dept VARCHAR, area VARCHAR, year INT, count INT, adj DOUBLE)")
    con.execute("CREATE TABLE alias (alias VARCHAR, name VARCHAR)")
    con.execute("CREATE TABLE dblp_authorship (key VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO alias VALUES ('S J Lee', 'S-J Lee'), ('Moved', 'Moved X')")
    con.execute("""INSERT INTO cs_faculty VALUES
        ('S J Lee', 'KAIST', NULL, 'g1', NULL), ('S-J Lee', 'KAIST', NULL, 'g1', NULL),
        ('J Lee', 'SNU', NULL, 'g2', NULL), ('J Lee 0001', 'SNU', NULL, 'g2', NULL),
        ('Other', 'SNU', NULL, 'NOSCHOLARPAGE', NULL), ('Other2', 'SNU', NULL, 'NOSCHOLARPAGE', NULL),
        ('Moved', 'A', NULL, NULL, NULL), ('Moved X', 'B', NULL, NULL, NULL)""")
    con.execute("INSERT INTO dblp_authorship VALUES ('1', 'J Lee'), ('2', 'J Lee 0001'), ('3', 'J Lee 0001')")
    con.execute("INSERT INTO cs_pub VALUES ('J Lee', 'SNU', 'db', 2020, 1, 1), ('J Lee 0001', 'SNU', 'db', 2020, 2, 2)")
    assert csrankings.dedupe_faculty(con) == 2
    assert sorted(r[0] for r in con.execute("SELECT name FROM cs_faculty").fetchall()) == \
        ["J Lee 0001", "Moved", "Moved X", "Other", "Other2", "S-J Lee"]
    assert con.execute("SELECT sum(count) FROM cs_pub WHERE name = 'J Lee 0001'").fetchone()[0] == 3
