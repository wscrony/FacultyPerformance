"""Board: Korea in the world — institution treemap, region trends, KR school rank, area specialization, venues."""

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
    st.dataframe(d[["rank", "institution", "grp", "country", "papers", "share"]].round(2), hide_index=True,
                 width="stretch", column_config={"share": st.column_config.NumberColumn("점유율 %", format="%.2f")})

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

# ── 4. area specialization ──
st.subheader("분야별 한국 점유율")
ap = _q("kr_area_profile", f)
overall = grp.share.get("Korea", 0)
ap = ap.sort_values("kr_share")
fig = go.Figure(go.Bar(
    x=ap.kr_share, y=ap.title, orientation="h", marker=dict(color=ui.series()[0], cornerradius=4),
    customdata=ap[["kr_papers", "papers"]].values,
    hovertemplate="%{y}<br>한국 %{x:.2f}% (%{customdata[0]:,} / %{customdata[1]:,}편)<extra></extra>",
))
fig.add_vline(x=overall, line=dict(color=ui.gray(), width=1.5, dash="dot"),
              annotation_text=f"전체 평균 {overall:.2f}%", annotation_position="top")
fig.update_layout(title="분야별 한국 기관 참여 논문 비율 — 점선보다 오른쪽이면 한국이 상대적으로 강한 분야")
fig.update_xaxes(ticksuffix="%")
ui.plot(fig, height=max(420, 22 * len(ap) + 100), key="spec")

# ── 5. venue × year ──
st.subheader("학회별 · 연도별 한국 점유율")
vy = _q("kr_venue_year", f, 25)
if not vy.empty:
    order = vy.groupby("venue").kr.sum().sort_values(ascending=False).index
    piv = vy.pivot_table(index="venue", columns="year", values="share").reindex(order)
    cnt = vy.pivot_table(index="venue", columns="year", values="kr").reindex(order)
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=[str(x) for x in piv.columns], y=piv.index, colorscale=ui.seq_scale(), xgap=2, ygap=2,
        customdata=cnt.values, texttemplate="%{text}",
        text=[["" if pd.isna(v) else f"{v:.1f}" for v in row] for row in piv.values],
        hovertemplate="%{y} %{x}<br>한국 %{z:.1f}% (%{customdata:.0f}편)<extra></extra>",
        colorbar=dict(title="%"),
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(title="한국 논문이 많은 상위 25개 학회 (셀 = 그 해 학회 논문 중 한국 기관 참여 비율 %, 빈칸 = 그 해 미개최)")
    ui.plot(fig, height=max(420, 24 * len(piv) + 120), legend=False, key="venue")

# ── 6. area strategy map ──
st.subheader("분야 전략 맵")
win = st.radio("성장률 비교 구간", [2, 3, 5], index=1, horizontal=True, key="w_win",
               format_func=lambda w: f"최근 {w}년 vs 직전 {w}년")
sm = B.area_strategy(c, f, win)
if not sm.empty:
    cats = ["ai", "systems", "theory", "interdisciplinary"]
    names = {"ai": "AI", "systems": "Systems", "theory": "Theory", "interdisciplinary": "Interdisciplinary"}
    fig = go.Figure()
    for i, k in enumerate(cats):
        d = sm[sm.category == k]
        fig.add_scatter(
            x=d.growth, y=d.kr_share, mode="markers+text", name=names[k], text=d.title, textposition="top center",
            textfont=dict(size=11),
            marker=dict(size=(d.recent ** 0.5) / max(sm.recent.max() ** 0.5, 1) * 60 + 8, color=ui.series()[i],
                        opacity=0.8, line=dict(width=2, color="rgba(255,255,255,0.9)")),
            customdata=d[["recent", "kr"]].values,
            hovertemplate="<b>%{text}</b><br>세계 성장 %{x:+.0f}%<br>한국 점유율 %{y:.2f}%"
                          "<br>최근 논문 %{customdata[0]:,}편 (한국 %{customdata[1]:,})<extra></extra>",
        )
    avg = 100 * sm.kr.sum() / sm.recent.sum()
    fig.add_hline(y=avg, line=dict(color=ui.gray(), dash="dot"), annotation_text=f"한국 평균 {avg:.1f}%")
    fig.add_vline(x=float(sm.growth.median()), line=dict(color=ui.gray(), dash="dot"),
                  annotation_text="성장률 중앙값", annotation_position="bottom right")
    fig.update_layout(title=f"x = 세계 논문 성장률 · y = 한국 점유율 (최근 {win}년) · 원 크기 = 분야 규모 — "
                            "오른쪽 아래 = 크는데 한국이 약한 분야")
    fig.update_xaxes(title="세계 논문 성장률", ticksuffix="%", zeroline=True, zerolinecolor="rgba(128,128,128,0.4)")
    fig.update_yaxes(title="한국 점유율", ticksuffix="%")
    ui.plot(fig, height=620, key="strategy")
