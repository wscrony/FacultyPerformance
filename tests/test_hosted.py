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


def test_new_bundle_url_replaces_files_under_an_open_connection(tmp_path, monkeypatch):
    """Changing FP_DATA_URL downloads the new bundle; an open connection on the old file keeps working."""
    import functools
    import http.server
    import tarfile
    import threading

    from fp import bundle

    srv_dir = tmp_path / "srv"
    srv_dir.mkdir()
    for ver in (1, 2):
        d = tmp_path / f"b{ver}"
        d.mkdir()
        duckdb.connect(str(d / "public.duckdb")).execute(f"CREATE TABLE t AS SELECT {ver} AS v").close()
        with tarfile.open(srv_dir / f"b{ver}.tar.gz", "w:gz") as tf:
            tf.add(d / "public.duckdb", arcname="public.duckdb")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(srv_dir))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA", data)
    monkeypatch.setattr(paths, "PUBLIC_DB", data / "public.duckdb")
    try:
        assert bundle.ensure(f"{base}/b1.tar.gz") is True
        old = duckdb.connect(str(data / "public.duckdb"), read_only=True)
        assert bundle.ensure(f"{base}/b1.tar.gz") is False  # same URL: nothing to do
        assert bundle.ensure(f"{base}/b2.tar.gz") is True
        assert old.execute("SELECT v FROM t").fetchone() == (1,)  # old handle still readable
        old.close()
        assert duckdb.connect(str(data / "public.duckdb"), read_only=True).execute("SELECT v FROM t").fetchone() == (2,)
    finally:
        srv.shutdown()
