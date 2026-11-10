import threading

import duckdb

from fp import ui


def test_cursors_of_finished_runs_are_closed(monkeypatch):
    """Each script-run thread gets a cursor; once the thread is gone its cursor (and temp tables) is closed."""
    root = duckdb.connect()
    monkeypatch.setattr(ui, "_root_con", lambda: root)
    monkeypatch.setattr(ui.age, "create_views", lambda c: c.execute("CREATE TEMP TABLE big AS SELECT 1 AS x"))
    monkeypatch.setattr(ui, "_CURS", {})
    got = []
    for _ in range(5):
        t = threading.Thread(target=lambda: got.append(ui.con()))
        t.start()
        t.join()
    ui.con()  # the next call prunes runs that ended
    assert len(ui._CURS) == 1
    for c in got:
        try:
            c.execute("SELECT 1")
            raise AssertionError("cursor of a finished run still open")
        except duckdb.ConnectionException:
            pass


def test_pinst_uses_shipped_table(monkeypatch):
    from fp import boards

    c = duckdb.connect()
    c.execute("CREATE TABLE pinst AS SELECT 1 AS key")
    boards.ensure_pinst(c)  # must not rebuild it as a temp table
    assert c.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'pinst' AND temporary").fetchone()[0] == 0
