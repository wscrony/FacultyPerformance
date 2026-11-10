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


def test_manual_answer_wins_and_keeps_fields(tmp_path):
    path = tmp_path / "pos.jsonl"
    L.judge_all([("A B", None, None, "")], path=path, min_version=L.VERSION,
                judge=lambda items: {0: {"position": "postdoc", "confidence": 0.8, "korean_name": "에이비",
                                         "institution": "KAIST"}})
    L.set_manual("A B", "professor", institution="SNU", country="kr", path=path)
    # a later LLM answer for the same person does not replace the manual one
    with open(path, "a") as fh:
        fh.write('{"person": "A B", "position": "postdoc", "confidence": 0.9, "v": 2}\n')
    r = L.load_cache(path)["A B"]
    assert (r["position"], r["institution"], r["korean_name"], r["source"]) == ("professor", "SNU", "에이비", "manual")


def test_hangul_filter():
    assert L._hangul("신진우") == "신진우" and L._hangul("남궁 민수") == "남궁 민수"
    assert L._hangul("Shin") is None and L._hangul("신진우 교수님입니다") is None
