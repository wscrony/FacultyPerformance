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
f = ui.board_filter("p", credit=False)
ui.top_venue_status()
st.caption("위 기간·분야 필터는 인재 풀, 국내 교수 실적, 해외 한국인 교수 섹션에 적용됩니다 (현재 소속·이동 차트는 전체 기준).")
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
tm = B.talent_matrix(c, f)
if loc == "해외만":
    tm = tm[tm.country.notna() & (tm.country != "kr")]
elif loc == "국내만":
    tm = tm[tm.country == "kr"]
excl = st.toggle("국내 교수 제외 (영입 후보만)", value=True, key="p_excl")
if excl:
    tm = tm[tm.role != "KR faculty"]
if not tm.empty:
    piv = tm.pivot_table(index="area", columns="stage", values="person", aggfunc="count").fillna(0)
    piv = piv.reindex([k for k in B.FIELDS if k in piv.index])
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=piv.columns, y=[f"{k} · {B.FIELDS[k][0]}" for k in piv.index], colorscale=ui.seq_scale(), xgap=2, ygap=2,
        text=piv.astype(int).values, texttemplate="%{text}",
        hovertemplate="%{y} · %{x}<br>%{z:.0f}명<extra></extra>", colorbar=dict(title="명"),
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(title=f"{f.start}–{f.end}에 논문이 있는 한국인 연구자 수 ({loc}{', 국내 교수 제외' if excl else ''}) — 주 분야 × 학문적 나이")
    ui.plot(fig, height=max(480, 24 * len(piv) + 120), legend=False, key="p_talent")
    st.caption(f"주 분야 = {f.start}–{f.end} 논문이 가장 많은 분야 (상단 기간·분야 필터를 따름). 학문적 나이는 대부분 추정값이라 ±2년 정도 오차가 있습니다.")

# ── age vs output ──
st.subheader("국내 교수: 학문적 나이 대비 실적")
ao = B.kr_faculty_age_output(c, f)
dept = B._departments(c)
if dept is not None:
    ao = ao.merge(dept.rename(columns={"person": "person"}), on="person", how="left")
win = f.end - f.start + 1
# yearly rate over the part of the window the person was active in (since PhD-4, as students publish too)
ao["active_years"] = ao.academic_age.clip(lower=0).add(4).clip(upper=win, lower=1)
ao["per_year"] = (ao.papers / ao.active_years).round(2)
METRICS = {"papers": "논문 수", "per_year": "연평균 논문 (활동 연수 기준)", "cites": "피인용", "first_au": "1저자 논문"}
k1, k2, k3 = st.columns([3, 2, 1.4])
hl = k1.multiselect("강조·비교할 학교 (최대 4)", B.kr_schools(c), max_selections=4, key="p_hl",
                    default=[s for s in B.kr_schools(c)[:2]])
ym = k2.selectbox("y축 지표", list(METRICS), format_func=METRICS.get, key="p_ym")
hide0 = k3.toggle("0편 숨기기", value=True, key="p_hide0", help="이 기간·분야에 논문이 없는 교수 (다른 분야 교수 등) 제외")
aos = ao[ao.papers > 0] if hide0 else ao
lab = METRICS[ym]
fig = go.Figure()
rest = aos[~aos.affiliation.isin(hl)]
fig.add_scatter(x=rest.academic_age, y=rest[ym], mode="markers", name="그 외",
                marker=dict(size=8, color=ui.gray(), opacity=0.45, line=dict(width=1, color="rgba(255,255,255,0.8)")),
                text=rest.person + " · " + rest.affiliation, hovertemplate="%{text}<br>%{x}년 · %{y}<extra></extra>")
for i, s in enumerate(hl):
    d = aos[aos.affiliation == s]
    fig.add_scatter(x=d.academic_age, y=d[ym], mode="markers", name=s,
                    marker=dict(size=9, color=ui.series()[i], line=dict(width=2, color="rgba(255,255,255,0.9)")),
                    text=d.person, hovertemplate="%{text}<br>%{x}년 · %{y}<extra></extra>")
