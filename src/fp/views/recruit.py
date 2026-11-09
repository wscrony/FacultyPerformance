"""Faculty recruiting: per department field, Korean researchers not yet on a KR faculty (abroad faculty, postdocs,
PhD students near graduation, industry, researchers), ranked on recent output in that field."""

import pandas as pd
import streamlit as st

from fp import age, metrics
from fp import boards as B
from fp import ui

ui.require_db()
c = ui.con()
Y = ui.THIS_YEAR
st.title("교수 초빙 후보")
st.caption("국내 대학 교수(CSrankings 명단)가 아닌 한국인 연구자 — 해외 교수·포닥·졸업 임박 박사과정·산업체·연구원 — 를 "
           "분야별로 최근 실적 순으로 보여줍니다. 한국인 여부·경력 단계·나이는 모두 추정입니다.")
ui.top_venue_status()

TYPES = ["해외 교수", age.POSTDOC, age.STUDENT, age.INDUSTRY, age.SENIOR, "미상"]
a1, a2, a3 = st.columns([2.2, 2.2, 3])
y0, y1 = a1.slider("실적 기간", 2000, Y, (Y - 4, Y), key="rc_years", help="기본: 최근 5년")
amin, amax = a2.slider("학문적 나이 (PhD 후 연차)", -6, 30, (-2, 10), key="rc_age",
                       help="음수 = 아직 PhD 전 (박사과정). 기본 −2~10: 졸업 임박 박사과정 ~ 조교수급")
types = a3.multiselect("후보 유형", TYPES, default=[t for t in TYPES if t != age.SENIOR and t != "미상"], key="rc_types")
b1, b2, b3, b4 = st.columns([1.6, 1.6, 2.2, 1.6])
min_p = b1.number_input("분야 논문 최소", 1, 50, 3, key="rc_min")
loc = b2.radio("현재 위치", ["전체", "해외", "국내"], horizontal=True, key="rc_loc")
SORTS = {"field": "분야 논문", "per_year": "분야 연평균 (활동 연수 기준)", "first": "1저자 논문", "cites": "피인용",
         "recent": "최근 2년 분야 논문"}
sort = b3.selectbox("정렬", list(SORTS), format_func=SORTS.get, key="rc_sort")
nt = b4.toggle("확장 학회 포함", value=False, key="rc_nt")

@st.cache_data(ttl=3600, show_spinner="후보 계산 중…")
def _compute(y0: int, y1: int, amin: int, amax: int, nt: bool, min_p: int, tv: float):
    """All candidates per field (before the type/location filters). Cached: a click only reruns the page."""
    from fp import age as _age

    c = ui._root_con().cursor()
    _age.create_views(c)
    F = metrics.Filter(start=y0, end=y1, next_tier=nt, country=None)
    rows, cols = _age.candidates(c, F, age_min=amin, age_max=amax, scope="korean", limit=None)
    cand = pd.DataFrame(rows, columns=cols)
    cand = cand[cand.role != "KR faculty"]
    stage = c.execute("SELECT person, stage FROM person_stage").df().set_index("person").stage
    cand["type"] = ["해외 교수" if r == "faculty abroad" else (stage.get(n) or "미상")
                    for n, r in zip(cand.name, cand.role)]
    cand["where"] = cand.role.map(lambda r: "해외" if "abroad" in r else ("국내" if "KR" in r else "미상"))
    bf = B.BF(start=y0, end=y1, next_tier=nt)
    pf = B.person_field_counts(c, list(cand.name), bf)
    rc = B.person_field_counts(c, list(cand.name), B.BF(start=max(y0, y1 - 1), end=y1, next_tier=nt))
    rc = rc.set_index(["name", "field"]).papers
    main = pf.sort_values(["papers", "field"], ascending=[False, True]).drop_duplicates("name").set_index("name").field
    krp = B.kr_faculty_field_rank(c, bf)
    krp = krp[krp.field != "전체"]
    out = {}
    for k in B.FIELDS:
        d = pf[(pf.field == k) & (pf.papers >= min_p)][["name", "papers"]].rename(columns={"papers": "field"})
        d = d.merge(cand, on="name")
        if d.empty:
            continue
        d["recent"] = [int(rc.get((n, k), 0)) for n in d.name]
        d["active"] = (d.acad_age.fillna(0).clip(lower=-4) + 5).clip(lower=1, upper=y1 - y0 + 1)
        d["per_year"] = (d.field / d.active).round(2)
        d["first"] = d.first_au
        d["main"] = d.name.map(main)
        kp = krp[krp.field == k].papers
        d["kr_equiv"] = [f"{1 + int((kp > n).sum())}위 / {len(kp)}명" for n in d.field]
        d["kr_pct"] = [max(1, round(100 * (kp >= n).mean())) if len(kp) else None for n in d.field]
        out[k] = d
    c.close()
    return cand, out


from fp import topvenues  # noqa: E402

