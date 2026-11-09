from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.test_m2 import con  # noqa: F401

APP = str(Path(__file__).resolve().parents[1] / "src" / "fp" / "app.py")


def test_app_renders_all_tabs(con):  # noqa: F811
    con.execute("INSERT INTO pii.degree (person, degree, year, source, confidence) VALUES ('Young','phd',2020,'homepage',0.8)")
    con.close()
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.tabs) == 3  # 학교 비교 merged into 국내 기관
    # pick people in search → inline comparison right there (one person → hint to add more)
    opts = at.multiselect(key="pick").options
    at.multiselect(key="pick").set_value(opts[:1]).run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("한 명만" in i.value for i in at.info)
    at.multiselect(key="pick").set_value(opts[:2]).run()
    assert not at.exception, [e.message for e in at.exception]
    assert any(s.value.startswith("비교:") for s in at.subheader)
