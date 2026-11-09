"""One department field in depth: how the leading KR schools compare over time (is the gap closing?)."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fp import age
from fp import boards as B
from fp import ui

ui.require_db()
c = ui.con()
B.ensure_pinst(c)
age.create_views(c)  # paper_cites, person_age

FK = [k for k in B.FIELDS if k != "기타"]
qp = st.query_params.get("f")
pick = st.session_state.pop("field_pick", None)
default = pick or (qp if qp in B.FIELDS else "ML")
if pick or "fd_field" not in st.session_state:
    st.session_state["fd_field"] = default  # a card click must win over the last selection
st.title("분야 심층 비교")
a1, a2, a3 = st.columns([2, 3, 1.4])
k = a1.selectbox("분야", list(B.FIELDS), key="fd_field",
                 format_func=lambda x: f"{x} · {B.FIELDS[x][0]}")
st.query_params["f"] = k
y0, y1 = a2.slider("기간", 2000, ui.THIS_YEAR, (2012, ui.THIS_YEAR), key="fd_years")
nt = a3.toggle("확장 학회 포함", value=False, key="fd_nt")
ui.top_venue_status()

fs = B.field_sql("v")
raw = c.execute(
    f"""SELECT x.institution, x.country, x.year, x.key, x.name, x.venue, x.is_last
        FROM pinst x JOIN venue v ON v.venue = x.venue
        WHERE ({fs}) = ? AND x.year BETWEEN ? AND ? AND (? OR NOT v.next_tier)""",
    [k, y0, y1, nt],
).df()
if raw.empty:
    st.info("이 조건에 논문이 없습니다.")
    st.stop()
kr = raw[raw.country == "kr"]
papers = kr.drop_duplicates(["institution", "key"])
rank_all = papers.groupby("institution").key.count().sort_values(ascending=False)
color_order = B.kr_schools(c)
pal = ui.series()


def col(s: str) -> str:
    i = color_order.index(s) if s in color_order else 99
    return pal[i % len(pal)] if i < len(pal) else ui.gray()


sch = st.multiselect("비교할 학교 (기본: 이 분야 상위 5)", list(rank_all.index), key=f"fd_sch_{k}",
                     default=list(rank_all.index[:5]), max_selections=8)
if not sch:
    st.stop()
mid = (y0 + y1) // 2
h1, h2 = (y0, mid), (mid + 1, y1)

# ── KPIs ──
world_n = raw.key.nunique()
kr_n = kr.key.nunique()
kr_share = lambda a, b: (kr[(kr.year >= a) & (kr.year <= b)].key.nunique()  # noqa: E731
                         / max(1, raw[(raw.year >= a) & (raw.year <= b)].key.nunique()) * 100)
lead = rank_all.index[0]
ui.kpis([
    (f"국내 논문 {y0}–{y1}", f"{kr_n:,}편", "국내 교수가 저자인 고유 논문"),
    ("세계 대비 국내 비중", f"{kr_share(y0, y1):.1f}%",
     f"전반 {h1[0]}–{h1[1]}: {kr_share(*h1):.1f}% → 후반 {h2[0]}–{h2[1]}: {kr_share(*h2):.1f}% "
     "(CSrankings 교수가 저자인 논문 기준)"),
    ("국내 1위", ui.short(lead), f"{int(rank_all.iloc[0]):,}편, 국내 합의 {rank_all.iloc[0] / kr_n * 100:.0f}%"),
    ("논문 있는 국내 기관", f"{len(rank_all)}곳", None),
])

tabs = st.tabs(["추세·격차", "교수 깊이", "학회·피인용", "신진 교수", "세계 속 위치"])

# ───────── trend & gap ─────────
with tabs[0]:
    yr = papers[papers.institution.isin(sch)].groupby(["institution", "year"]).key.count().unstack(0)
    yr = yr.reindex(range(y0, y1 + 1)).fillna(0)
    smooth = st.radio("값", ["연도별", "3년 이동평균", "누적"], horizontal=True, key="fd_smooth")
    z = yr.rolling(3, min_periods=1).mean() if smooth == "3년 이동평균" else (yr.cumsum() if smooth == "누적" else yr)
    fig = go.Figure()
    for s in sch:
        if s in z:
            fig.add_scatter(x=z.index, y=z[s], name=ui.short(s), mode="lines+markers",
                            line=dict(color=col(s), width=2), marker=dict(size=8),
                            hovertemplate=f"{ui.short(s)} %{{x}}: %{{y:.1f}}편<extra></extra>")
    fig.update_layout(title=f"{k} 논문 수 ({smooth})", hovermode="x unified")
    ui.plot(fig, height=400, key="fd_trend")

    # gap to leader: ratio of each school to the yearly leader among the selected (3y rolling to damp noise)
    r3 = yr.rolling(3, min_periods=1).sum()
    ld = r3.idxmax(axis=1)
    ratio = r3.div(r3.max(axis=1), axis=0) * 100
    fig = go.Figure()
    for s in sch:
        if s in ratio:
            fig.add_scatter(x=ratio.index, y=ratio[s], name=ui.short(s), mode="lines",
                            line=dict(color=col(s), width=2),
                            hovertemplate=f"{ui.short(s)} %{{x}}: 1위의 %{{y:.0f}}%<extra></extra>")
    fig.update_layout(title="1위 대비 비율 (%, 최근 3년 합 기준) — 선이 올라가면 격차가 좁혀지는 중", hovermode="x unified")
    fig.update_yaxes(range=[0, 105], ticksuffix="%")
    ui.plot(fig, height=360, key="fd_gap")

    _tot = papers.groupby("year").key.count().reindex(yr.index)
    share = yr.div(_tot.where(_tot > 0), axis=0) * 100
    fig = go.Figure()
    for s in sch:
        if s in share:
            fig.add_scatter(x=share.index, y=share[s].rolling(3, min_periods=1).mean(), name=ui.short(s),
                            mode="lines", line=dict(color=col(s), width=2),
                            hovertemplate=f"{ui.short(s)} %{{x}}: 국내의 %{{y:.0f}}%<extra></extra>")
    fig.update_layout(title="국내 논문 중 비중 (%, 3년 이동평균) — 쏠림이 커지는지/분산되는지", hovermode="x unified")
    fig.update_yaxes(ticksuffix="%")
    ui.plot(fig, height=360, key="fd_share")

    def half(a, b):
        return yr.loc[a:b].sum()

    t = pd.DataFrame({"first": half(*h1), "second": half(*h2)})
    t["growth"] = ((t.second / t["first"].where(t["first"] > 0) - 1) * 100).round(0)
    t["gap1"] = (t["first"] / t["first"].max() * 100).round(0)
    t["gap2"] = (t.second / t.second.max() * 100).round(0)
    t["gap_change"] = t.gap2 - t.gap1
    t["peak"] = [int(yr[s].idxmax()) if s in yr else None for s in t.index]
    t = t.reindex(sch).reset_index().rename(columns={"index": "institution", "institution": "institution"})
    st.dataframe(
        t, hide_index=True, width="stretch",
        column_config={
            "institution": "학교", "first": f"전반 {h1[0]}–{h1[1]}", "second": f"후반 {h2[0]}–{h2[1]}",
            "growth": st.column_config.NumberColumn("증가율", format="%+d%%"),
            "gap1": st.column_config.NumberColumn("전반 1위 대비", format="%d%%"),
            "gap2": st.column_config.NumberColumn("후반 1위 대비", format="%d%%"),
            "gap_change": st.column_config.NumberColumn("격차 변화", format="%+d%%p",
                                                        help="+ = 1위와의 격차가 좁혀짐"),
            "peak": st.column_config.NumberColumn("최다 연도", format="%d"),
        },
    )
    st.caption("논문 = 그 학교 현재 교수(CSrankings)가 저자인 고유 논문. 교수가 옮기면 과거 논문도 현재 학교로 셉니다.")

# ───────── faculty depth ─────────
with tabs[1]:
    ks = kr[kr.institution.isin(sch)]
    act = ks.groupby(["institution", "year"]).name.nunique().unstack(0).reindex(range(y0, y1 + 1)).fillna(0)
    fig = go.Figure()
    for s in sch:
        if s in act:
            fig.add_scatter(x=act.index, y=act[s], name=ui.short(s), mode="lines+markers",
                            line=dict(color=col(s), width=2), marker=dict(size=8),
                            hovertemplate=f"{ui.short(s)} %{{x}}: %{{y}}명<extra></extra>")
    fig.update_layout(title=f"그 해 {k} 논문을 낸 교수 수 — 인력 규모 추세", hovermode="x unified")
    ui.plot(fig, height=360, key="fd_act")

    per = ks.groupby(["institution", "name"]).key.nunique().reset_index(name="n")
    rows = []
    for s in sch:
        d = per[per.institution == s].sort_values("n", ascending=False)
        tot = papers[papers.institution == s].key.count()
        if d.empty:
            continue
        rows.append({"institution": s, "faculty": len(d), "per_fac": tot / len(d),
                     "top1": d.iloc[0]["name"], "top1_share": d.iloc[0].n / tot * 100,
                     "top3_share": d.head(3).n.sum() / tot * 100,
                     "core": int((d.n >= 5).sum()),
                     "last": ks[(ks.institution == s) & ks.is_last].key.nunique() / max(1, tot) * 100})
    dep = pd.DataFrame(rows)
    st.dataframe(
        dep, hide_index=True, width="stretch",
        column_config={
            "institution": "학교", "faculty": st.column_config.NumberColumn("논문 낸 교수", format="%d명"),
            "per_fac": st.column_config.NumberColumn("교수 1인당", format="%.1f"),
            "top1": "최다 교수", "top1_share": st.column_config.NumberColumn("최다 교수 비중", format="%.0f%%",
                                                                         help="학교 논문 중 한 사람 비중 — 높으면 한 명 의존"),
            "top3_share": st.column_config.NumberColumn("상위 3명 비중", format="%.0f%%"),
            "core": st.column_config.NumberColumn("5편 이상 교수", format="%d명"),
            "last": st.column_config.NumberColumn("교수가 마지막저자", format="%.0f%%",
                                                  help="교수가 연구를 이끈 논문 비율 (마지막 저자)"),
        },
    )
    # distribution of papers per faculty
    fig = go.Figure()
    for s in sch:
        d = per[per.institution == s]
        if len(d):
            fig.add_box(x=d.n, name=ui.short(s), marker_color=col(s), boxpoints="all", jitter=0.4,
                        text=d.name, hovertemplate="%{text}: %{x}편<extra></extra>", orientation="h")
    fig.update_layout(title=f"교수별 {k} 논문 수 분포 ({y0}–{y1}) — 점 = 교수", showlegend=False)
    fig.update_xaxes(title="논문 수")
    ui.plot(fig, height=80 + 60 * len(sch), legend=False, key="fd_box")

# ───────── venues & citations ─────────
with tabs[2]:
    vp = papers[papers.institution.isin(sch)].groupby(["venue", "institution"]).key.count().unstack(1).fillna(0)
    vp = vp.loc[vp.sum(axis=1).sort_values(ascending=False).index[:20]]
    fig = go.Figure(go.Heatmap(
        z=vp.values, x=[ui.short(s) for s in vp.columns], y=vp.index, colorscale=ui.seq_scale(), xgap=2, ygap=2,
        text=vp.astype(int).values, texttemplate="%{text}", hovertemplate="%{x} · %{y}: %{z}편<extra></extra>",
        colorbar=dict(title="편")))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(title="학회별 논문 수 — 학교마다 강한 학회")
    ui.plot(fig, height=max(320, 26 * len(vp) + 120), legend=False, key="fd_venue")

    ck = papers[papers.institution.isin(sch)][["institution", "key", "year"]]
    has_c = c.execute("SELECT count(*) FROM duckdb_views() WHERE view_name = 'paper_cites'").fetchone()[0]
    if has_c:
        cc = c.execute("SELECT key, cited_by FROM paper_cites WHERE key IN (SELECT unnest(?))",
                       [list(ck.key.unique())]).df()
        ck = ck.merge(cc, on="key", how="left")
        rows = []
        for s in sch:
            d = ck[ck.institution == s]
            dd = d.dropna(subset=["cited_by"])
            rows.append({"institution": s, "papers": len(d), "cites": int(dd.cited_by.sum()),
                         "med": dd.cited_by.median() if len(dd) else None,
                         "p90": dd.cited_by.quantile(0.9) if len(dd) else None,
                         "hits": int((dd.cited_by >= 100).sum()), "cov": len(dd) / max(1, len(d)) * 100})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "institution": "학교", "papers": "논문", "cites": st.column_config.NumberColumn("피인용 합", format="localized"),
            "med": st.column_config.NumberColumn("논문당 중앙값", format="%.0f"),
            "p90": st.column_config.NumberColumn("상위 10% 논문 기준선", format="%.0f"),
            "hits": st.column_config.NumberColumn("100회 이상 논문", format="%d편"),
            "cov": st.column_config.NumberColumn("피인용 확보율", format="%.0f%%"),
        })
        # median citations by publication year (newer papers have had less time)
        my = ck.dropna(subset=["cited_by"]).groupby(["year", "institution"]).cited_by.median().unstack(1)
        fig = go.Figure()
        for s in sch:
            if s in my:
                fig.add_scatter(x=my.index, y=my[s], name=ui.short(s), mode="lines+markers",
                                line=dict(color=col(s), width=2), marker=dict(size=8),
                                hovertemplate=f"{ui.short(s)} %{{x}}년 논문: 중앙값 %{{y:.0f}}회<extra></extra>")
        fig.update_layout(title="출판 연도별 논문당 피인용 중앙값 — 같은 연도 논문끼리 영향력 비교", hovermode="x unified")
        fig.update_yaxes(type="log", title="피인용 (로그)")
        ui.plot(fig, height=380, key="fd_cite")
        st.caption("피인용 = OpenAlex 현재 값. 최근 논문은 쌓일 시간이 짧아 낮습니다 — 같은 출판 연도끼리 비교하세요.")

# ───────── rising faculty ─────────
with tabs[3]:
    ag = c.execute("SELECT person, academic_age FROM person_age").df().set_index("person").academic_age
    lim = st.slider("학문적 나이 상한", 3, 20, 10, key="fd_young")
    per = kr[kr.institution.isin(sch)].groupby(["name", "institution"]).key.nunique().reset_index(name="papers")
    recent = kr[(kr.institution.isin(sch)) & (kr.year >= y1 - 2)].groupby("name").key.nunique()
    per["recent"] = per.name.map(recent).fillna(0).astype(int)
    per["academic_age"] = per.name.map(ag)
    yg = per[per.academic_age <= lim].sort_values(["papers", "recent"], ascending=False)
    cnt = yg.groupby("institution").name.count().reindex(sch).fillna(0).astype(int)
    st.markdown(" · ".join(f"**{ui.short(s)}** {n}명" for s, n in cnt.items())
                + f" — 학문적 나이 {lim}년 이하이면서 {k} 논문이 있는 교수")
    ui.people_table(
        ui.add_age(yg[["name", "institution", "academic_age", "papers", "recent"]], after="academic_age"),
        "fd_young_tab", B.BF(start=y0, end=y1, next_tier=nt, fields=[k]), height=min(520, 36 * len(yg) + 40),
        column_config={"institution": "학교", "academic_age": "학문적 나이", "papers": f"{k} 논문 {y0}–{y1}",
                       "recent": f"최근 3년 ({y1 - 2}–{y1})"},
    )

# ───────── world position ─────────
with tabs[4]:
    allp = raw.drop_duplicates(["institution", "key"])
    step = st.radio("구간", ["3년", "5년"], horizontal=True, key="fd_step")
    w = 3 if step == "3년" else 5
    bins = [(a, min(a + w - 1, y1)) for a in range(y0, y1 + 1, w)]
    rows = []
    for a, b in bins:
        cnt = allp[(allp.year >= a) & (allp.year <= b)].groupby("institution").key.count()
        rk = cnt.rank(method="min", ascending=False)
        for s in sch:
            if s in rk:
                rows.append({"period": f"{a}–{b}", "institution": s, "rank": int(rk[s]), "papers": int(cnt[s])})
    wr = pd.DataFrame(rows)
    fig = go.Figure()
    for s in sch:
        d = wr[wr.institution == s]
        fig.add_scatter(x=d.period, y=d["rank"], name=ui.short(s), mode="lines+markers",
                        line=dict(color=col(s), width=2), marker=dict(size=9), customdata=d.papers,
                        hovertemplate=f"{ui.short(s)} %{{x}}: 세계 %{{y}}위 (%{{customdata}}편)<extra></extra>")
    fig.update_yaxes(autorange="reversed", type="log", title="세계 순위 (로그, 위가 상위)")
    fig.update_layout(title=f"{k} 세계 기관 순위 추이 (CSrankings 교수 소속 기준)", hovermode="x unified")
    ui.plot(fig, height=420, key="fd_world")
    # which world institutions are just above each school in the latest period
    a, b = bins[-1]
    cnt = allp[(allp.year >= a) & (allp.year <= b)].groupby(["institution", "country"]).key.count() \
        .reset_index(name="papers").sort_values("papers", ascending=False).reset_index(drop=True)
    cnt["rank"] = cnt.papers.rank(method="min", ascending=False).astype(int)
    st.markdown(f"**최근 구간 {a}–{b} 세계 순위표** (선택한 학교 강조)")
    top = cnt.head(max(30, int(cnt[cnt.institution.isin(sch)]["rank"].max() or 0) + 3))
    st.dataframe(top.style.apply(lambda r: ["font-weight:700; background-color: rgba(42,120,214,0.16)"
                                            if r.institution in sch else "" for _ in r], axis=1),
                 hide_index=True, width="stretch", height=420,
                 column_config={"rank": st.column_config.NumberColumn("순위", format="%d위"), "institution": "기관",
                                "country": "국가", "papers": "논문"})