med = aos.groupby("academic_age")[ym].median().rolling(5, center=True, min_periods=2).median()
fig.add_scatter(x=med.index, y=med.values, mode="lines", name="국내 중앙값 (5년 이동)",
                line=dict(color=ui.gray(), width=2, dash="dot"), hovertemplate="중앙값 %{x}년: %{y:.1f}<extra></extra>")
fig.update_layout(title=f"점 = 국내 교수 · x = 학문적 나이 · y = {lab} ({f.start}–{f.end})")
fig.update_xaxes(title="학문적 나이 (추정 포함)")
fig.update_yaxes(title=lab)
ui.plot(fig, height=520, key="p_scatter")

# ── table: school × academic-age band ──
BANDS = [(-99, 5, "~5년"), (6, 10, "6–10년"), (11, 20, "11–20년"), (21, 99, "21년~")]
ao["band"] = pd.cut(ao.academic_age, [b[0] - 0.5 for b in BANDS] + [999], labels=[b[2] for b in BANDS])
grp = {s: ao[ao.affiliation == s] for s in hl}
grp["국내 전체"] = ao
rows = []
for g, d in grp.items():
    for band in ["전체"] + [b[2] for b in BANDS]:
        x = d if band == "전체" else d[d.band == band]
        act = x[x.papers > 0]
        rows.append({"group": g, "band": band, "n": len(x), "active": len(act),
                     "med": act.papers.median() if len(act) else None,
                     "p75": act.papers.quantile(0.75) if len(act) else None,
                     "per_year": act.per_year.median() if len(act) else None,
                     "cites": act.cites.median() if len(act) else None,
                     "first": act.first_au.sum() / max(1, act.papers.sum()) * 100 if len(act) else None,
                     "total": int(x.papers.sum())})
tb = pd.DataFrame(rows)
view = st.radio("표 보기", ["학교 × 연차 구간", "학교만 (전체)"], horizontal=True, key="p_tbview")
if view == "학교만 (전체)":
    tb = tb[tb.band == "전체"]
st.dataframe(
    tb, hide_index=True, width="stretch",
    column_config={
        "group": "학교", "band": "학문적 나이", "n": st.column_config.NumberColumn("교수", format="%d명"),
        "active": st.column_config.NumberColumn("논문 있는 교수", format="%d명",
                                                help="이 기간·분야에 논문이 1편 이상 — 아래 지표는 이 사람들 기준"),
        "med": st.column_config.NumberColumn("논문 중앙값", format="%.1f"),
        "p75": st.column_config.NumberColumn("논문 상위 25%", format="%.1f", help="75 백분위수"),
        "per_year": st.column_config.NumberColumn("연평균 논문 중앙값", format="%.2f"),
        "cites": st.column_config.NumberColumn("피인용 중앙값", format="%.0f"),
        "first": st.column_config.NumberColumn("1저자 비율", format="%.0f%%", help="1저자 논문 / 전체 논문"),
        "total": st.column_config.NumberColumn("논문 합"),
    },
)
st.caption("같은 연차 구간끼리 비교하면 경력 차이를 빼고 학교를 비교할 수 있습니다. 중앙값은 논문이 있는 교수 기준.")

