import duckdb

from fp import paths, pii


def test_open_bundle_db_is_used_without_key(tmp_path, monkeypatch):
    pub, plain = tmp_path / "public.duckdb", tmp_path / "pii_open.duckdb"
    duckdb.connect(str(pub)).execute("CREATE TABLE t AS SELECT 1 AS x").close()
    duckdb.connect(str(plain)).execute("CREATE TABLE department AS SELECT 'A' AS person").close()
    monkeypatch.setattr(paths, "PUBLIC_DB", pub)
    monkeypatch.setattr(paths, "PII_DB", tmp_path / "pii.duckdb")  # absent
    monkeypatch.setattr(paths, "PII_OPEN_DB", plain)
    monkeypatch.setenv("FP_PII_KEY", "")
    from fp import db
    monkeypatch.setattr(db, "connect", lambda read_only=False: duckdb.connect(str(pub), read_only=read_only))
    c = pii.connect(read_only=True)
    assert c.execute("SELECT person FROM pii.department").fetchall() == [("A",)]


def test_hosted_top_venue_choice_is_per_session(monkeypatch):
    from fp import topvenues

    fake = {}
    monkeypatch.setenv("FP_HOSTED", "1")
    monkeypatch.setattr(topvenues, "_session", lambda: fake)
    v0 = topvenues.version()
    topvenues.save(["compgeom"], [])
    assert topvenues.load()["top"] == ["compgeom"] and topvenues.version() > v0
