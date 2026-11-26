"""Visibility score: per field, 0–100 from public data (papers, citations, fellowships, chair/steering roles,
best papers, keynotes) — fp.visibility."""

import pandas as pd
import streamlit as st

from fp import boards as B
from fp import ui
from fp import visibility as VS

ui.require_db()
c = ui.con()
Y = ui.THIS_YEAR
st.title("Visibility 점수")
st.caption(
    "분야 안에서 학계 visibility를 공개 데이터만으로 0–100점으로 계산합니다. "
    "**V = 0.2·min(논문, 100) + 0.001·min(인용, 20,000) + 10·(ACM Fellow + IEEE Fellow) + "
    "10·(PC Chair 횟수 + General Chair 횟수 + Steering/Endowment 학회 수) + 5·(Best Paper 횟수 + Keynote 횟수)**, "
    f"각 횟수는 최대 {VS.MAX_COUNT}회, 총점 최대 {VS.TOTAL_CAP}점. 논문·인용은 그 분야 top 학회 누적, 경력은 그 분야 "
    "top 학회 것만 인정(같은 학회·같은 해는 1회), Fellow는 분야와 무관하게 인정.")
ui.top_venue_status()

f1, f2, f3 = st.columns([2.2, 3, 2.2])
field = f1.selectbox("분야", list(B.FIELDS), format_func=lambda k: f"{k} · {B.FIELDS[k][0]}", key="vs_field")
opts, va = B.sub_options(c, [field], False)
subs = f2.multiselect("세부 분야 · 학회 (여러 개 가능, 비우면 분야 전체)", opts, format_func=B.sub_label, key="vs_subs")
who = f3.radio("대상", ["전체", "국내 교수", "그 외 (초빙 후보)"], horizontal=True, key="vs_who")
venues = B.sub_venues(subs, va) if subs else B.field_venues(c, [field], False)
label = field if not subs else " + ".join(B.sub_label(o).replace("학회 · ", "") for o in subs[:3]) + (
    f" 외 {len(subs) - 3}개" if len(subs) > 3 else "")


@st.cache_data(ttl=3600, show_spinner="대상자 불러오는 중…")
def _people(_v: float):
    """KR faculty + the recruiting pool, with a type label."""
    from fp import age
    from fp.ingest import visibility as IV

    cc = ui._root_con().cursor()
    try:
        ppl = IV.all_people(cc)
        role = dict(cc.execute("SELECT person, role FROM person_role").fetchall())
        stage = dict(cc.execute("SELECT person, stage FROM person_stage").fetchall())
    finally:
        cc.close()
    return pd.DataFrame({"name": list(ppl), "acad_age": list(ppl.values()),
                         "type": ["국내 교수" if role.get(p) == "KR faculty" else
                                  ("해외 교수" if role.get(p) == "faculty abroad" else stage.get(p) or age.UNKNOWN)
                                  for p in ppl]})


@st.cache_data(ttl=3600, show_spinner="점수 계산 중…")
def _scores(names: tuple, venues: tuple, _v: float):
    cc = ui._root_con().cursor()
    try:
        return VS.scores(cc, list(names), list(venues))
    finally:
        cc.close()


from fp import topvenues  # noqa: E402

ppl = _people(topvenues.version())
if who == "국내 교수":
    ppl = ppl[ppl.type == "국내 교수"]
elif who != "전체":
    ppl = ppl[ppl.type != "국내 교수"]
if not venues:
    st.info("이 분야에 top 학회가 없습니다. Top 학회 설정을 확인하세요.")
    st.stop()
df = _scores(tuple(sorted(ppl.name)), tuple(venues), topvenues.version())
df = df.merge(ppl, on="name", how="left")
if df.empty:
    st.info("이 분야에 논문이나 경력 기록이 있는 사람이 없습니다.")
    st.stop()
df.insert(0, "rank", range(1, len(df) + 1))
inst = dict(c.execute("SELECT person, coalesce(affiliation, affil_raw) FROM person_role").fetchall())
df["institution"] = df.name.map(inst)

