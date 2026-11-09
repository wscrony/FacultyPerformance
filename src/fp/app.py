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


def _plot(fig: go.Figure, height: int = 420):
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
    st.plotly_chart(fig, width="stretch")


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

tab_school, tab_search, tab_cmp, tab_status = st.tabs(["학교 비교", "교수·후보자 검색", "교수 비교", "데이터 현황"])


# ───────────────────────── 학교 비교 ─────────────────────────

with tab_school:
    rows, cols = metrics.school_ranking(con(), F)
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        st.info("조건에 맞는 데이터가 없습니다.")
    else:
        df["pubs_per_faculty"] = (df.pubs_total / df.faculty).round(1)
        top = df.head(40 if region == "world" else 20)
        c1, c2 = st.container(), st.container()
        with c1:
            st.subheader("순위")
            st.dataframe(
                top[["institution", "score", "faculty", "pubs_total", "pubs_per_faculty"]].round(2),
                hide_index=False, width="stretch", height=min(560, 36 * len(top) + 40),
            )
        with c2:
            st.subheader("학교 × 분야 (논문 수)")
            m = metrics.school_area_matrix(con(), F)
            m = m[m.institution.isin(top.institution.head(20))]
            piv = m.pivot_table(index="institution", columns="area", values="pubs", aggfunc="sum").fillna(0)
            piv = piv.loc[[i for i in top.institution if i in piv.index]]
            piv = piv[piv.sum().sort_values(ascending=False).index]
            norm = st.radio("값", ["절대값", "교수 1인당", "학교 내 비중(%)"], horizontal=True,
                            help="ML/Vision이 커서 다른 분야가 묻힐 때 비중 보기가 유용합니다")
            z, unit = piv, "편"
            if norm == "교수 1인당":
                z, unit = piv.div(top.set_index("institution").faculty.reindex(piv.index), axis=0), "편/교수"
            elif norm == "학교 내 비중(%)":
                z, unit = piv.div(piv.sum(axis=1), axis=0) * 100, "%"
            fig = go.Figure(go.Heatmap(
                z=z.values, x=z.columns, y=z.index, colorscale=SEQ_BLUE, xgap=2, ygap=2,
                customdata=piv.values,
                hovertemplate="%{y}<br>%{x}: %{z:.1f} " + unit + " (논문 %{customdata:.0f}편)<extra></extra>",
                colorbar=dict(title=unit),
            ))
            fig.update_yaxes(autorange="reversed")
            fig.update_xaxes(tickangle=-45, dtick=1)
            _plot(fig, height=max(360, 26 * len(piv) + 120))
        st.download_button("CSV 다운로드", df.drop(columns=["by_area"]).to_csv(index=False), "schools.csv")


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
    st.dataframe(
        view, hide_index=True, width="stretch", height=420, key="search_tab",
        on_select="rerun", selection_mode=["single-row", "single-cell"],
        column_config={
            "rank": st.column_config.NumberColumn("순위", help="선택한 순위 기준으로 매긴 검색 결과 내 순위"),
            "age": st.column_config.NumberColumn(
                "나이", help="올해 − 출생연도. 출생연도가 없으면 PhD−30, 학사−23, 첫 top 논문−28로 추정 (±3년 정도 오차)"),
            "나이추정": st.column_config.CheckboxColumn("나이 추정값"),
            "acad_age": st.column_config.NumberColumn("학문적 나이"),
            "cohort_pctl": st.column_config.ProgressColumn("pctl", min_value=0, max_value=100, format="%d"),
            "phd_year": st.column_config.NumberColumn("PhD", format="%d"),
            "birth_year": st.column_config.NumberColumn("출생(추정 포함)", format="%d"),
            "role": st.column_config.TextColumn("구분", help="교수 외 구분(박사과정·포닥 등)은 소속 기관 유형과 학문적 나이로 추정"),
            "affil_year": st.column_config.NumberColumn("소속 연도", format="%d",
                                                        help="비교수: 소속을 확인한 가장 최근 논문의 연도 (OpenAlex)"),
            "first_au": st.column_config.NumberColumn("1저자"),
            "cites_all": st.column_config.NumberColumn("피인용(경력 전체)", help="모든 분야·연도의 대상 학회 DOI 논문 합"),
            "cites": st.column_config.NumberColumn(
                "피인용", help="선택한 분야·기간의 대상 학회 논문 중 DOI가 있는 논문의 OpenAlex 피인용 합. "
                             "NeurIPS·ICML·ICLR·USENIX 등 DOI 없는 학회는 빠져 있어 ML·시스템 분야는 과소집계"),
            "last_au": st.column_config.NumberColumn("마지막저자"),
        },
    )
    sel = st.session_state.get("search_tab")
    picked_rows = []
    if sel is not None and hasattr(sel, "selection"):  # a row checkbox, or any cell (e.g. the name) in that row
        picked_rows = list(sel.selection.get("rows") or []) or [c[0] for c in (sel.selection.get("cells") or [])]
    if picked_rows and picked_rows[0] < len(view):
        pr = view.iloc[picked_rows[0]]
        ui.person_card(pr["name"], G, key="search_papers")
    else:
        st.caption("이름(또는 행의 아무 칸)을 클릭하면 그 사람의 논문 목록·피인용·나이 근거가 여기에 나옵니다.")
    if scope != "kr":
        st.caption("명단 외 인물의 소속은 본인 최근 논문의 OpenAlex 소속(‘소속 연도’ 기준)입니다. "
                   "한국인 여부는 이름·공저 기반 추정입니다.")
    chosen = st.multiselect(
        f"상세 보기 / 비교 대상 (결과 목록에서 선택, 최대 {MAX_COMPARE}명 · 첫 번째 사람의 프로필이 아래에 표시)",
        sorted(view["name"].tolist()), max_selections=MAX_COMPARE, key="pick",
    )
    b1, b2 = st.columns([1, 4])
    if b1.button("선택한 사람 비교 →", disabled=not chosen):
        label = {o.split(" — ")[0]: o for o in people_list(None)}
        st.session_state.cmp_pick = [label[n] for n in chosen[:MAX_COMPARE] if n in label]
        st.toast("'교수 비교' 탭으로 이동하세요")
    b2.download_button(
        "CSV (개인정보 제외)",
        cdf.drop(columns=list(age.PII_COLUMNS & set(cdf.columns)) + ["추정"]).to_csv(index=False),
        "candidates.csv",
    )

    if chosen:
        who = chosen[0]
        st.divider()
        st.subheader(who)
        lk = links.person_links(con(), who)
        st.markdown(" · ".join(
            f"[{k}{' (검색)' if kind == 'search' else ''}]({u})" for k, (u, kind) in lk.items()))
        d1, d2 = st.columns([2, 3])
        with d1:
            st.markdown("**학위·나이 근거** (로컬 암호화 DB)")
            deg = con().execute(
                "SELECT degree, year, institution, source, evidence FROM pii.degree WHERE person=? ORDER BY degree",
                [who],
            ).df()
            st.dataframe(deg, hide_index=True, width="stretch")
            r = con().execute(
                "SELECT phd_year_eff, age_source, academic_age, birth_year, birth_source, first_top_year "
                "FROM person_age WHERE person=?", [who]
            ).df()
            st.dataframe(r, hide_index=True, width="stretch")
        with d2:
            s = metrics.person_series(con(), [who], metrics.Filter(start=1970, end=THIS_YEAR,
                                                                   areas=sel_areas, next_tier=next_tier, venues=sel_venues))
            by_area = s.groupby("area").pubs.sum().sort_values()
            fig = go.Figure(go.Bar(x=by_area.values, y=by_area.index, orientation="h",
                                   marker=dict(color=SERIES[0], cornerradius=4),
                                   hovertemplate="%{y}: %{x}편<extra></extra>"))
            fig.update_layout(title="분야별 논문 수 (전체 경력)")
            _plot(fig, height=max(240, 28 * len(by_area) + 80))
            by_year = s.groupby("year").pubs.sum()
            fig = go.Figure(go.Bar(x=by_year.index, y=by_year.values, marker=dict(color=SERIES[0], cornerradius=4),
                                   hovertemplate="%{x}: %{y}편<extra></extra>"))
            fig.update_layout(title="연도별 논문 수")
            _plot(fig, height=260)


