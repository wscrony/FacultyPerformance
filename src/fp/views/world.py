"""Board: Korea in the world — institution treemap, region trends, KR school rank."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fp import boards as B
from fp import ui

ui.require_db()
st.title("세계 속 한국")
st.caption("대상 학회(CSrankings) 논문 기준. 교수의 논문은 현재 소속 기관에 귀속됩니다 (CSrankings 방식). "
           "점유율의 분모는 기간·분야 내 전체 논문이며, CSrankings 교수 저자가 없는 논문(약 30%)도 분모에 포함됩니다.")
f = ui.board_filter("w")
c = ui.con()


@st.cache_data(ttl=3600)
def _inst(f: B.BF, top: int):
    return B.institution_share(ui.con(), f, top)


@st.cache_data(ttl=3600)
def _q(name: str, f: B.BF, *args):
    return getattr(B, name)(ui.con(), f, *args)


inst, total = _inst(f, 400)
if total == 0 or inst.empty:
    st.info("조건에 맞는 논문이 없습니다.")
    st.stop()
grp = _q("group_share", f).set_index("grp")
cr = _q("country_rank", f)
kr_rank = cr.loc[cr.country == "kr", "rank"]
kr_inst = inst[inst.country == "kr"]
top50_kr = int((inst.head(50).country == "kr").sum())

# ── KPI row ──
ui.kpis([
    ("한국 논문 점유율", f"{grp.share.get('Korea', 0):.2f}%", "한국 기관 교수가 참여한 논문 / 전체 논문"),
    ("국가 순위", f"{int(kr_rank.iloc[0])}위" if len(kr_rank) else "-", "기관 귀속 논문 수 기준 국가 순위"),
    ("세계 Top 50 기관 중 한국", f"{top50_kr}곳", None),
    (f"국내 1위 · {kr_inst.institution.iloc[0] if len(kr_inst) else '-'}",
     f"세계 {int(kr_inst['rank'].iloc[0])}위" if len(kr_inst) else "-", f"{total:,}편 중"),
])

# ── 1. treemap ──
st.subheader("기관별 논문 점유율")
top = st.slider("표시할 기관 수", 20, 120, 50, 10, key="w_top")
d = inst.head(top)
gs = [g for g in ui.REGIONS if g in set(d.grp)]
ids, labels, parents, values, colors, text = [], [], [], [], [], []
for g in gs:
    ids.append(g); labels.append(g); parents.append(""); values.append(0)
    colors.append(ui.region_color(g))
    text.append(f"{g.upper()} · {grp.share.get(g, 0):.1f}%")
for r in d.itertuples():
    ids.append(f"{r.grp}/{r.institution}"); labels.append(r.institution); parents.append(r.grp)
    values.append(int(r.papers)); colors.append(ui.region_color(r.grp))
    text.append(f"<b>{ui.short(r.institution)}</b><br>{r.share:.2f}%")
fig = go.Figure(go.Treemap(
    ids=ids, textfont_color=[ui.ink(x) for x in colors], labels=labels, parents=parents, values=values, branchvalues="remainder",
    text=text, texttemplate="%{text}", textposition="top left",
    marker=dict(colors=colors, line=dict(width=2, color="rgba(255,255,255,0.9)" if ui.mode() == "light" else "#1a1a19")),
    hovertemplate="<b>%{label}</b><br>%{value:,}편<extra></extra>",
    tiling=dict(pad=3), pathbar=dict(visible=False), root=dict(color="rgba(0,0,0,0)"),
    textfont=dict(size=20), insidetextfont=dict(size=20),
))
fig.update_layout(title=f"상위 {top}개 기관 · 타일 크기 = 논문 수 · 라벨 = 전체 {total:,}편 중 점유율 ({f.start}–{f.end})",
                  )
ui.plot(fig, height=760, legend=False, key="treemap")
st.caption("● " + "  ● ".join(f"{g}" for g in gs) + " — 색은 지역, 같은 지역은 같은 색입니다.")
with st.expander("숫자 보기"):
    st.dataframe(d[["rank", "institution", "grp", "country", "papers", "share", "faculty", "per_faculty", "rank_norm"]]
                 .round(2), hide_index=True, width="stretch",
                 column_config={"rank": "순위 (논문 수)", "share": st.column_config.NumberColumn("점유율 %", format="%.2f"),
                                "faculty": "교수", "per_faculty": st.column_config.NumberColumn("1인당", format="%.1f"),
                                "rank_norm": st.column_config.NumberColumn(
                                    "순위 (1인당)", help=f"교수 {B.MIN_FACULTY_NORM}명 이상 기관 중")})

# ── 2. region share trend ──
st.subheader("지역별 점유율 추이")
ys = _q("group_share_by_year", f)
fig = go.Figure()
for g in ui.REGIONS:
    s = ys[ys.grp == g]
    if s.empty:
        continue
    fig.add_scatter(x=s.year, y=s.share, name=g, mode="lines+markers",
                    line=dict(color=ui.region_color(g), width=3 if g == "Korea" else 2),
                    marker=dict(size=8), hovertemplate=f"{g}<br>%{{x}}: %{{y:.1f}}%<extra></extra>")
fig.update_layout(title="연도별 지역 점유율 (%, 한 논문은 지역당 한 번)", hovermode="x unified")
fig.update_yaxes(ticksuffix="%")
ui.plot(fig, height=400, key="trend")

# ── 3. KR school world rank ──
st.subheader("국내 대학의 세계 순위 변화")
schools = B.kr_schools(c)[:8]
rk = _q("kr_school_world_rank_by_year", f, schools)
col = {s: ui.series()[i] for i, s in enumerate(schools)}
fig = go.Figure()
for s in schools:
    d2 = rk[rk.institution == s]
    if d2.empty:
        continue
    fig.add_scatter(x=d2.year, y=d2["rank"], name=s, mode="lines+markers", line=dict(color=col[s], width=2),
                    marker=dict(size=8), customdata=d2.papers,
                    hovertemplate=f"{s}<br>%{{x}}: 세계 %{{y}}위 (%{{customdata}}편)<extra></extra>")
fig.update_yaxes(autorange="reversed", title="세계 순위", type="log", tickvals=[1, 2, 5, 10, 20, 50, 100, 200, 500])
fig.update_layout(title="연도별 세계 순위 (논문 수 기준, 로그 축 · 위쪽이 상위)")
ui.plot(fig, height=460, key="bump")
