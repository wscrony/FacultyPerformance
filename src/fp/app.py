"""Local Streamlit app. Launch with `fp app` (binds to 127.0.0.1 only — the page shows PII)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fp import age, links, metrics, pii  # noqa: F401
from fp import boards as B

from fp.ui import THIS_YEAR, con  # shared cursor (one DuckDB connection per process)
from fp import ui

SERIES = ui.series()
SEQ_BLUE = ui.seq_scale()
MAX_COMPARE = 4

ui.require_db()


@st.cache_data
def area_options() -> pd.DataFrame:
    return pd.DataFrame(metrics.all_areas(con()), columns=["area", "title", "category"])


@st.cache_data
def people_list(country: str | None) -> list[str]:
    """Faculty ('name — affiliation') plus Korean pool members without a faculty listing ('name — -')."""
    fac = [f"{n} — {a}" for n, a in metrics.people(con(), country)]
    known = {x.split(" — ")[0] for x in fac}
    pool = [r[0] for r in con().execute("SELECT person FROM korean_eff WHERE is_korean ORDER BY 1").fetchall()]
    return fac + [f"{n} — -" for n in pool if n not in known]


def _plot(fig: go.Figure, height: int = 420, key: str | None = None):
    multi = len(fig.data) > 1
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=84 if multi else 40, b=8),
        title=dict(y=0.99, yanchor="top", x=0, xanchor="left", font=dict(size=15)),
        hoverlabel=dict(namelength=-1),
        showlegend=multi,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, traceorder="normal"),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    st.plotly_chart(fig, width="stretch", key=key)


# ───────────────────────── sidebar (global filters) ─────────────────────────

areas_df = area_options()
with st.sidebar:
    st.header("필터")
    yr = st.slider("기간", 1980, THIS_YEAR, (THIS_YEAR - 9, THIS_YEAR))
    cats = st.multiselect("분야", list(B.FIELDS), format_func=lambda k: f"{k} · {B.FIELDS[k][0]}", key="sb_fields")
    pool = areas_df[areas_df.area.map(lambda a: B.AREA_FIELD.get(a, "기타")).isin(cats)] if cats else areas_df
    picked = st.multiselect(
        "세부 분야 (CSrankings)", pool.area.tolist(), format_func=lambda a: f"{a} ({areas_df.set_index('area').title[a]})"
    )
    sel_areas = picked or None
    next_tier = st.toggle("확장 학회 포함", value=False, help="기본은 top 학회만. 켜면 CSrankings 확장 학회 + AAAI·IJCAI 포함")
    sel_venues = B.field_venues(con(), cats, next_tier) if cats and not picked else None
    region = st.radio("국가", ["kr", "world"], horizontal=True)
    st.caption("학교 점수 = 분야별 (논문 수+1)의 기하평균. 같은 학교 교수 공저 논문은 각자 1편씩 셉니다.")

country = None if region == "world" else region
F = metrics.Filter(start=yr[0], end=yr[1], areas=sel_areas, next_tier=next_tier, country=country, venues=sel_venues)

def render_compare(names: list[str], kp: str, flt: metrics.Filter | None = None) -> None:
    """Side-by-side comparison of up to MAX_COMPARE people (used by the search and compare tabs)."""
    flt = flt or F
    color = {n: SERIES[i] for i, n in enumerate(names)}
    s = metrics.person_series(con(), names, flt)
    ages = con().execute(
        "SELECT person, phd_year_eff, age_source FROM person_age WHERE person IN (SELECT unnest(?))", [names]
    ).df().set_index("person")
    info = pd.DataFrame({
        "name": names,
        "학문적 나이": [THIS_YEAR - ages.phd_year_eff.get(n) if n in ages.index and pd.notna(ages.phd_year_eff.get(n)) else None for n in names],
        "나이 출처": [ages.age_source.get(n) if n in ages.index else None for n in names],
        f"논문 {yr[0]}–{yr[1]}": [int(s[s["name"] == n].pubs.sum()) for n in names],
    })
    # citations side by side: the period's papers and the whole career (same venue/field filter)
    career = metrics.Filter(start=1970, end=THIS_YEAR, areas=flt.areas, next_tier=flt.next_tier, venues=flt.venues)
    has_dblp = con().execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'dblp_authorship'").fetchone()[0]
    empty = pd.DataFrame({"year": pd.Series(dtype=int), "title": pd.Series(dtype=str), "cites": pd.Series(dtype=float)})
    pp = {n: age.person_papers(con(), n, career) if has_dblp else empty for n in names}

    def _h(c):
        c = sorted((int(x) for x in c.dropna()), reverse=True)
        return sum(1 for i, x in enumerate(c, 1) if x >= i)

    info[f"피인용 {yr[0]}–{yr[1]}"] = [
        int(d[(d.year >= flt.start) & (d.year <= flt.end)].cites.fillna(0).sum()) for d in pp.values()]
    info[f"논문당 피인용 {yr[0]}–{yr[1]}"] = [
        round(float(d[(d.year >= flt.start) & (d.year <= flt.end)].cites.dropna().mean() or 0), 1)
        if d[(d.year >= flt.start) & (d.year <= flt.end)].cites.notna().any() else None for d in pp.values()]
    info["피인용 (경력 전체)"] = [int(d.cites.fillna(0).sum()) for d in pp.values()]
    info["h-index (top 학회)"] = [_h(d.cites) for d in pp.values()]
    info["최다 피인용 논문"] = [
        (f"{d.loc[d.cites.idxmax()].title} ({int(d.loc[d.cites.idxmax()].year)}, {int(d.cites.max()):,}회)"
         if d.cites.notna().any() else None) for d in pp.values()]
    ui.people_table(ui.add_age(info, after="학문적 나이"), f"{kp}_info", flt)
    st.caption("피인용 = OpenAlex. h-index는 이 필터의 top 학회 논문만으로 계산 (Google Scholar보다 작음). "
               "DOI 없는 논문은 제목 매칭이 진행 중이라 일부 비어 있을 수 있습니다.")

    piv = s.pivot_table(index="area", columns="name", values="pubs", aggfunc="sum").fillna(0)
    piv = piv.loc[piv.sum(axis=1).sort_values().index]
    fig = go.Figure()
    for n in names:
        if n in piv:
            fig.add_bar(y=piv.index, x=piv[n], name=n, orientation="h",
                        marker=dict(color=color[n], cornerradius=4),
                        hovertemplate=f"{n}<br>%{{y}}: %{{x}}편<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08,
                      title=f"분야별 논문 수 ({yr[0]}–{yr[1]})")
    _plot(fig, height=max(300, 22 * len(piv) * max(1, len(names)) // 2 + 120), key=f"{kp}_area")

    full = metrics.person_series(con(), names, metrics.Filter(start=1970, end=THIS_YEAR, areas=flt.areas,
                                                               next_tier=flt.next_tier, venues=flt.venues))
    fig = go.Figure()
    for n in names:
        d = full[full["name"] == n].groupby("year").pubs.sum().sort_index().cumsum()
        if d.empty:
            continue
        base = ages.phd_year_eff.get(n) if n in ages.index else None
        x = d.index - base if base is not None and pd.notna(base) else d.index
        fig.add_scatter(x=x, y=d.values, name=n, mode="lines+markers",
                        line=dict(color=color[n], width=2), marker=dict(size=8),
                        hovertemplate=f"{n}<br>PhD 후 %{{x}}년: 누적 %{{y:.0f}}편<extra></extra>")
    fig.update_layout(title="누적 논문 수 — PhD 취득 연도 기준 정렬 (x=PhD 후 연차, 추정 포함)",
                      hovermode="x unified")
    fig.update_xaxes(title="PhD 후 연차", zeroline=True, zerolinecolor="rgba(128,128,128,0.4)")
    _plot(fig, height=380, key=f"{kp}_cum")

    fig = go.Figure()
    for n in names:
        d = pp[n].dropna(subset=["cites"]).groupby("year").cites.sum().sort_index().cumsum()
        if d.empty:
            continue
        base = ages.phd_year_eff.get(n) if n in ages.index else None
        x = d.index - base if base is not None and pd.notna(base) else d.index
        fig.add_scatter(x=x, y=d.values, name=n, mode="lines+markers",
                        line=dict(color=color[n], width=2), marker=dict(size=8),
                        hovertemplate=f"{n}<br>PhD 후 %{{x}}년에 낸 논문까지: 누적 피인용 %{{y:,.0f}}<extra></extra>")
    fig.update_layout(title="누적 피인용 — 논문 출판 연도 기준 (x=PhD 후 연차, 현재 피인용 수)", hovermode="x unified")
    fig.update_xaxes(title="PhD 후 연차", zeroline=True, zerolinecolor="rgba(128,128,128,0.4)")
    _plot(fig, height=380, key=f"{kp}_cumcite")


ui.top_venue_status()
# 학교 비교 → merged into 국내 기관 (ranking + citations + field charts live there)
tab_search, tab_cmp, tab_status = st.tabs(["교수·후보자 검색", "교수 비교", "데이터 현황"])


# ───────────────────────── 교수·후보자 검색 ─────────────────────────

with tab_search:
    scope_lbl = {"kr": "국내 교수만", "korean": "한국인 연구자 전체 (교수·포닥·박사과정·산업체)",
                 "world": "전 세계 교수"}
    scope = st.radio("검색 범위", list(scope_lbl), format_func=scope_lbl.get, horizontal=True, key="scope", index=1,
                     help="포닥·학생 등 교수가 아닌 사람은 '한국인 연구자 전체'에서 나옵니다 (한국인으로 분류된 사람만 추적).")
    STAGES = ["국내 교수", "해외 교수", age.STUDENT, age.POSTDOC, age.SENIOR, age.INDUSTRY, age.UNKNOWN]
    roles, loc = None, "전체"
    if scope == "korean":
        r1, r2 = st.columns([4, 2])
        roles = r1.multiselect(
            "구분", STAGES, key="roles",
            help="교수 = CSrankings 명단. 그 외는 최근 논문 소속(OpenAlex)의 기관 유형과 학문적 나이로 추정: "
                 f"기업 소속 = 산업체. 박사 연도를 알면 박사 전 = 박사과정, 박사 후 0–{age.POSTDOC_MAX_ACAD_AGE}년 = 포닥. "
                 f"모르면 첫 top 논문 후 {age.STUDENT_MAX_YEARS}년 이내 = 박사과정, {age.POSTDOC_MAX_YEARS}년 이내 = 포닥. "
                 "그 이후 = 연구원·명단 외 교수(명예교수·산업체 겸직 등). 추정이라 경계 부근은 틀릴 수 있습니다.")
        loc = r2.radio("소속 위치", ["전체", "국내", "해외"], horizontal=True, key="loc")
    c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
    schools = sorted({a for _, a in metrics.people(con(), "kr" if scope == "kr" else None)})
    sch = c1.multiselect("소속", schools, help="명단 외 인물은 OpenAlex 기관명을 CSrankings 학교명으로 매핑해 필터합니다")
    age_rng = c2.slider("학문적 나이 (PhD 후 연수)", 0, 45, (0, 45))
    min_pubs = c3.number_input("최소 논문 수", 0, 200, 0, 1)
    window = c4.number_input("코호트 ±", 1, 5, 2)
    t1, t2, t3 = st.columns([2, 4, 2])
    sort = t1.selectbox("순위 기준", list(age.SORTS), format_func=age.SORTS.get, key="sort")
    known_only = t3.toggle("확인된 나이만 (ORCID·홈페이지·수동)", value=False)
    sf = t2.multiselect("분야 (이 검색에만 적용, 비우면 사이드바 필터)", list(B.FIELDS),
                        format_func=lambda k: f"{k} · {B.FIELDS[k][0]}", key="search_fields")
    G = metrics.Filter(**{**F.__dict__, "schools": sch or None})
    if sf:
        G.areas, G.venues = None, B.field_venues(con(), sf, next_tier)
    rows, cols = age.candidates(
        con(), G,
        age_min=age_rng[0] if age_rng[0] > 0 else None,
        age_max=age_rng[1] if age_rng[1] < 45 else None,
        min_pubs=min_pubs, cohort_window=window, limit=2000 if scope == "world" else None, scope=scope, sort=sort,
    )
    cdf = pd.DataFrame(rows, columns=cols)
    stg = dict(con().execute("SELECT person, stage FROM person_stage").fetchall())
    where_ = cdf.role.map({"KR faculty": "국내", "faculty abroad": "해외", "other · KR": "국내",
                           "other · abroad": "해외"}).fillna("미상")
    cdf["role"] = [{"KR faculty": "국내 교수", "faculty abroad": "해외 교수"}.get(r) or stg.get(n) or age.UNKNOWN
                   for r, n in zip(cdf.role, cdf.name)]
    keep = pd.Series(True, index=cdf.index)
    if roles:
        keep &= cdf.role.isin(roles)
    if loc != "전체":
        keep &= where_ == loc
    if known_only:
        keep &= ~cdf.age_src.fillna("est").str.startswith("est")
    cdf = cdf[keep].copy()
    if roles or loc != "전체" or known_only:
        cdf["rank"] = range(1, len(cdf) + 1)
    cdf["추정"] = cdf.age_src.fillna("est").str.startswith("est")
    cdf["나이추정"] = cdf.birth_src.fillna("est").str.startswith("est")
    st.caption(
        f"{len(cdf)}명 · pctl = 국내 교수 중 같은 분야, 학문적 나이 ±{window}년 집단에서의 백분위 (기간 {yr[0]}–{yr[1]})"
    )
    view = cdf[["rank", "name", "institution", "role", "affil_year", "age", "나이추정", "acad_age", "추정", "phd_year",
                "birth_year", "pubs",
                "first_au", "last_au", "cites", "cites_all", "cohort_pctl", "cohort_n", "age_src"]]
    pick_i = ui.clickable_table(
        view, "search_tab", "name", link_label="이름",
        link_help="클릭하면 논문 목록·피인용·나이 근거가 아래에 열립니다", height=420,
        column_config={
            "rank": st.column_config.NumberColumn("순위", help="선택한 순위 기준으로 매긴 검색 결과 내 순위"),
            "age": st.column_config.NumberColumn(
                "나이", help="올해 − 출생연도. 출생연도가 없으면 PhD−30, 학사−23, 첫 top 논문−28로 추정 (±3년 정도 오차)"),
            "나이추정": ui.AGE_COLS["age_est"],
            "acad_age": st.column_config.NumberColumn("학문적 나이", help="올해 − 박사 연도 (박사 전이면 음수)"),
            "추정": st.column_config.CheckboxColumn(
                "학문적 나이 추정값", help="체크 = 박사 연도를 확인하지 못한 추정값 (LLM 지식 또는 첫 top 논문+2년). "
                                     "자세한 근거는 '학문적 나이 출처' 열 제목에 마우스를 올려 보세요"),
            "cohort_pctl": st.column_config.ProgressColumn("pctl", min_value=0, max_value=100, format="%d"),
            "phd_year": st.column_config.NumberColumn("PhD", format="%d"),
            "birth_year": st.column_config.NumberColumn("출생(추정 포함)", format="%d"),
            "role": st.column_config.TextColumn("구분", help="교수 외 구분(박사과정·포닥 등)은 소속 기관 유형과 학문적 나이로 추정"),
            "affil_year": st.column_config.NumberColumn("소속 연도", format="%d",
                                                        help="비교수: 소속을 확인한 가장 최근 논문의 연도 (OpenAlex)"),
            "first_au": st.column_config.NumberColumn("1저자"),
            "cites_all": st.column_config.NumberColumn("피인용(경력 전체)", help="모든 분야·연도의 대상 학회 논문 피인용 합"),
            "cites": st.column_config.NumberColumn(
                "피인용", help="선택한 분야·기간의 대상 학회 논문의 OpenAlex 피인용 합. DOI로 매칭하고, DOI가 없는 "
                             "논문(NeurIPS·ICLR 등)은 제목 일치로 매칭. 아직 매칭 못 한 논문은 빠져 있음"),
            "last_au": st.column_config.NumberColumn("마지막저자"),
        },
    )
    if pick_i is not None:
        pr = view.iloc[pick_i]
        st.markdown(f"▼ **{pr['name']}** 선택됨 — 자세한 정보 (다른 이름을 클릭하면 바뀝니다)")
        ui.person_card(pr["name"], G, key="search_papers")
    else:
        st.caption("파란 이름을 클릭하면 그 사람의 논문 목록·피인용·나이 근거가 아래에 열립니다.")
    if scope != "kr":
        st.caption("명단 외 인물의 소속은 본인 최근 논문의 OpenAlex 소속(‘소속 연도’ 기준)입니다. "
                   "한국인 여부는 이름·공저 기반 추정입니다.")
    chosen = st.multiselect(
        f"비교할 사람 (결과 목록에서 2–{MAX_COMPARE}명 선택 → 바로 아래에 비교가 나옵니다)",
        sorted(view["name"].tolist()), max_selections=MAX_COMPARE, key="pick",
    )
    st.download_button(
        "CSV (개인정보 제외)",
        cdf.drop(columns=list(age.PII_COLUMNS & set(cdf.columns)) + ["추정"]).to_csv(index=False),
        "candidates.csv",
    )
    if len(chosen) == 1:
        st.info("한 명만 선택했습니다. 비교하려면 한 명 이상 더 고르세요 (한 사람의 상세 정보는 위 표에서 이름을 클릭).")
    if len(chosen) >= 2:
        st.divider()
        st.subheader("비교: " + " vs ".join(chosen))
        render_compare(chosen, "search_cmp", G)


# ───────────────────────── 교수 비교 ─────────────────────────

with tab_cmp:
    opts = people_list(None)
    name_of = {o: o.split(" — ")[0] for o in opts}
    sel = st.multiselect(f"비교할 사람 (최대 {MAX_COMPARE}명)", opts, max_selections=MAX_COMPARE, key="cmp_pick")
    names = [name_of[o] for o in sel]
    if len(names) < 1:
        st.info("이름을 검색해 선택하세요. 검색 탭에서 행을 골라 넘길 수도 있습니다.")
    else:
        render_compare(names, "cmp")


# ───────────────────────── 데이터 현황 ─────────────────────────

with tab_status:
    st.subheader("스냅샷")
    st.dataframe(metrics.snapshots(con()), hide_index=True)
    st.subheader("국내 교수 학문적 나이 출처")
    cov = con().execute(
        """SELECT coalesce(a.age_source, 'none') AS source, count(*) AS faculty
           FROM person_age a JOIN institution i ON i.institution = a.affiliation
           WHERE i.country = 'kr' GROUP BY 1 ORDER BY 2 DESC"""
    ).df()
    st.dataframe(cov, hide_index=True)
    st.subheader("수집 로그")
    st.dataframe(
        con().execute("SELECT source, status, count(*) n, max(fetched_at) last FROM pii.fetch_log GROUP BY ALL ORDER BY ALL").df(),
        hide_index=True,
    )
