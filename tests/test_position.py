import duckdb

from fp.ingest import llm_position as L


def test_judge_all_caches_and_resumes(tmp_path):
    path = tmp_path / "pos.jsonl"
    rows = [(f"P{i} 0001", "KAIST", 2026, "t (iclr 2026)") for i in range(7)]
    calls = []

    def judge(items):
        calls.append([n for _, n, *_ in items])
        return {i: {"position": "professor", "country": "kr", "confidence": 0.9, "institution": "KAIST"}
                for i, *_ in items}

    assert L.judge_all(rows, judge=judge, batch=3, workers=2, path=path)["asked"] == 7
    assert all(not n.endswith("0001") for c in calls for n in c)  # DBLP suffix stripped for the search
    assert L.judge_all(rows, judge=judge, batch=3, path=path)["asked"] == 0  # all cached
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS pii")
    assert L.store(con, path) == {"stored": 7, "professor": 7}
    assert con.execute("SELECT count(*) FROM pii.position WHERE country = 'kr'").fetchone()[0] == 7


def test_failed_batch_is_retried_next_time(tmp_path):
    path = tmp_path / "pos.jsonl"

    def boom(items):
        raise RuntimeError("cli down")

    assert L.judge_all([("A", None, None, "")], judge=boom, path=path)["failed"] == 1
    assert L.load_cache(path) == {}