# ── per-person table for the highlighted schools ──
if hl:
    pt = aos[aos.affiliation.isin(hl)].copy()
    pt["band_med"] = pt.academic_age.map(ao[ao.papers > 0].groupby("academic_age")[ym].median()
                                         .rolling(5, center=True, min_periods=2).median())
    pt["vs_med"] = (pt[ym] / pt.band_med.where(pt.band_med > 0)).round(1)
    pt = pt.sort_values(ym, ascending=False)
    cols = ["person", "affiliation"] + (["dept_group"] if "dept_group" in pt else []) + [
        "academic_age", "papers", "per_year", "cites", "first_au", "vs_med"]
    st.markdown(f"**{' · '.join(hl)} 교수별** ({lab} 순, 이름을 누르면 세부 정보)")
    ui.people_table(
        ui.add_age(pt[cols].rename(columns={"person": "name"}), after="academic_age"), "p_ao_people", f,
        height=min(520, 36 * len(pt) + 40),
        column_config={
            "affiliation": "학교", "dept_group": "학과 구분 (추정)", "academic_age": "학문적 나이",
            "papers": "논문", "per_year": st.column_config.NumberColumn("연평균", format="%.2f"),
            "cites": st.column_config.NumberColumn("피인용", format="localized"), "first_au": "1저자",
            "vs_med": st.column_config.NumberColumn(f"같은 연차 중앙값 대비 ({lab})", format="%.1f배",
                                                    help="1.0 = 같은 학문적 나이(±2년) 국내 교수 중앙값"),
        },
    )

# ── faculty abroad (recruiting view) ──
st.subheader("해외 대학의 한국인 교수")
rows, cols = age.candidates(c, metrics.Filter(start=f.start, end=f.end, areas=f.areas, venues=f.venues,
                                              next_tier=f.next_tier), scope="korean", limit=None)
cand = pd.DataFrame(rows, columns=cols)
fa = cand[cand.role == "faculty abroad"][["name", "institution", "age", "acad_age", "pubs", "first_au", "last_au",
                                           "cites"]]
pf = B.person_field_counts(c, list(fa.name), f)
order = {k: n for n, k in enumerate(B.FIELDS)}
if pf.empty:
    st.info("조건에 맞는 해외 한국인 교수가 없습니다.")
else:
    pf["fo"] = pf.field.map(order)
    main = pf.sort_values(["name", "papers", "fo"], ascending=[True, False, True]).drop_duplicates("name")
    main = main.rename(columns={"field": "main_field", "papers": "field_papers"})[["name", "main_field", "field_papers"]]
    fa = fa.merge(main, on="name")
    fa["rank"] = fa.groupby("main_field").field_papers.rank(method="min", ascending=False).astype(int)
    fa["n_abroad"] = fa.groupby("main_field").name.transform("count")
    # where they would rank among KR faculty in the same field and window
    krp = B.kr_faculty_field_rank(c, f)
    krp = krp[krp.field != "전체"]
    fa["kr_equiv"] = [
        f"{1 + int((krp[(krp.field == m)].papers > n).sum())}위 / {int((krp.field == m).sum())}명"
        for m, n in zip(fa.main_field, fa.field_papers)
    ]
    fa["rank_txt"] = [f"{r}위 / {n}명" for r, n in zip(fa["rank"], fa.n_abroad)]
    fa["fo"] = fa.main_field.map(order)
    fa = fa.sort_values(["fo", "rank"])
    mx = int(fa.field_papers.max() or 1)
    st.caption(f"주 분야 = {f.start}–{f.end} 논문이 가장 많은 분야. 국내 환산 순위 = 같은 분야·기간 국내 교수들 사이에 "
               "넣었을 때의 순위.")
    ui.people_table(
        ui.add_age(fa[["main_field", "rank_txt", "kr_equiv", "name", "institution", "field_papers", "pubs", "first_au",
                       "last_au", "cites", "acad_age"]], after="acad_age"),
        "p_abroad", f, height=460,
        column_config={
            "main_field": "주 분야", "rank_txt": st.column_config.TextColumn("분야 내 순위 (해외 한국인 교수)"),
            "kr_equiv": st.column_config.TextColumn("국내 환산 순위"), "name": "이름", "institution": "소속",
            "field_papers": st.column_config.ProgressColumn(f"주 분야 논문 {f.start}–{f.end}", format="%d",
                                                            min_value=0, max_value=mx),
            "pubs": "전체 분야 논문", "first_au": "1저자", "last_au": "마지막저자", "cites": "피인용",
            "acad_age": "학문적 나이",
        },
    )
