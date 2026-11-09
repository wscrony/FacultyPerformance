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
        agg = tr.groupby(["year", "field"]).papers.sum().reset_index()
        fig = go.Figure()
        FK = [k for k in B.FIELDS if k != "기타"]
        for i, a in enumerate(FK + ["기타"]):
            s = agg[agg.field == a]
            if s.empty:
                continue
            color = ui.gray() if a == "기타" else ui.series()[i % 8]
            fig.add_bar(x=s.year, y=s.papers, name=a,
                        marker=dict(color=color, pattern=dict(shape="/" if i >= 8 and a != "기타" else "", fillmode="replace", bgcolor=color, fgcolor="rgba(255,255,255,0.8)", solidity=0.3),
                                    line=dict(width=1, color="rgba(255,255,255,0.6)")),
                        hovertemplate=f"{a} · {B.FIELDS[a][0]}<br>%{{x}}: %{{y}}편<extra></extra>")
        fig.update_layout(barmode="stack", bargap=0.2, title=f"{school} · 연도별 논문 수 (분야별 + 기타)")
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

ff = B.school_faculty_fields(c, school, f)
info = active.set_index("name")[["academic_age", "age_source", "papers_window", "main_area"]]
fields_here = [k for k in B.FIELDS if k in set(ff[ff.papers_window > 0].field)]
tabs = st.tabs(["전체"] + [f"{k} ({int((ff[(ff.field == k)].papers_window > 0).sum())})" for k in fields_here])


def fac_table(df, label: str, key: str):
    mx = int(df.papers.max() or 1) if len(df) else 1
    st.dataframe(
        df, hide_index=True, width="stretch", height=min(420, 36 * len(df) + 40), key=key,
        column_config={
            "rank": st.column_config.NumberColumn("순위", width="small"), "name": "이름",
            "papers": st.column_config.ProgressColumn(label, format="%d", min_value=0, max_value=mx),
            "career": st.column_config.NumberColumn("경력 전체 (해당 분야)" if key != "s_fac_all" else "경력 전체"),
            "papers_window": st.column_config.NumberColumn(f"전체 분야 {f.start}–{f.end}"),
            "academic_age": st.column_config.NumberColumn("학문적 나이"), "age_source": "나이 출처",
            "main_area": "주 분야",
        },
    )


with tabs[0]:
    d = active.rename(columns={"papers_window": "papers", "papers_career": "career"})
    d = d.assign(rank=range(1, len(d) + 1))[["rank", "name", "papers", "career", "main_area", "academic_age", "age_source"]]
    fac_table(d, f"논문 {f.start}–{f.end}", "s_fac_all")
for t, k in zip(tabs[1:], fields_here):
    with t:
        d = ff[(ff.field == k) & (ff.papers_window > 0)].sort_values(["papers_window", "papers_career"], ascending=False)
        d = d.rename(columns={"papers_window": "papers", "papers_career": "career"}).join(info, on="name")
        d = d.assign(rank=range(1, len(d) + 1))[["rank", "name", "papers", "career", "papers_window", "main_area",
                                                  "academic_age", "age_source"]]
        st.caption(f"{k} · {B.FIELDS[k][0]} — 이 분야 논문이 {f.start}–{f.end}에 1편 이상인 교수, 이 분야 논문 수 순")
        fac_table(d, f"{k} 논문 {f.start}–{f.end}", f"s_fac_{k}")

# ── department fields: who leads each field ──
st.subheader("분야별 국내 순위")
st.caption("분야 = " + " · ".join(f"{k} ({v[0]})" for k, v in B.FIELDS.items())
           + ". 한 논문이 여러 학교에 공동 귀속될 수 있습니다.")
fm = B.kr_field_matrix(c, f)
FK = [k for k in B.FIELDS if k != "기타"]
if not fm.empty:
    order = list(fm.groupby("institution").papers.sum().sort_values(ascending=False).index[:15])
    # leaderboard: top 5 per field
    lead = fm[fm.field.isin(FK)].sort_values(["field", "kr_rank"])
    for grp in ("Applied", "Foundational"):
        st.markdown(f"**{grp} Computing**")
        cols = st.columns(6)
        for i, k in enumerate([k for k in FK if B.FIELDS[k][1] == grp]):
            with cols[i].container(border=True):
                st.markdown(f"**{k}** · {B.FIELDS[k][0]}")
                for r in lead[lead.field == k].head(5).itertuples():
                    st.markdown(f"{int(r.kr_rank)}. {ui.short(r.institution)} — {int(r.papers)}편")
    # rank heatmap: schools × fields (cell = KR rank, color = papers)
    sub = fm[fm.institution.isin(order) & fm.field.isin(FK)]
    piv = sub.pivot_table(index="institution", columns="field", values="papers").reindex(index=order, columns=FK)
    rk = sub.pivot_table(index="institution", columns="field", values="kr_rank").reindex(index=order, columns=FK)
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=FK, y=[ui.short(o) for o in order], colorscale=ui.seq_scale(), xgap=2, ygap=2,
        text=[["" if pd.isna(n) else f"{int(r)}위 · {int(n)}" for n, r in zip(prow, rrow)]
              for prow, rrow in zip(piv.values, rk.values)],
        texttemplate="%{text}", customdata=[[B.FIELDS[k][0] for k in FK]] * len(order),
        hovertemplate="%{y} · %{customdata}<br>%{z:.0f}편<extra></extra>", colorbar=dict(title="편"),
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(title="논문 수 상위 15개 국내 기관 × 분야 (셀 = 국내 순위 · 논문 수, 색 = 논문 수)")
    ui.plot(fig, height=620, legend=False, key="s_fieldrank")

    # field mix (100% stacked): Applied fields in one hue family order, Foundational next, 기타 gray
    st.subheader("학교별 분야 구성")
    tot = fm.groupby("institution").papers.sum()
    fig = go.Figure()
    pal = ui.series()
    for i, k in enumerate(FK + ["기타"]):
        d = fm[fm.field == k].set_index("institution").reindex(order).fillna({"pct": 0, "papers": 0})
        color = ui.gray() if k == "기타" else pal[i % len(pal)]
        fig.add_bar(y=[ui.short(o) for o in order], x=d.pct, name=k, orientation="h",
                    marker=dict(color=color, pattern=dict(shape="/" if i >= 8 and k != "기타" else "", fillmode="replace", bgcolor=color, fgcolor="rgba(255,255,255,0.8)", solidity=0.3),
                                line=dict(width=2, color="rgba(255,255,255,0.9)" if ui.mode() == "light" else "#1a1a19")),
                    customdata=d.papers, text=[f"{k}" if v >= 7 else "" for v in d.pct], textposition="inside",
                    insidetextanchor="middle", textfont=dict(color=ui.ink(color)),
                    hovertemplate=f"%{{y}}<br>{k}: %{{x:.1f}}% (%{{customdata:.0f}}편)<extra></extra>")
    fig.update_layout(barmode="stack", title="분야 비중 (%, 슬래시 무늬 = 9번째 이후 분야, 회색 = 기타)")
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(ticksuffix="%", range=[0, 100])
    ui.plot(fig, height=600, key="s_mix")

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