cand, per_all = _compute(y0, y1, amin, amax, nt, int(min_p), topvenues.version())
cand = cand[cand.type.isin(types)]
if loc != "전체":
    cand = cand[cand["where"] == loc]
if cand.empty:
    st.info("조건에 맞는 후보가 없습니다.")
    st.stop()
per_field = {}
for k, d in per_all.items():
    d = d[d.name.isin(cand.name)]
    if len(d):
        # deterministic order (name breaks ties) so a clicked row stays the same person
        per_field[k] = d.sort_values([sort, "field", "name"], ascending=[False, False, True],
                                     kind="mergesort").reset_index(drop=True)

# ── overview ──
ov = pd.DataFrame([{"field": k, "n": len(d), **{t: int((d.type == t).sum()) for t in types},
                    "top": ", ".join(d.name.head(3))} for k, d in per_field.items()])
ui.kpis([
    ("후보 (중복 제외)", f"{cand[cand.name.isin(pd.concat(per_field.values()).name)].name.nunique():,}명",
     f"분야 논문 {min_p}편 이상인 사람만"),
    ("해외 교수", f"{int((cand.type == '해외 교수').sum())}명", None),
    ("포닥 (추정)", f"{int((cand.type == age.POSTDOC).sum())}명", None),
    ("박사과정 (추정)", f"{int((cand.type == age.STUDENT).sum())}명", None),
])
tabs = st.tabs(["요약"] + [f"{k}" for k in per_field])
with tabs[0]:
    st.dataframe(ov, hide_index=True, width="stretch", column_config={
        "field": "분야", "n": st.column_config.NumberColumn("후보", format="%d명"),
        "top": f"상위 3명 ({SORTS[sort]} 순)", **{t: st.column_config.NumberColumn(t, format="%d") for t in types}})
    st.caption("한 사람이 여러 분야에 논문이 있으면 각 분야에 모두 나옵니다 ('주 분야' 열로 구분). 분야 탭에서 이름을 누르면 "
               "논문 목록·피인용·나이 근거가 열립니다.")

for t, (k, d) in zip(tabs[1:], per_field.items()):
    with t:
        st.markdown(f"**{k} · {B.FIELDS[k][0]}** — 후보 {len(d)}명")
        tt = st.multiselect("유형 좁히기", types, default=types, key=f"rc_t_{k}")
        d = d[d.type.isin(tt)]
        show = d[["name", "type", "institution", "main", "acad_age", "field", "recent", "per_year", "first", "cites",
                  "kr_equiv", "kr_pct", "cohort_pctl"]].copy()
        show.insert(0, "no", range(1, len(show) + 1))
        mx = int(show.field.max() or 1)
        ui.people_table(
            ui.add_age(show, after="acad_age"), f"rc_tab_{k}", B.BF(start=y0, end=y1, next_tier=nt, fields=[k]),
            height=min(620, 36 * len(show) + 40),
            column_config={
                "no": st.column_config.NumberColumn("#", width="small"), "type": "유형 (추정)",
                "institution": "현재 소속", "main": "주 분야", "acad_age": "학문적 나이",
                "field": st.column_config.ProgressColumn(f"{k} 논문 {y0}–{y1}", format="%d", min_value=0,
                                                         max_value=mx),
                "recent": st.column_config.NumberColumn("최근 2년", help=f"{max(y0, y1 - 1)}–{y1} {k} 논문"),
                "per_year": st.column_config.NumberColumn("연평균", format="%.2f",
                                                          help="분야 논문 / 활동 연수 (PhD 4년 전부터, 기간 안)"),
                "first": st.column_config.NumberColumn("1저자", help="기간 내 전체 top 학회 1저자 논문"),
                "cites": st.column_config.NumberColumn("피인용", format="localized", help="기간 내 논문의 OpenAlex 피인용"),
                "kr_equiv": st.column_config.TextColumn("국내 교수 환산 순위",
                                                        help=f"같은 기간 {k} 논문 수로 국내 교수들 사이에 넣었을 때 순위"),
                "kr_pct": st.column_config.NumberColumn("국내 교수 대비", format="상위 %d%%",
                                                        help="국내 교수 중 이 사람 이상으로 논문을 낸 비율 — 작을수록 상위"),
                "cohort_pctl": st.column_config.NumberColumn("동년차 국내 교수 대비", format="%d백분위",
                                                             help="학문적 나이 ±2년 국내 교수 사이 백분위 (전체 분야 논문)"),
            },
        )
        st.download_button("CSV 다운로드", show.to_csv(index=False), f"recruit_{k}_{y0}-{y1}.csv",
                           key=f"rc_csv_{k}")
st.caption("유형·한국인 여부·나이는 공개 데이터(CSrankings·DBLP·OpenAlex·ORCID·홈페이지)와 LLM 지식으로 추정한 값이라 "
           "틀릴 수 있습니다. 초빙 판단 전 본인 이력서로 확인하세요.")
