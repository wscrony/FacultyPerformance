"""Board: Korean researchers — where they are, age × role, mobility, age vs output, faculty abroad.

PII-derived (inferred ethnicity, ages, affiliations): local app only.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fp import age, metrics
from fp import boards as B
from fp import ui

ui.require_db()
st.title("한국인 연구자")
st.caption("한국인 여부는 이름·공저 기반 추정, 소속은 본인 논문의 OpenAlex 소속, 나이는 확인값 또는 추정값입니다. "
           "모든 데이터는 로컬 암호화 DB에서만 읽습니다.")
c = ui.con()
land = B.korean_landscape(c)

ROLES = ["KR faculty", "faculty abroad", "other · KR", "other · abroad", "other · unknown"]
ROLE_KO = {"KR faculty": "국내 교수", "faculty abroad": "해외 교수", "other · KR": "그 외 · 국내",
           "other · abroad": "그 외 · 해외", "other · unknown": "그 외 · 미상"}


def role_color(r: str) -> str:
    return ui.gray() if r == "other · unknown" else ui.series()[ROLES.index(r)]


known = land.grp.notna()
abroad = land[known & (land.grp != "Korea")]
confirmed = land.age_source.isin(["orcid", "homepage", "manual"])
ui.kpis([
    ("추적 중인 한국인 연구자", f"{len(land):,}명", "최근 10년 대상 학회 논문 3편 이상 + 국내 교수"),
    ("해외 소속 비율", f"{100 * len(abroad) / max(known.sum(), 1):.0f}%", "소속을 아는 사람 중"),
    ("해외 교수 (CSrankings)", f"{(land.role == 'faculty abroad').sum():,}명", None),
    ("나이 확인 비율", f"{100 * confirmed.mean():.0f}%", "ORCID·홈페이지·수동 입력"),
])

# ── where they are ──
st.subheader("지금 어디에 있나")
w = land[known].copy()
w["inst"] = w.affiliation.fillna("(소속 미상)")
g = w.groupby(["grp", "inst"]).size().reset_index(name="n").sort_values("n", ascending=False)
top_n = st.slider("기관 수", 30, 150, 70, 10, key="p_top")
g = g.head(top_n)
gs = [x for x in ui.REGIONS if x in set(g.grp)]
gt = w.groupby("grp").size()
ids = list(gs) + [f"{r.grp}/{r.inst}" for r in g.itertuples()]
tile_colors = [ui.region_color(x) for x in gs] + [ui.region_color(x) for x in g.grp]
fig = go.Figure(go.Treemap(
    ids=ids, textfont_color=[ui.ink(x) for x in tile_colors], labels=list(gs) + list(g.inst), parents=[""] * len(gs) + list(g.grp),
    values=[0] * len(gs) + list(g.n), branchvalues="remainder",
    text=[f"{x.upper()} · {gt.get(x, 0):,}명" for x in gs] + [f"<b>{ui.short(r.inst)}</b><br>{r.n}명" for r in g.itertuples()],
    texttemplate="%{text}", textposition="top left",
    marker=dict(colors=[ui.region_color(x) for x in gs] + [ui.region_color(x) for x in g.grp],
                line=dict(width=2, color="rgba(255,255,255,0.9)" if ui.mode() == "light" else "#1a1a19")),
    hovertemplate="<b>%{label}</b><br>%{value}명<extra></extra>", tiling=dict(pad=3), pathbar=dict(visible=False),
    textfont=dict(size=18), insidetextfont=dict(size=18),
))
fig.update_layout(title="한국인 연구자의 현재 소속 (교수·포닥·학생·산업체 포함, 타일 = 인원)",
                  )
ui.plot(fig, height=640, legend=False, key="p_where")

c1, c2 = st.columns(2)
# ── age × role ──
with c1:
    fig = go.Figure()
    a = land.dropna(subset=["academic_age"])
    for r in ROLES:
        s = a[a.role == r]
        if s.empty:
            continue
        fig.add_histogram(x=s.academic_age.clip(-5, 45), name=ROLE_KO[r], xbins=dict(start=-5, end=45, size=2),
                          marker=dict(color=role_color(r)),
                          hovertemplate=f"{ROLE_KO[r]}<br>%{{x}}년: %{{y}}명<extra></extra>")
    fig.update_layout(barmode="stack", bargap=0.08, title="학문적 나이 분포 (구분별, 2년 단위)")
    fig.update_xaxes(title="학문적 나이 (음수 = 박사 전으로 추정)")
    ui.plot(fig, height=440, key="p_age")
# ── mobility sankey ──
with c2:
    mv = B.mobility(c)
    if not mv.empty:
        flows = mv.groupby(["start_grp", "now_grp"]).size().reset_index(name="n")
        srcs = [x for x in ui.REGIONS if x in set(flows.start_grp)]
        dsts = [x for x in ui.REGIONS if x in set(flows.now_grp)]
        labels = [f"{x} (처음)" for x in srcs] + [f"{x} (지금)" for x in dsts]
        idx = {("s", x): i for i, x in enumerate(srcs)} | {("d", x): len(srcs) + i for i, x in enumerate(dsts)}

        def rgba(hexc: str, a: float) -> str:
            h = hexc.lstrip("#")
            return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"

        fig = go.Figure(go.Sankey(
            node=dict(label=labels, pad=14, thickness=16,
                      color=[ui.region_color(x) for x in srcs] + [ui.region_color(x) for x in dsts]),
            link=dict(source=[idx[("s", r.start_grp)] for r in flows.itertuples()],
                      target=[idx[("d", r.now_grp)] for r in flows.itertuples()],
                      value=list(flows.n), color=[rgba(ui.region_color(r.start_grp), 0.35) for r in flows.itertuples()],
                      hovertemplate="%{source.label} → %{target.label}: %{value}명<extra></extra>"),
        ))
        fig.update_layout(title="첫 논문 소속 → 최근 논문 소속 (3년 이상 차이, 인원)")
        ui.plot(fig, height=440, legend=False, key="p_flow")
        back = int(((mv.start_grp != "Korea") & (mv.now_grp == "Korea")).sum())
        out = int(((mv.start_grp == "Korea") & (mv.now_grp != "Korea")).sum())
        st.caption(f"해외 → 국내 {back:,}명 · 국내 → 해외 {out:,}명 (첫 논문 소속은 보통 박사과정 기관)")

# ── talent matrix ──
st.subheader("인재 풀: 분야 × 경력 단계")
loc = st.radio("위치", ["전체", "해외만", "국내만"], horizontal=True, key="p_loc")
tm = B.talent_matrix(c)
if loc == "해외만":
    tm = tm[tm.country.notna() & (tm.country != "kr")]
elif loc == "국내만":
    tm = tm[tm.country == "kr"]
excl = st.toggle("국내 교수 제외 (영입 후보만)", value=True, key="p_excl")
if excl:
    tm = tm[tm.role != "KR faculty"]
if not tm.empty:
    piv = tm.pivot_table(index="area", columns="stage", values="person", aggfunc="count").fillna(0)
    piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=piv.columns, y=piv.index, colorscale=ui.seq_scale(), xgap=2, ygap=2,
        text=piv.astype(int).values, texttemplate="%{text}",
        hovertemplate="%{y} · %{x}<br>%{z:.0f}명<extra></extra>", colorbar=dict(title="명"),
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(title=f"최근 5년 활동한 한국인 연구자 수 ({loc}{', 국내 교수 제외' if excl else ''}) — 주 분야 × 학문적 나이")
    ui.plot(fig, height=max(480, 24 * len(piv) + 120), legend=False, key="p_talent")
    st.caption("주 분야 = 최근 5년 논문이 가장 많은 분야. 학문적 나이는 대부분 추정값이라 ±2년 정도 오차가 있습니다.")

# ── age vs output ──
st.subheader("국내 교수: 학문적 나이 대비 실적")
f = ui.board_filter("p", credit=False)
ao = B.kr_faculty_age_output(c, f)
hl = st.multiselect("강조할 학교 (최대 4)", B.kr_schools(c), max_selections=4, key="p_hl",
                    default=[s for s in B.kr_schools(c)[:2]])
fig = go.Figure()
rest = ao[~ao.affiliation.isin(hl)]
fig.add_scatter(x=rest.academic_age, y=rest.papers, mode="markers", name="그 외",
                marker=dict(size=8, color=ui.gray(), opacity=0.45, line=dict(width=1, color="rgba(255,255,255,0.8)")),
                text=rest.person + " · " + rest.affiliation, hovertemplate="%{text}<br>%{x}년 · %{y}편<extra></extra>")
for i, s in enumerate(hl):
    d = ao[ao.affiliation == s]
    fig.add_scatter(x=d.academic_age, y=d.papers, mode="markers", name=s,
                    marker=dict(size=9, color=ui.series()[i], line=dict(width=2, color="rgba(255,255,255,0.9)")),
                    text=d.person, hovertemplate="%{text}<br>%{x}년 · %{y}편<extra></extra>")
med = ao.groupby("academic_age").papers.median().rolling(5, center=True, min_periods=2).median()
fig.add_scatter(x=med.index, y=med.values, mode="lines", name="중앙값 (5년 이동)", line=dict(color=ui.gray(), width=2, dash="dot"),
                hovertemplate="중앙값 %{x}년: %{y:.0f}편<extra></extra>")
fig.update_layout(title=f"점 = 국내 교수 · x = 학문적 나이 · y = {f.start}–{f.end} 논문 수")
fig.update_xaxes(title="학문적 나이 (추정 포함)")
fig.update_yaxes(title="논문 수")
ui.plot(fig, height=520, key="p_scatter")

# ── faculty abroad (recruiting view) ──
st.subheader("해외 대학의 한국인 교수")
rows, cols = age.candidates(c, metrics.Filter(start=f.start, end=f.end, areas=f.areas, venues=f.venues),
                            scope="korean", limit=None)
cand = pd.DataFrame(rows, columns=cols)
fa = cand[cand.role == "faculty abroad"][["rank", "name", "institution", "age", "acad_age", "pubs", "first_au",
                                           "last_au", "cites", "cohort_pctl"]]
fa = fa.assign(rank=range(1, len(fa) + 1))
st.dataframe(fa, hide_index=True, width="stretch", height=420, column_config={
    "rank": "순위", "name": "이름", "institution": "소속", "age": st.column_config.NumberColumn("나이(추정)"),
    "acad_age": "학문적 나이", "pubs": st.column_config.ProgressColumn("논문", format="%d", min_value=0,
                                                                   max_value=int(fa.pubs.max() if len(fa) else 1)),
    "first_au": "1저자", "last_au": "마지막저자", "cites": "피인용",
    "cohort_pctl": st.column_config.NumberColumn("국내 동년배 대비 pctl", format="%d"),
})
