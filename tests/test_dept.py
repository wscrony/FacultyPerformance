import duckdb

from fp import boards as B
from fp.ingest import llm_dept


def _con():
    c = duckdb.connect()
    c.execute("ATTACH ':memory:' AS pii")
    c.execute("CREATE TABLE institution (institution VARCHAR, country VARCHAR)")
    c.execute("INSERT INTO institution VALUES ('KAIST','kr'), ('MIT','us')")
    c.execute("CREATE TABLE cs_faculty (name VARCHAR, affiliation VARCHAR, homepage VARCHAR)")
    c.execute("INSERT INTO cs_faculty VALUES ('A','KAIST','https://cs.kaist.ac.kr/a'), ('B','KAIST',NULL), ('C','MIT',NULL)")
    return c


def test_department_judge_and_store_low_confidence_dropped():
    c = _con()
    rows = llm_dept.targets(c)
    assert [r[0] for r in rows] == ["A", "B"]  # KR faculty only
    fake = lambda items: {0: ("School of Computing", "컴퓨터/소프트웨어", 0.9), 1: ("School of EE", "weird", 0.3)}
    res = llm_dept.judge_rows(rows, judge=fake)
    llm_dept.store(c, res)
    got = dict(c.execute("SELECT person, dept_group FROM pii.department").fetchall())
    assert got == {"A": "컴퓨터/소프트웨어", "B": None}
    assert llm_dept.targets(c) == []  # done people are skipped
    df = B._departments(c).rename(columns={"person": "name"})
    df["dept_group"] = df.dept_group
    assert B.institution_dept_summary(df) == "컴퓨터/소프트웨어 1명 · 미상 1명"
