import pytest

from fp import age, db, metrics, paths, pii
from fp.ingest import orcid


def test_valid_orcid():
    assert orcid.valid_orcid("0000-0002-1825-0097")  # ORCID's documented example
    assert orcid.valid_orcid("0000-0002-1694-233X")
    assert not orcid.valid_orcid("0000-0002-1825-0098")  # bad checksum
    assert not orcid.valid_orcid("0000-0000-0000-0000")
    assert not orcid.valid_orcid("")
    assert not orcid.valid_orcid(None)


@pytest.mark.parametrize(
    "title,want",
    [
        ("Ph.D.", "phd"),
        ("PhD in Computer Science", "phd"),
        ("Doctor of Philosophy", "phd"),
        ("박사", "phd"),
        ("M.S.", "ms"),
        ("Master of Engineering", "ms"),
        ("B.S.", "bs"),
        ("Bachelor of Science", "bs"),
        ("Postdoctoral Researcher", None),
        ("Diploma", None),
        ("Programs", None),
        (None, None),
    ],
)
def test_classify(title, want):
    assert orcid.classify(title) == want


def _edu(title, year, org="Univ"):
    return {
        "education-summary": {
            "role-title": title,
            "end-date": {"year": {"value": str(year)}} if year else None,
            "organization": {"name": org},
        }
    }


def test_parse_activities_and_best():
    body = {
        "educations": {
            "affiliation-group": [
                {"summaries": [_edu("Ph.D.", 2008, "Stanford")]},
                {"summaries": [_edu("B.S.", 2001, "SNU")]},
                {"summaries": [_edu("Postdoc", 2010)]},
            ]
        },
        "qualifications": {
            "affiliation-group": [
                {"summaries": [{"qualification-summary": {"role-title": "PhD", "end-date": {"year": {"value": "2009"}}}}]}
            ]
        },
    }
    best = orcid.best_per_degree(orcid.parse_activities(body))
    assert best["phd"].year == 2008 and best["phd"].institution == "Stanford"
    assert best["bs"].year == 2001
    assert "ms" not in best


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_PII_KEY", "test-key")
    monkeypatch.setattr(paths, "PUBLIC_DB", tmp_path / "public.duckdb")
    monkeypatch.setattr(paths, "PII_DB", tmp_path / "pii.duckdb")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    for d in ("RAW", "SNAPSHOTS", "CACHE", "EXPORTS"):
        monkeypatch.setattr(paths, d, tmp_path / d.lower())
    c = pii.connect()
    c.executemany("INSERT INTO venue VALUES (?,?,?,?,?)", [("sigmod", "mod", "DB", "systems", False)])
    c.executemany("INSERT INTO institution VALUES (?,?,?,?)", [("A", "asia", "kr", ""), ("Z", "europe", "de", "")])
    c.executemany(
        "INSERT INTO cs_faculty VALUES (?,?,?,?,?)",
        [
            ("Old", "A", "", "", "0000-0002-1825-0097"),
            ("Young", "A", "", "", "0000-0000-0000-0000"),
            ("Mid", "A", "", "", ""),
            ("Abroad", "Z", "", "", ""),
        ],
    )
    c.executemany(
        "INSERT INTO cs_pub VALUES (?,?,?,?,?,?)",
        [
            ("Old", "A", "sigmod", 2000, 1, 1.0),
            ("Old", "A", "sigmod", 2024, 2, 1.0),
            ("Young", "A", "sigmod", 2020, 3, 3.0),
            ("Mid", "A", "sigmod", 2018, 1, 0.5),
            ("Abroad", "Z", "sigmod", 2019, 1, 1.0),
        ],
    )
    return c


def test_pii_db_is_encrypted(con, tmp_path):
    import duckdb

    marker = "ZZ-Plaintext-Marker-Person-1970"
    con.execute("INSERT INTO pii.birth (person, year, source, confidence) VALUES (?, 1970, 'manual', 1)", [marker])
    con.execute("CHECKPOINT pii")
    con.close()
    raw = duckdb.connect()
    with pytest.raises(Exception):
        raw.execute(f"ATTACH '{tmp_path / 'pii.duckdb'}' AS x")
    assert marker.encode() not in (tmp_path / "pii.duckdb").read_bytes()


def test_orcid_update_with_fake_fetch(con):
    body = {"educations": {"affiliation-group": [{"summaries": [_edu("Ph.D.", 1998)]}]}}
    stats = orcid.update(con, scope="kr", fetch=lambda oid: body, sleep=0)
    assert stats["targets"] == 3 and stats["ok"] == 1 and stats["invalid_id"] == 2 and stats["phd"] == 1
    # second run is a no-op (fetched recently)
    assert orcid.update(con, scope="kr", fetch=lambda oid: body, sleep=0)["targets"] == 0


def test_age_resolution_priority(con):
    con.execute(
        """INSERT INTO pii.degree (person, degree, year, source, confidence) VALUES
           ('Old', 'phd', 1998, 'orcid', 0.9), ('Old', 'phd', 1997, 'manual', 1.0),
           ('Mid', 'bs', 2010, 'orcid', 0.9)"""
    )
    age.create_views(con, this_year=2026)
    got = {r[0]: r[1:] for r in con.execute(
        "SELECT person, phd_year_eff, age_source, academic_age, birth_year, birth_source FROM person_age"
    ).fetchall()}
    assert got["Old"] == (1997, "manual", 29, 1967, "est:phd")
    assert got["Young"] == (2022, "est:first_top", 4, 1992, "est:first_top")  # first top 2020 + 2
    assert got["Mid"] == (2020, "est:first_top", 6, 1987, "est:bs")


def test_candidates_age_filter_and_cohort(con):
    f = metrics.Filter(start=2016, end=2026)
    rows, cols = age.candidates(con, f, age_max=10)
    names = [r[0] for r in rows]
    assert names == ["Young", "Mid"]  # Old has a confirmed PhD (manual 1997), Abroad not kr
    r = dict(zip(cols, rows[0]))
    assert r["cohort_pctl"] == 75.0 and r["cohort_n"] == 2  # Young beats Mid; self counts 0.5


def test_forget(con):
    con.execute("INSERT INTO pii.degree (person, degree, year, source, confidence) VALUES ('Old','phd',1998,'orcid',0.9)")
    out = pii.forget(con, "Old")
    assert out["degree"] == 1
    assert orcid.targets(con, "kr", force=True) == [("Mid", ""), ("Young", "0000-0000-0000-0000")]
    age.create_views(con)
    assert con.execute("SELECT count(*) FROM person_age WHERE person='Old'").fetchone()[0] == 0