n_srch = int(df.searched.sum())
ui.kpis([
    ("대상자", f"{len(df):,}명", f"{label} top 학회 논문 또는 경력 기록이 있는 사람"),
    ("50점 이상", f"{int((df.score >= 50).sum())}명", None),
    ("경력 기록 1건 이상", f"{int((df[[c_ for c_, _ in VS.ITEMS.values()]].sum(axis=1) > 0).sum())}명", None),
    ("웹 검색 완료", f"{n_srch:,}명", "나머지는 공개 명단(ACM Fellow·Best Paper)만 반영"),
])
if not VS._has(c, "vis_item"):
    st.warning("경력 항목(Fellow·Chair·Steering·Best Paper·Keynote) 데이터가 아직 이 데이터 번들에 없습니다. 지금 점수는 "
               "논문·인용만으로 계산한 값(최대 40점)입니다.")
elif n_srch == 0:
    st.warning("경력 항목 웹 검색이 아직 반영되지 않았습니다. 지금은 논문·인용과 공개 명단(ACM Fellow·Best Paper)만으로 "
               "계산한 점수입니다.")

show = df[["rank", "name", "institution", "type", "acad_age", "score", "P", "C", "acm", "ieee", "pc", "gc", "sc",
           "bp", "kn", "evidence", "evidence_url", "searched"]]
chk = {col: (st.column_config.CheckboxColumn(lbl, help=f"{pts}점", width="small") if kind in VS.FELLOWS else
              st.column_config.NumberColumn(lbl, help=f"횟수 × {pts}점 (최대 {VS.MAX_COUNT}회)", width="small"))
       for (kind, (col, pts)), lbl in zip(VS.ITEMS.items(), ["ACM", "IEEE", "PC Chair", "General Chair", "Steering",
                                                             "Best Paper", "Keynote"])}
ui.people_table(
    show, f"vs_tab_{field}_{label}", B.BF(start=1970, end=Y, venues=venues), height=min(640, 36 * len(show) + 40),
    column_config={
        "rank": st.column_config.NumberColumn("#", width="small"), "institution": "소속", "type": "구분",
        "acad_age": "학문적 나이",
        "score": st.column_config.ProgressColumn("Visibility", format="%.1f", min_value=0, max_value=100),
        "P": st.column_config.NumberColumn(f"{label} 논문", help=f"누적 (상한 {VS.P_CAP}편 = 20점)"),
        "C": st.column_config.NumberColumn("인용", format="localized",
                                           help=f"그 논문들의 OpenAlex 피인용 합 (상한 {VS.C_CAP:,} = 20점)"),
        **chk,
        "evidence": st.column_config.TextColumn("확인된 경력", width="large"),
        "evidence_url": st.column_config.LinkColumn("근거", display_text="↗", width="small"),
        "searched": st.column_config.CheckboxColumn("웹 검색", help="경력 항목을 웹에서 찾아봤는지 (아니면 공개 명단만)",
                                                    width="small"),
    })
st.download_button("CSV 다운로드", show.to_csv(index=False), f"visibility_{label}.csv", key="vs_csv")
st.caption(
    "경력 항목은 공개 자료에서 찾았습니다: ACM Fellow는 Wikipedia의 ACM Fellows 목록, Best Paper는 Jeff Huang의 "
    "'Best Paper Awards in Computer Science' 목록(논문 제목으로 DBLP 논문과 대조), 나머지(IEEE Fellow·PC/General Chair·"
    "Steering/Endowment·Keynote)와 목록에 없는 수상은 LLM 웹 검색(근거 URL 포함). **찾지 못한 항목은 0점**이므로 실제보다 "
    "낮을 수 있고, 웹 검색은 국내 교수와 학문적 나이 4년 이상 후보만 했습니다. 인용은 OpenAlex 현재 값이며 일부 논문은 "
    "피인용을 찾지 못했습니다. 근거 열의 링크는 그 사람의 첫 번째 근거입니다.")