# ───────────────────────── 교수 비교 ─────────────────────────

with tab_cmp:
    opts = people_list(None)
    name_of = {o: o.split(" — ")[0] for o in opts}
    sel = st.multiselect(f"비교할 사람 (최대 {MAX_COMPARE}명)", opts, max_selections=MAX_COMPARE, key="cmp_pick")
    names = [name_of[o] for o in sel]
    if len(names) < 1:
        st.info("이름을 검색해 선택하세요. 검색 탭에서 행을 골라 넘길 수도 있습니다.")
    else:
        color = {n: SERIES[i] for i, n in enumerate(names)}
        s = metrics.person_series(con(), names, F)
        ages = con().execute(
            "SELECT person, phd_year_eff, age_source FROM person_age WHERE person IN (SELECT unnest(?))", [names]
        ).df().set_index("person")
        info = pd.DataFrame({
            "name": names,
            "학문적 나이": [THIS_YEAR - ages.phd_year_eff.get(n) if n in ages.index and pd.notna(ages.phd_year_eff.get(n)) else None for n in names],
            "나이 출처": [ages.age_source.get(n) if n in ages.index else None for n in names],
            f"논문 {yr[0]}–{yr[1]}": [int(s[s["name"] == n].pubs.sum()) for n in names],
        })
        ui.people_table(ui.add_age(info, after="학문적 나이"), "cmp_info", F)

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
        _plot(fig, height=max(300, 22 * len(piv) * max(1, len(names)) // 2 + 120))

        full = metrics.person_series(con(), names, metrics.Filter(start=1970, end=THIS_YEAR, areas=sel_areas,
                                                                   next_tier=next_tier, venues=sel_venues))
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
        _plot(fig, height=380)


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
