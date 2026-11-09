from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.test_m2 import con  # noqa: F401

APP = str(Path(__file__).resolve().parents[1] / "src" / "fp" / "app.py")


def test_app_renders_all_tabs(con):  # noqa: F811
    con.execute("INSERT INTO pii.degree (person, degree, year, source, confidence) VALUES ('Young','phd',2020,'homepage',0.8)")
    con.close()
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.tabs) == 4
    # pick a person in search → profile + compare state
    at.multiselect(key="pick").set_value(["Young"]).run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("Young" in s.value for s in at.subheader)
