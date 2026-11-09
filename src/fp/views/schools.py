"""Board: Korean institutions — school profile, area mix across schools, inter-school collaboration."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fp import boards as B
from fp import ui

ui.require_db()
st.title("국내 기관")
f = ui.board_filter("s", credit=False)
c = ui.con()
schools = B.kr_schools(c)

# ── school profile ──
st.subheader("학교 프로필")
school = st.selectbox("학교", schools, key="s_school")
prof = B.school_profile(c, school, f)
active = prof[prof.papers_career > 0]
inst, total = B.institution_share(c, f, 1000)
row = inst[inst.institution == school]
ages = active.academic_age.dropna()
ui.kpis([
    ("교수 (CSrankings)", f"{len(active)}명", "대상 학회 논문이 1편 이상인 교수"),
    (f"논문 {f.start}–{f.end}", f"{int(row.papers.iloc[0]) if len(row) else 0:,}편", "교수 참여 논문 (중복 제거)"),
    ("세계 순위", f"{int(row['rank'].iloc[0])}위" if len(row) else "-", "같은 기간·분야, 논문 수 기준"),
    ("학문적 나이 중앙값", f"{ages.median():.0f}년" if len(ages) else "-", "PhD 후 연수 (추정 포함)"),
])

tr = B.school_trend(c, school, f)
c1, c2 = st.columns([3, 2])
with c1:
    tr = tr[tr.year >= max(2000, f.start - 15)]
    if not tr.empty:
        top_areas = tr.groupby("area").papers.sum().sort_values(ascending=False)
        keep = list(top_areas.index[:7])
        tr["area2"] = tr.area.where(tr.area.isin(keep), "기타")
        agg = tr.groupby(["year", "area2"]).papers.sum().reset_index()
        fig = go.Figure()
        for i, a in enumerate(keep + (["기타"] if "기타" in set(agg.area2) else [])):
            s = agg[agg.area2 == a]
            fig.add_bar(x=s.year, y=s.papers, name=a,
                        marker=dict(color=ui.gray() if a == "기타" else ui.series()[i], line=dict(width=1, color="rgba(255,255,255,0.6)")),
                        hovertemplate=f"{a}<br>%{{x}}: %{{y}}편<extra></extra>")
        fig.update_layout(barmode="stack", bargap=0.2, title=f"{school} · 연도별 논문 수 (분야별, 상위 7개 분야 + 기타)")
        fig.add_vrect(x0=f.start - 0.5, x1=f.end + 0.5, fillcolor="rgba(128,128,128,0.08)", line_width=0,
                      annotation_text="선택 기간", annotation_position="top left")
        ui.plot(fig, height=420, key="s_trend")
with c2:
    a = active.dropna(subset=["academic_age"])
    if not a.empty:
        a = a.assign(src=a.age_source.fillna("est").str.startswith("est").map({True: "추정", False: "확인됨"}))
        fig = go.Figure()
        for i, k in enumerate(["확인됨", "추정"]):
            s = a[a.src == k]
            fig.add_histogram(x=s.academic_age, name=k, xbins=dict(start=0, end=50, size=3),
                              marker=dict(color=ui.series()[0] if k == "확인됨" else ui.gray(), cornerradius=4),
                              hovertemplate=f"{k}<br>%{{x}}년: %{{y}}명<extra></extra>")
        fig.update_layout(barmode="stack", title="교수 학문적 나이 분포 (PhD 후 연수, 3년 단위)")
        fig.update_xaxes(title="학문적 나이")
        ui.plot(fig, height=420, key="s_age")

st.dataframe(
    active, hide_index=True, width="stretch", height=380,
    column_config={
        "name": "이름", "academic_age": st.column_config.NumberColumn("학문적 나이"),
        "age_source": "나이 출처",
        "papers_window": st.column_config.ProgressColumn(
            f"논문 {f.start}–{f.end}", format="%d", min_value=0,
            max_value=int(active.papers_window.max() or 1)),
        "papers_career": st.column_config.NumberColumn("경력 전체 논문"),
        "main_area": "주 분야",
    },
)

# ── area mix across schools ──
st.subheader("학교별 분야 구성")
mix = c.execute(
    f"""
    SELECT x.institution, v.category, count(DISTINCT x.key) AS papers
    FROM pinst x JOIN venue v ON v.venue = x.venue
    WHERE {f.where('x')[0]} AND x.country = 'kr' GROUP BY 1, 2
    """,
    f.where("x")[1],
).df()
if not mix.empty:
    order = mix.groupby("institution").papers.sum().sort_values(ascending=False).index[:15]
    mix = mix[mix.institution.isin(order)]
    tot = mix.groupby("institution").papers.transform("sum")
    mix["pct"] = 100 * mix.papers / tot
    cats = ["ai", "systems", "theory", "interdisciplinary"]
    names = {"ai": "AI", "systems": "Systems", "theory": "Theory", "interdisciplinary": "Interdisciplinary"}
    fig = go.Figure()
    for i, k in enumerate(cats):
        s = mix[mix.category == k].set_index("institution").reindex(order)
        fig.add_bar(y=s.index, x=s.pct, name=names[k], orientation="h",
                    marker=dict(color=ui.series()[i], line=dict(width=2, color="rgba(255,255,255,0.9)" if ui.mode() == "light" else "#1a1a19")),
                    customdata=s.papers, text=s.pct.round(0).astype("Int64").astype(str) + "%", textposition="inside",
                    insidetextanchor="middle",
                    hovertemplate=f"%{{y}}<br>{names[k]}: %{{x:.1f}}% (%{{customdata:.0f}}편)<extra></extra>")
    fig.update_layout(barmode="stack", title="논문 수 상위 15개 국내 기관의 분야 그룹 비중 (%)")
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(ticksuffix="%", range=[0, 100])
    ui.plot(fig, height=560, key="s_mix")

# ── collaboration ──
st.subheader("국내 기관 간 협업")
cm = B.kr_collab_matrix(c, f, 15)
if not cm.empty:
    order = list(cm.groupby("a").papers.sum().sort_values(ascending=False).index)
    piv = cm.pivot_table(index="a", columns="b", values="papers").reindex(index=order, columns=order)
    for o in order:
        piv.loc[o, o] = float("nan")  # no self-collaboration cell
    lab = [ui.short(o) for o in order]
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=lab, y=lab, colorscale=ui.seq_scale(), xgap=2, ygap=2,
        text=[["" if pd.isna(v) else f"{int(v)}" for v in row] for row in piv.fillna(-1).where(piv.notna()).values],
        texttemplate="%{text}",
        hovertemplate="%{y} × %{x}<br>공동 논문 %{z:.0f}편<extra></extra>", colorbar=dict(title="편"),
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickangle=-40)
    fig.update_layout(title="두 기관 교수가 함께 쓴 논문 수 (논문이 많은 15개 기관)")
    ui.plot(fig, height=620, legend=False, key="s_collab")
