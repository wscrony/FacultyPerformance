import math

import duckdb
import pytest

from fp import db, metrics
from fp.ingest.csrankings import parse_config

CONFIG_TS = """
namespace CSRankings {
    export const parentMap: { [key: string]: string } = {
        'sigmod': 'mod',
        'vldb': 'mod',
        'icde': 'mod', // next tier
        'icml': 'mlmining',
    };
    export const nextTier: { [key: string]: boolean } = {
        'icde': true,
    };
    export const areaMap: Array<AreaMap> = [
        { area: "mod", title: "DB" },
        { area: "mlmining", title: "ML" },
    ];
    export const aiAreas = ["mlmining"];
    export const systemsAreas = ["mod"];
    export const theoryAreas = [];
    export const interdisciplinaryAreas = [];
    export const noteMap = { 'Tech': 'https://tech.cornell.edu/' };
}
"""


def test_parse_config(monkeypatch):
    import fp.ingest.csrankings as m

    src = CONFIG_TS
    # pad to pass the sanity threshold
    extra = "".join(f"        'v{i}': 'mod',\n" for i in range(60))
    src = src.replace("'icml': 'mlmining',", "'icml': 'mlmining',\n" + extra)
    rows = {r.venue: r for r in m.parse_config(src)}
    assert rows["sigmod"].area == "mod" and rows["sigmod"].area_title == "DB"
    assert rows["sigmod"].category == "systems" and not rows["sigmod"].next_tier
    assert rows["icde"].next_tier
    assert rows["icml"].category == "ai"


def test_parse_config_rejects_garbage():
    with pytest.raises(ValueError):
        parse_config("nothing here")


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "t.duckdb")
    c.executemany(
        "INSERT INTO venue VALUES (?,?,?,?,?)",
        [
            ("sigmod", "mod", "DB", "systems", False),
            ("icde", "mod", "DB", "systems", True),
            ("icml", "mlmining", "ML", "ai", False),
        ],
    )
    c.executemany(
        "INSERT INTO institution VALUES (?,?,?,?)",
        [("A Univ", "asia", "kr", ""), ("B Univ", "asia", "kr", ""), ("C Univ", "europe", "de", "")],
    )
    c.executemany(
        "INSERT INTO cs_pub VALUES (?,?,?,?,?,?)",
        [
            ("Alice", "A Univ", "sigmod", 2020, 1, 0.5),
            ("Alice", "A Univ", "icml", 2021, 1, 1.0),
            ("Alice", "A Univ", "icde", 2021, 1, 1.0),  # next tier: excluded by default
            ("Bob", "B Univ", "sigmod", 2019, 2, 2.0),
            ("Bob", "B Univ", "sigmod", 2010, 1, 1.0),  # out of range
            ("Carl", "C Univ", "sigmod", 2020, 5, 5.0),  # other country
        ],
    )
    return c


def test_school_score_matches_csrankings_formula(con):
    rows, cols = metrics.school_ranking(con, metrics.Filter(start=2016, end=2026))
    got = {r[0]: r for r in rows}
    assert set(got) == {"A Univ", "B Univ"}
    # A: (0.5+1)*(1+1) over 2 areas; B: (2+1)*(0+1)
    assert got["A Univ"][1] == pytest.approx(math.sqrt(1.5 * 2.0))
    assert got["B Univ"][1] == pytest.approx(math.sqrt(3.0 * 1.0))
    assert got["A Univ"][2] == 1


def test_next_tier_and_area_filter(con):
    f = metrics.Filter(start=2016, end=2026, areas=["mod"], next_tier=True)
    rows, _ = metrics.school_ranking(con, f)
    got = {r[0]: r[1] for r in rows}
    assert got["A Univ"] == pytest.approx(0.5 + 1.0 + 1.0)  # single area → adj+1


def test_faculty_table_world(con):
    rows, _ = metrics.faculty_table(con, metrics.Filter(start=2016, end=2026, country=None))
    assert [r[0] for r in rows] == ["Carl", "Bob", "Alice"]
