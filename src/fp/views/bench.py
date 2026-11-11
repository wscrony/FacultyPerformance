"""University benchmark: one base school (default POSTECH) against chosen peers (default SNU, KAIST, Stanford,
Princeton, U Tokyo). Seven views — summary, field ranks & strengths, time series, groups, citations, faculty,
venues — chosen with a segmented control so only the open view is computed."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from fp import age
from fp import boards as B
from fp import ui

ui.require_db()
c = ui.con()
B.ensure_pinst(c)
age.create_views(c)  # paper_cites, person_age
Y = ui.THIS_YEAR

st.title("대학 벤치마크")
st.caption("기준 학교 하나를 비교 대학들과 나란히 놓고 교수 1인당 실적, 분야별 순위·강약, 변화, 교수 집단을 봅니다. "
           "교수 = CSrankings 명단(현재 기준, CS 관련 여러 학과 포함). 논문 = 그 학교 교수가 저자인 top 학회 논문 "
           "(공저는 학교당 1편).")
ui.top_venue_status()


@st.cache_data(ttl=3600)
def _faculty() -> pd.DataFrame:
    return ui.con().execute(
        """SELECT i.institution, i.country, count(DISTINCT c.name) AS faculty
           FROM cs_faculty c JOIN institution i ON i.institution = c.affiliation GROUP BY 1, 2"""
    ).df()


fac_all = _faculty()
insts = fac_all.sort_values("faculty", ascending=False).institution.tolist()
DEFAULT = ["POSTECH", "Seoul National University", "KAIST", "Stanford University", "Princeton University",
           "University of Tokyo"]
a1, a2 = st.columns([1.3, 4])
base = a1.selectbox("기준 학교", insts, index=insts.index("POSTECH") if "POSTECH" in insts else 0, key="bm_base")
peers = a2.multiselect("비교 대학 (최대 9)", [i for i in insts if i != base], max_selections=9, key="bm_peers",
                       default=[i for i in DEFAULT if i in insts and i != base])
b1, b2, b3 = st.columns([3, 2.2, 1.4])
y0, y1 = b1.slider("기간", 2000, Y, (Y - 9, Y), key="bm_years")
DEN = {"all": "CSrankings 교수 전체", "active": "그 분야 논문 있는 교수"}
den = b2.radio("1인당 기준 (분야별)", list(DEN), format_func=DEN.get, horizontal=True, key="bm_den",
               help="분야별 1인당을 학교 전체 교수로 나눌지(학교 규모 대비), 그 분야에서 논문을 낸 교수로 나눌지"
                    "(그 분야 인력의 생산성)")
nt = b3.toggle("확장 학회 포함", value=False, key="bm_nt")
schools = [base] + peers
if not peers:
    st.info("비교할 대학을 하나 이상 고르세요.")
    st.stop()

FS = B.field_sql("v")
from fp import topvenues  # noqa: E402

TV = topvenues.version()


def _q(sql: str, params: list) -> pd.DataFrame:
    cc = ui._root_con().cursor()
    try:
        B.ensure_pinst(cc)
        age.create_views(cc)
        return cc.execute(sql, params).df()
    finally:
        cc.close()


@st.cache_data(ttl=3600, max_entries=8)
def _load(schools: tuple, y0: int, y1: int, nt: bool, tv: float):
    return _q(f"""SELECT x.institution, x.key, x.year, x.name, x.is_last, x.venue, v.area, v.area_title,
                         ({FS}) AS field, pc.cited_by
                  FROM pinst x JOIN venue v ON v.venue = x.venue LEFT JOIN paper_cites pc ON pc.key = x.key
                  WHERE x.institution IN (SELECT unnest(?)) AND x.year BETWEEN ? AND ? AND (? OR NOT v.next_tier)""",
              [list(schools), y0, y1, nt])


@st.cache_data(ttl=3600, max_entries=4)
def _world(y0: int, y1: int, nt: bool, tv: float):
    """Every CSrankings institution × field × year: distinct papers (for world ranks)."""
    return _q(f"""SELECT x.institution, ({FS}) AS field, x.year, count(DISTINCT x.key) AS n
                  FROM pinst x JOIN venue v ON v.venue = x.venue
                  WHERE x.year BETWEEN ? AND ? AND (? OR NOT v.next_tier) GROUP BY 1, 2, 3""", [y0, y1, nt])


@st.cache_data(ttl=3600, max_entries=8)
def _people(schools: tuple):
    """Faculty of the schools with academic age (estimated) and KR department group."""
    have_dept = ui._root_con().execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = 'pii' AND table_name = 'department'"
    ).fetchone()[0]
    dj = "LEFT JOIN pii.department d ON d.person = f.name" if have_dept else ""
    dc = "d.dept_group" if have_dept else "NULL::VARCHAR"
    return _q(f"""SELECT f.name, f.affiliation AS institution, pa.academic_age, pa.age_source, {dc} AS dept
                  FROM (SELECT name, any_value(affiliation) AS affiliation FROM cs_faculty
                        WHERE affiliation IN (SELECT unnest(?)) GROUP BY 1) f
                  LEFT JOIN person_age pa ON pa.person = f.name {dj}""", [list(schools)])


@st.cache_data(ttl=3600, max_entries=8)
def _coauth(schools: tuple, y0: int, y1: int, nt: bool, tv: float):
    """All CSrankings-faculty authors (any institution) of the schools' papers — collaboration structure."""
    return _q("""SELECT x.key, x.institution, x.country FROM pinst x
                 WHERE x.key IN (SELECT y.key FROM pinst y JOIN venue v ON v.venue = y.venue
                                 WHERE y.institution IN (SELECT unnest(?)) AND y.year BETWEEN ? AND ?
                                   AND (? OR NOT v.next_tier))""", [list(schools), y0, y1, nt])


raw = _load(tuple(schools), y0, y1, nt, TV)
if raw.empty:
    st.info("이 조건에 논문이 없습니다.")
    st.stop()
raw["cited_by"] = raw.cited_by.astype(float)
pap = raw.drop_duplicates(["institution", "key"])  # one paper per school
nfac = fac_all.set_index("institution").faculty
NF = nfac.reindex(schools).astype(float).replace(0, np.nan)
pal = ui.series()
color = {s: (pal[i] if i < len(pal) else ui.gray()) for i, s in enumerate(schools)}  # 9th+ school folds to gray
S = {s: ui.short(s) for s in schools}
years = y1 - y0 + 1
YR = list(range(y0, y1 + 1))
FK = [k for k in B.FIELDS if k in set(pap.field)]
FLAB = {k: f"{k} · {B.FIELDS[k][0]}" for k in B.FIELDS}


def div_scale():
    mid = "#e8e6df" if ui.mode() == "light" else "#3a3935"
    return [[0, "#eb6834"], [0.5, mid], [1, "#2a78d6"]]


def rank_scale():  # rank 1 = darkest
    return [[0, ui.seq_scale()[-1][1]], [1, ui.seq_scale()[0][1]]]


def per_year(d: pd.DataFrame) -> pd.DataFrame:
    """year × school paper counts (all years/schools present)."""
    return d.groupby(["year", "institution"]).key.count().unstack(fill_value=0).reindex(
        index=YR, columns=schools, fill_value=0)


def hindex(c_: pd.Series) -> int:
    v = sorted((int(x) for x in c_.dropna()), reverse=True)
    return sum(1 for i, x in enumerate(v, 1) if x >= i)


def line(fig, x, y, s, **kw):
    fig.add_scatter(x=x, y=y, name=S[s], mode="lines+markers",
                    line=dict(color=color[s], width=3 if s == base else 2),
                    marker=dict(size=8 if s == base else 6), **kw)


# ── shared numbers ──
cov = pap.groupby("institution").cited_by.apply(lambda x: x.notna().mean() * 100).reindex(schools).fillna(0)
rows = []
for s in schools:
    p = pap[pap.institution == s]
    a = raw[raw.institution == s]
    n = int(nfac.get(s, 0))
    cites = p.cited_by.dropna()
    rows.append({
        "school": s, "faculty": n, "active": a.name.nunique(), "papers": len(p),
        "per_fac": len(p) / max(n, 1), "per_fac_yr": len(p) / max(n, 1) / years,
        "cites": int(cites.sum()), "cites_per_fac": cites.sum() / max(n, 1),
        "med_cites": float(cites.median()) if len(cites) else None, "cov": cov[s],
        "pi_share": p.key.isin(a[a.is_last].key).mean() * 100 if len(p) else None,
        "fields": int((p.groupby("field").key.count() >= 5).sum()),
    })
ov = pd.DataFrame(rows)
me = ov.iloc[0]
low_cov = [S[s] for s in schools if cov[s] < 60]


def rk_of(col: str) -> int:
    return int(ov[col].rank(ascending=False, method="min").iloc[0])


ui.kpis([
    (f"{S[base]} 교수", f"{int(me.faculty)}명", f"비교 대학 중앙값 {ov.faculty.iloc[1:].median():.0f}명"),
    ("교수 1인당 논문", f"{me.per_fac:.1f}편", f"{len(schools)}개 대학 중 {rk_of('per_fac')}위 · {y0}–{y1}"),
    ("교수 1인당 피인용", f"{me.cites_per_fac:,.0f}", f"{len(schools)}개 중 {rk_of('cites_per_fac')}위"),
    ("논문당 피인용 중앙값", f"{me.med_cites:.0f}" if pd.notna(me.med_cites) else "-",
     f"{len(schools)}개 중 {rk_of('med_cites')}위"),
])
if low_cov:
    st.warning("피인용을 확인한 논문 비율이 낮은 학교가 있어 피인용 비교가 부정확합니다: "
               + ", ".join(f"{S[s]} {cov[s]:.0f}%" for s in schools if cov[s] < 60)
               + " (OpenAlex 수집이 진행 중 — 하루 사용 한도 때문에 며칠 걸립니다).")

# field matrices (period)
fp_ = pap.groupby(["institution", "field"]).key.count().unstack(fill_value=0).reindex(
    index=schools, columns=FK, fill_value=0).astype(float)
actf = raw.groupby(["institution", "field"]).name.nunique().unstack(fill_value=0).reindex(
    index=schools, columns=FK, fill_value=0).astype(float)
per = fp_.div(NF, axis=0) if den == "all" else fp_ / actf.replace(0, np.nan)
cit = pap.groupby(["institution", "field"]).cited_by.sum().unstack(fill_value=0).reindex(
    index=schools, columns=FK, fill_value=0).astype(float)
cit_per = cit.div(NF, axis=0)
share = fp_.div(fp_.sum(axis=1).replace(0, np.nan), axis=0) * 100

VIEWS = ["요약", "분야별 순위·강약", "시계열", "그룹", "피인용·영향력", "교수", "학회"]
view = st.segmented_control("보기", VIEWS, default="요약", key="bm_view") or "요약"
st.divider()

# ════════════════════════════ 요약 ════════════════════════════
if view == "요약":
    pick = ui.clickable_table(ov, "bm_ov_tbl", "school", link_label="학교",
                              link_help="클릭하면 그 학교 교수 명단이 아래에 열립니다", column_config={
        "faculty": st.column_config.NumberColumn("교수", format="%d명"),
        "active": st.column_config.NumberColumn("논문 있는 교수", format="%d명", help="기간 내 1편 이상"),
        "papers": st.column_config.NumberColumn(f"논문 {y0}–{y1}", format="localized"),
        "per_fac": st.column_config.NumberColumn("교수 1인당", format="%.1f"),
        "per_fac_yr": st.column_config.NumberColumn("1인당 연간", format="%.2f"),
        "cites": st.column_config.NumberColumn("피인용 합", format="localized"),
        "cites_per_fac": st.column_config.NumberColumn("1인당 피인용", format="%.0f"),
        "med_cites": st.column_config.NumberColumn("논문당 피인용 중앙값", format="%.0f"),
        "cov": st.column_config.NumberColumn("피인용 확인 비율", format="%.0f%%",
                                             help="OpenAlex에서 피인용을 찾은 논문 비율 — 낮으면 피인용 값이 작게 나옴"),
        "pi_share": st.column_config.NumberColumn("교신(마지막) 저자 비율", format="%.0f%%",
                                                  help="마지막 저자가 그 학교 교수인 논문 비율 — 주도 연구 비중"),
        "fields": st.column_config.NumberColumn("5편 이상 분야 수", help="연구 폭")})
    if pick is None:
        st.caption("학교 이름을 클릭하면 그 학교 교수 명단(분야별 논문 수 포함)이 아래에 열립니다.")
    else:
        rs = ov.school.iloc[pick]
        st.markdown(f"▼ **{rs}** 교수 명단 ({int(nfac.get(rs, 0))}명, 논문 {y0}–{y1})")
        rf = st.selectbox("분야로 정렬", ["전체"] + FK, key="bm_roster_f", format_func=lambda k: FLAB.get(k, k))
        ppl = _people((rs,))
        d = raw[raw.institution == rs]
        cnt = d.groupby(["name", "field"]).key.nunique().unstack(fill_value=0).reindex(columns=FK, fill_value=0)
        ro = ppl[["name", "academic_age", "dept"]].set_index("name").join(cnt, how="left").fillna(
            {k: 0 for k in FK})
        ro["papers"] = d.groupby("name").key.nunique().reindex(ro.index).fillna(0).astype(int)
        ro["cites"] = d.drop_duplicates(["name", "key"]).groupby("name").cited_by.sum().reindex(ro.index).fillna(0)
        ro["main"] = cnt.idxmax(axis=1).where(cnt.max(axis=1) > 0).reindex(ro.index)
        ro = ro.reset_index().sort_values([rf if rf != "전체" else "papers", "papers"], ascending=False)
        cols = ["name", "academic_age", "main", "papers", "cites"] + ([rf] if rf != "전체" else []) + [
            k for k in FK if k != rf]
        if ro.dept.notna().any():
            cols.insert(2, "dept")
        ui.people_table(ro[cols].astype({k: int for k in FK}), f"bm_roster_{rs}",
                        B.BF(start=y0, end=y1, next_tier=nt, fields=[rf] if rf != "전체" else None),
                        height=min(600, 36 * len(ro) + 40), column_config={
                            "academic_age": st.column_config.NumberColumn("학문적 나이 (추정)", format="%d"),
                            "dept": "학과", "main": "주 분야",
                            "papers": st.column_config.NumberColumn("논문", format="%d"),
                            "cites": st.column_config.NumberColumn("피인용", format="localized"),
                            **{k: st.column_config.NumberColumn(k, format="%d") for k in FK}})
    g1, g2 = st.columns(2)
    for col_, m, lbl, fmt in [(g1, "per_fac", "교수 1인당 논문", ".1f"), (g2, "cites_per_fac", "교수 1인당 피인용", ",.0f")]:
        fig = go.Figure(go.Bar(
            x=[S[s] for s in ov.school], y=ov[m], marker=dict(color=[color[s] for s in ov.school], cornerradius=4),
            text=[format(v, fmt) for v in ov[m]], textposition="outside",
            hovertemplate="%{x}: %{y:" + fmt + "}<extra></extra>"))
        fig.update_layout(title=f"{lbl} ({y0}–{y1})")
        with col_:
            ui.plot(fig, height=340, key=f"bm_ov_{m}")

    st.subheader("양과 질 (B3)")
    qf = st.selectbox("분야", ["전체"] + FK, key="bm_qf", format_func=lambda k: FLAB.get(k, k))
    d = pap if qf == "전체" else pap[pap.field == qf]
    qd = pd.DataFrame({"school": schools,
                       "x": [len(d[d.institution == s]) / max(int(nfac.get(s, 0)), 1) for s in schools],
                       "y": [d[d.institution == s].cited_by.median() for s in schools],
                       "n": [len(d[d.institution == s]) for s in schools]})
    fig = go.Figure()
    for r in qd.itertuples():
        fig.add_scatter(x=[r.x], y=[r.y], name=S[r.school], mode="markers+text", text=[S[r.school]],
                        textposition="top center", marker=dict(size=10 + min(30, r.n / 40), color=color[r.school],
                                                               line=dict(width=2, color="white")),
                        hovertemplate=f"{S[r.school]}: 1인당 %{{x:.1f}}편 · 중앙값 %{{y:.0f}}회 ({r.n}편)<extra></extra>")
    fig.add_vline(x=qd.x.median(), line=dict(color=ui.gray(), dash="dot"))
    fig.add_hline(y=qd.y.median(), line=dict(color=ui.gray(), dash="dot"))
    fig.update_layout(title=f"{qf}: 오른쪽 = 많이 냄, 위 = 많이 인용됨 (점선 = 중앙값, 점 크기 = 논문 수)", showlegend=False)
    fig.update_xaxes(title="교수 1인당 논문")
    fig.update_yaxes(title="논문당 피인용 중앙값")
    ui.plot(fig, height=420, legend=False, key="bm_quad")

    st.subheader("분야 프로필 (B4)")
    z = per.div(per.max(axis=0).replace(0, np.nan), axis=1).fillna(0) * 100
    fig = go.Figure()
    for s in schools:
        fig.add_scatterpolar(r=list(z.loc[s]) + [z.loc[s].iloc[0]], theta=FK + [FK[0]], name=S[s],
                             line=dict(color=color[s], width=3 if s == base else 1.5),
                             fill="toself" if s == base else None, opacity=1 if s == base else 0.8,
                             hovertemplate=f"{S[s]} %{{theta}}: 1위의 %{{r:.0f}}%<extra></extra>")
    fig.update_layout(title=f"분야별 교수 1인당 논문 ({DEN[den]}) — 각 분야 1위 = 100",
                      polar=dict(radialaxis=dict(range=[0, 100], showticklabels=False)))
    ui.plot(fig, height=520, key="bm_radar")
    st.caption("교수 수는 지금 CSrankings 명단이라 과거 논문을 지금 교수 수로 나눈 값입니다. 학교마다 CSrankings에 "
               "들어간 학과 범위가 달라(전기전자·산업공학 포함 여부 등) 1인당 값에 영향이 있습니다.")

# ════════════════════════════ 분야별 순위·강약 ════════════════════════════
elif view == "분야별 순위·강약":
    MET = {"per": f"교수 1인당 논문 ({DEN[den]})", "cit": "교수 1인당 피인용", "abs": "논문 수 (절대)",
           "share": "분야 비중 (학교 논문 중 %)"}
    met = st.radio("지표", list(MET), format_func=MET.get, horizontal=True, key="bm_met")
    M = {"per": per, "cit": cit_per, "abs": fp_, "share": share}[met].astype(float)
    peer = M.iloc[1:]
    med = peer.median()
    tb = pd.DataFrame({
        "field": [FLAB[k] for k in M.columns], "key": list(M.columns), "base": M.iloc[0].values,
        "peer_med": med.values, "peer_max": peer.max().values,
        "leader": [S[M[k].idxmax()] if M[k].max() > 0 else "" for k in M.columns],
        "ratio": (M.iloc[0] / med.replace(0, np.nan)).astype(float).values,
        "rank": M.rank(ascending=False, method="min").iloc[0].values.astype(int),
        "papers": fp_.iloc[0].values.astype(int)})
    tb["verdict"] = np.select([(tb.ratio >= 1.2).to_numpy(bool), (tb.ratio <= 0.6).to_numpy(bool)],
                              ["강점", "약점"], "비슷")
    tb.loc[tb.peer_med == 0, "verdict"] = "비교 불가"
    tb = tb.sort_values("ratio", ascending=False, na_position="last")
    st.markdown(f"**{S[base]}의 강점** (비교 대학 중앙값의 1.2배 이상): "
                f"{', '.join(tb[tb.verdict == '강점'].key) or '없음'}  \n"
                f"**약점** (0.6배 이하): {', '.join(tb[tb.verdict == '약점'].key) or '없음'} — 지표: {MET[met]}")
    lr = np.log2(tb.ratio.clip(lower=1 / 8, upper=8).fillna(1))
    fig = go.Figure(go.Bar(
        y=tb.key, x=lr, orientation="h", marker=dict(color=lr, colorscale=div_scale(), cmin=-2, cmax=2,
                                                     cornerradius=4),
        customdata=np.stack([tb.base, tb.peer_med, tb.ratio.fillna(0)], axis=1),
        hovertemplate="%{y}: " + S[base] + " %{customdata[0]:.2f} vs 중앙값 %{customdata[1]:.2f} "
                      "(×%{customdata[2]:.2f})<extra></extra>"))
    fig.update_layout(title=f"{S[base]} ÷ 비교 대학 중앙값 (오른쪽 = 강점)", yaxis=dict(autorange="reversed"))
    fig.update_xaxes(tickvals=[-2, -1, 0, 1, 2], ticktext=["×¼", "×½", "×1", "×2", "×4"], zeroline=True)
    ui.plot(fig, height=max(320, 30 * len(tb) + 80), key="bm_ratio")
    st.dataframe(tb.drop(columns="key"), hide_index=True, width="stretch", column_config={
        "field": "분야", "base": st.column_config.NumberColumn(S[base], format="%.2f"),
        "peer_med": st.column_config.NumberColumn("비교 대학 중앙값", format="%.2f"),
        "peer_max": st.column_config.NumberColumn("비교 대학 최고", format="%.2f"), "leader": "1위 학교",
        "ratio": st.column_config.NumberColumn("중앙값 대비", format="×%.2f"),
        "rank": st.column_config.NumberColumn(f"순위 / {len(schools)}", format="%d위"),
        "papers": st.column_config.NumberColumn(f"{S[base]} 논문", format="%d편"), "verdict": "판정"})

    st.subheader("분야별 순위표 (B1)")
    rk_rows = {}
    for lbl, a_ in [(f"전체 {y0}–{y1}", y0), (f"최근 5년 {max(y0, y1 - 4)}–{y1}", max(y0, y1 - 4)),
                    (f"최근 3년 {max(y0, y1 - 2)}–{y1}", max(y0, y1 - 2))]:
        d = pap[pap.year >= a_]
        m_ = d.groupby(["institution", "field"]).key.count().unstack(fill_value=0).reindex(
            index=schools, columns=FK, fill_value=0).div(NF, axis=0)
        rk_rows[lbl] = m_.rank(ascending=False, method="min")
    pick = st.radio("기간", list(rk_rows), horizontal=True, key="bm_rkp")
    R = rk_rows[pick]
    fig = go.Figure(go.Heatmap(
        z=R.values, x=FK, y=[S[s] for s in schools], colorscale=rank_scale(), zmin=1, zmax=len(schools),
        text=R.values.astype(int), texttemplate="%{text}위", showscale=False,
        hovertemplate="%{y} · %{x}: %{z:.0f}위<extra></extra>"))
    fig.update_layout(title=f"교수 1인당 논문 순위 ({pick}) — 진할수록 상위", yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=60 * len(schools) + 120, key="bm_rk")
    ch = (rk_rows[list(rk_rows)[0]] - rk_rows[list(rk_rows)[2]]).loc[base]
    up = [f"{k}(↑{int(v)})" for k, v in ch.items() if v > 0]
    dn = [f"{k}(↓{int(-v)})" for k, v in ch.items() if v < 0]
    st.caption(f"{S[base]}: 전체 기간 대비 최근 3년 순위가 오른 분야 {', '.join(up) or '없음'} · "
               f"내린 분야 {', '.join(dn) or '없음'}")

    st.subheader("세계 순위 (B2)")
    w = _world(y0, y1, nt, TV)
    minf = st.slider("1인당 순위에 넣을 최소 교수 수", 5, 60, 15, key="bm_minf",
                     help="교수가 아주 적은 기관이 1인당 순위를 왜곡하지 않게")
    wt = w.groupby(["institution", "field"]).n.sum().unstack(fill_value=0)
    wf = nfac.reindex(wt.index)
    wp = wt.div(wf.replace(0, np.nan), axis=0)[wf >= minf]
    wr = []
    for k in FK:
        if k not in wt:
            continue
        r1 = wt[k].rank(ascending=False, method="min")
        r2 = wp[k].rank(ascending=False, method="min")
        wr.append({"field": FLAB[k], **{S[s]: (f"{int(r1.get(s))}위 · 1인당 {int(r2.get(s))}위"
                                               if s in r1 and wt.at[s, k] > 0 and s in r2 and pd.notna(r2.get(s))
                                               else (f"{int(r1.get(s))}위" if s in r1 and wt.at[s, k] > 0 else "-"))
                                        for s in schools}})
    st.dataframe(pd.DataFrame(wr), hide_index=True, width="stretch")
    st.caption(f"CSrankings 전체 {len(wt):,}개 기관 중 순위: 논문 수 기준 · 교수 1인당 기준(교수 {minf}명 이상 기관만).")
    wk = st.selectbox("세계 순위 추이를 볼 분야", ["전체"] + FK, key="bm_wk", format_func=lambda k: FLAB.get(k, k))
    ww = w if wk == "전체" else w[w.field == wk]
    yy = ww.groupby(["year", "institution"]).n.sum().unstack(fill_value=0).reindex(YR, fill_value=0)
    roll = yy.rolling(3, min_periods=1).sum()
    rr = roll.rank(axis=1, ascending=False, method="min")
    fig = go.Figure()
    for s in schools:
        if s in rr:
            line(fig, rr.index, rr[s], s, hovertemplate=f"{S[s]} %{{x}} (3년 합): 세계 %{{y:.0f}}위<extra></extra>")
    fig.update_layout(title=f"세계 순위 추이 — {wk} (논문 수, 3년 합)", hovermode="x unified")
    fig.update_yaxes(autorange="reversed", type="log", title="세계 순위 (로그)")
    ui.plot(fig, height=400, key="bm_wtrend")

    st.subheader("특화 지수 (B5)")
    tot_share = fp_.sum(axis=0) / fp_.values.sum()
    rca = share.div(tot_share * 100, axis=1)
    lz = np.log2(rca.clip(lower=1 / 8, upper=8).fillna(1))
    fig = go.Figure(go.Heatmap(
        z=lz.values, x=FK, y=[S[s] for s in schools], colorscale=div_scale(), zmin=-2, zmax=2, showscale=False,
        text=[[f"×{v:.1f}" for v in row] for row in rca.fillna(0).values], texttemplate="%{text}",
        hovertemplate="%{y} · %{x}: 비교 대학 평균 비중의 %{text}<extra></extra>"))
    fig.update_layout(title="분야 비중 ÷ 비교 대학 전체 비중 — 파랑 = 그 학교가 특히 집중하는 분야",
                      yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=60 * len(schools) + 120, key="bm_rca")

    st.subheader("1위와의 거리 (B6)")
    pf_all = fp_.div(NF, axis=0)
    gap = pd.DataFrame({
        "field": [FLAB[k] for k in FK], "leader": [S[pf_all[k].idxmax()] for k in FK],
        "lead_v": [pf_all[k].max() for k in FK], "base_v": [pf_all.at[base, k] for k in FK]})
    gap["diff"] = gap.lead_v - gap.base_v
    gap["need_yr"] = gap["diff"] * nfac.get(base, 0) / years
    gap = gap.sort_values("need_yr")
    st.dataframe(gap, hide_index=True, width="stretch", column_config={
        "field": "분야", "leader": "1위", "lead_v": st.column_config.NumberColumn("1위 1인당", format="%.2f"),
        "base_v": st.column_config.NumberColumn(f"{S[base]} 1인당", format="%.2f"),
        "diff": st.column_config.NumberColumn("1인당 차이", format="%.2f"),
        "need_yr": st.column_config.NumberColumn("따라잡으려면 매년 더 필요한 논문", format="%.1f편",
                                                 help=f"1인당 차이 × {S[base]} 교수 수 ÷ 기간 연수")})

    with st.expander("세부 분야 (CSrankings area) 비교"):
        ap = pap.groupby(["institution", "area"]).key.count().unstack(fill_value=0).reindex(schools).fillna(0)
        title = dict(zip(pap.area, pap.area_title))
        ap = ap.loc[:, ap.sum() >= 5]
        apf = ap.div(NF, axis=0)
        pm = apf.iloc[1:].median()
        t2 = pd.DataFrame({"area": [f"{a} · {title.get(a, '')}" for a in apf.columns],
                           "field": [B.AREA_FIELD.get(a, "기타") for a in apf.columns],
                           "base": apf.iloc[0].values, "peer_med": pm.values,
                           "ratio": (apf.iloc[0] / pm.replace(0, np.nan)).astype(float).values,
                           "rank": apf.rank(ascending=False, method="min").iloc[0].astype(int).values,
                           "papers": ap.iloc[0].astype(int).values,
                           "leader": [S[apf[a].idxmax()] for a in apf.columns]})
        t2 = t2.sort_values("ratio", ascending=False, na_position="last")
        st.dataframe(t2, hide_index=True, width="stretch", height=min(700, 36 * len(t2) + 40), column_config={
            "area": "세부 분야", "field": "분야", "base": st.column_config.NumberColumn(S[base], format="%.3f"),
            "peer_med": st.column_config.NumberColumn("비교 중앙값", format="%.3f"),
            "ratio": st.column_config.ProgressColumn("중앙값 대비", format="×%.2f", min_value=0,
                                                     max_value=float(max(2.0, t2.ratio.max() or 2))),
            "rank": st.column_config.NumberColumn(f"순위 / {len(schools)}", format="%d위"),
            "papers": st.column_config.NumberColumn(f"{S[base]} 논문", format="%d편"), "leader": "1위"})

# ════════════════════════════ 시계열 ════════════════════════════
elif view == "시계열":
    sm = st.radio("표시", ["연도별", "3년 이동평균"], horizontal=True, key="bm_sm", index=1)
    sm3 = sm != "연도별"

    def smooth(df):
        return df.rolling(3, min_periods=1).mean() if sm3 else df

    st.subheader("학교 전체 교수 1인당 논문")
    pf = smooth(per_year(pap).div(NF, axis=1))
    fig = go.Figure()
    for s in schools:
        line(fig, pf.index, pf[s], s, hovertemplate=f"{S[s]} %{{x}}: %{{y:.2f}}편/교수<extra></extra>")
    fig.update_layout(title=f"교수 1인당 논문 ({sm})", hovermode="x unified")
    ui.plot(fig, height=380, key="bm_trend")

    st.subheader("분야별 추이 (A1)")
    rows_n = int(np.ceil(len(FK) / 4))
    fig = make_subplots(rows=rows_n, cols=4, subplot_titles=FK, shared_xaxes=True, vertical_spacing=0.08,
                        horizontal_spacing=0.05)
    for i, k in enumerate(FK):
        yk = smooth(per_year(pap[pap.field == k]).div(NF, axis=1))
        for s in schools:
            fig.add_scatter(x=yk.index, y=yk[s], name=S[s], legendgroup=s, showlegend=i == 0, mode="lines",
                            line=dict(color=color[s], width=2.5 if s == base else 1.3),
                            hovertemplate=f"{S[s]} {k} %{{x}}: %{{y:.2f}}<extra></extra>",
                            row=i // 4 + 1, col=i % 4 + 1)
    fig.update_layout(title=f"분야별 교수 1인당 논문 ({sm}) — 분야마다 세로축이 다름")
    ui.plot(fig, height=250 * rows_n + 80, legend=True, key="bm_small")

    st.subheader(f"{S[base]}의 분야별 순위 변화 (A2)")
    rk_y = {}
    for k in FK:
        yk = per_year(pap[pap.field == k]).rolling(3, min_periods=1).sum().div(NF, axis=1)
        rk_y[k] = yk.rank(axis=1, ascending=False, method="min")[base]
    RY = pd.DataFrame(rk_y).T
    fig = go.Figure(go.Heatmap(
        z=RY.values, x=RY.columns, y=FK, colorscale=rank_scale(), zmin=1, zmax=len(schools), showscale=False,
        text=RY.fillna(0).values.astype(int), texttemplate="%{text}", hovertemplate="%{y} %{x}: %{z:.0f}위<extra></extra>"))
    fig.update_layout(title=f"{S[base]}의 {len(schools)}개 대학 중 순위 (3년 합, 교수 1인당) — 진할수록 상위",
                      yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=30 * len(FK) + 120, key="bm_rky")

    st.subheader("학교 순위 변화 (A3)")
    m3 = st.radio("지표", ["교수 1인당 논문", "논문 수", "교수 1인당 피인용"], horizontal=True, key="bm_a3")
    if m3 == "교수 1인당 피인용":
        base_df = pap.groupby(["year", "institution"]).cited_by.sum().unstack(fill_value=0).reindex(
            index=YR, columns=schools, fill_value=0).div(NF, axis=1)
    else:
        base_df = per_year(pap)
        if m3 == "교수 1인당 논문":
            base_df = base_df.div(NF, axis=1)
    rr = base_df.rolling(3, min_periods=1).sum().rank(axis=1, ascending=False, method="min")
    fig = go.Figure()
    for s in schools:
        line(fig, rr.index, rr[s], s, hovertemplate=f"{S[s]} %{{x}}: %{{y:.0f}}위<extra></extra>")
    fig.update_layout(title=f"{m3} 순위 (3년 합)", hovermode="x unified")
    fig.update_yaxes(autorange="reversed", dtick=1, title="순위")
    ui.plot(fig, height=360, key="bm_bump")

    st.subheader("성장 경주 (A4)")
    cum = per_year(pap).cumsum()
    first = per_year(pap).iloc[:3].mean().replace(0, np.nan)
    idx = smooth(per_year(pap)).div(first, axis=1) * 100
    fig = go.Figure()
    for s in schools:
        line(fig, idx.index, idx[s], s, customdata=cum[s],
             hovertemplate=f"{S[s]} %{{x}}: 처음 3년 평균의 %{{y:.0f}}% (누적 %{{customdata}}편)<extra></extra>")
    fig.add_hline(y=100, line=dict(color=ui.gray(), dash="dot"))
    fig.update_layout(title=f"논문 수 지수 — 처음 3년 평균 = 100 (누가 더 빨리 늘었나)", hovermode="x unified")
    ui.plot(fig, height=380, key="bm_idx")

    st.subheader("분야 구성 변화 (A5)")
    cs = st.selectbox("학교", schools, key="bm_a5", format_func=lambda s: S[s])
    d = pap[pap.institution == cs]
    comp = d.groupby(["year", "field"]).key.count().unstack(fill_value=0).reindex(YR, fill_value=0)
    comp = comp.rolling(3, min_periods=1).sum()
    comp = comp.div(comp.sum(axis=1).replace(0, np.nan), axis=0) * 100
    top7 = comp.mean().sort_values(ascending=False).index[:7].tolist()
    comp["나머지"] = comp.drop(columns=top7).sum(axis=1)
    fig = go.Figure()
    for i, k in enumerate(top7 + ["나머지"]):
        fig.add_scatter(x=comp.index, y=comp[k], name=k, mode="lines", stackgroup="one",
                        line=dict(width=0.5, color=pal[i] if k != "나머지" else ui.gray()),
                        hovertemplate=f"{k} %{{x}}: %{{y:.0f}}%<extra></extra>")
    fig.update_layout(title=f"{S[cs]} 논문의 분야 비중 (%, 3년 합)", hovermode="x unified")
    fig.update_yaxes(range=[0, 100])
    ui.plot(fig, height=380, key="bm_comp")

    st.subheader(f"격차 지도 (A6) — {S[base]} ÷ 비교 대학 중앙값")
    gm = {}
    for k in FK:
        yk = per_year(pap[pap.field == k]).rolling(3, min_periods=1).sum().div(NF, axis=1)
        gm[k] = yk[base] / yk[peers].median(axis=1).replace(0, np.nan)
    G = pd.DataFrame(gm).T
    lg = np.log2(G.clip(lower=1 / 8, upper=8))
    fig = go.Figure(go.Heatmap(
        z=lg.values, x=G.columns, y=FK, colorscale=div_scale(), zmin=-2, zmax=2, showscale=False,
        text=[[f"×{v:.1f}" if pd.notna(v) else "" for v in row] for row in G.values], texttemplate="%{text}",
        hovertemplate="%{y} %{x}: 중앙값의 %{text}<extra></extra>"))
    fig.update_layout(title="파랑 = 비교 대학보다 높음, 주황 = 낮음 (3년 합, 교수 1인당) — 색이 바뀌는 해를 보세요",
                      yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=30 * len(FK) + 120, key="bm_gapmap")
    gap = pf[base] / pf[peers].median(axis=1).replace(0, np.nan)
    fig = go.Figure()
    fig.add_scatter(x=gap.index, y=gap.values, mode="lines+markers", name="전체",
                    line=dict(color=color[base], width=2), marker=dict(size=8),
                    hovertemplate="%{x}: ×%{y:.2f}<extra></extra>")
    fig.add_hline(y=1, line=dict(color=ui.gray(), dash="dot"))
    fig.update_layout(title=f"전체: {S[base]} ÷ 비교 대학 중앙값 — 1 위로 올라가면 따라잡는 중")
    ui.plot(fig, height=300, key="bm_gap")

    st.subheader("성장률 (A7)")
    k3 = min(3, years // 2) or 1
    gr = []
    for k in FK:
        yk = per_year(pap[pap.field == k])
        a_, b_ = yk.iloc[:k3].mean(), yk.iloc[-k3:].mean()
        span = max(1, years - k3)
        cg = ((b_.clip(lower=0.01) / a_.clip(lower=0.01)) ** (1 / span) - 1) * 100
        gr.append({"field": k, "base": cg[base], "peer": cg[peers].median(), "leader_now": S[b_.idxmax()]})
    gr = pd.DataFrame(gr)
    fig = go.Figure()
    fig.add_bar(x=gr.field, y=gr.base, name=S[base], marker=dict(color=color[base], cornerradius=4),
                hovertemplate=f"{S[base]} %{{x}}: 연 %{{y:+.1f}}%<extra></extra>")
    fig.add_bar(x=gr.field, y=gr.peer, name="비교 대학 중앙값", marker=dict(color=ui.gray(), cornerradius=4),
                hovertemplate="비교 중앙값 %{x}: 연 %{y:+.1f}%<extra></extra>")
    fig.update_layout(barmode="group", title=f"분야별 연평균 증가율 (처음 {k3}년 평균 → 마지막 {k3}년 평균)")
    fig.update_yaxes(ticksuffix="%")
    ui.plot(fig, height=360, key="bm_cagr")

    st.subheader("추세가 이어지면 (A8, 추정)")
    fc = []
    for k in FK:
        yk = per_year(pap[pap.field == k]).rolling(3, min_periods=1).mean().div(NF, axis=1)
        tail = yk.iloc[-min(6, len(yk)):]
        x = np.arange(len(tail))
        if len(tail) < 3:
            continue
        sb = np.polyfit(x, tail[base].values, 1)[0]
        others = [s for s in schools if s != base]
        lead = max(others, key=lambda s: tail[s].iloc[-1])
        sl = np.polyfit(x, tail[lead].values, 1)[0]
        g_ = tail[lead].iloc[-1] - tail[base].iloc[-1]
        if g_ <= 0:
            when = "이미 앞섬"
        elif sb - sl > 1e-6:
            yrs = g_ / (sb - sl)
            when = f"약 {yrs:.0f}년 뒤 ({y1 + int(np.ceil(yrs))}년)" if yrs <= 30 else "30년 이상"
        else:
            when = "격차 벌어지는 중"
        fc.append({"field": FLAB[k], "rival": S[lead], "gap": g_, "base_slope": sb, "rival_slope": sl, "when": when})
    st.dataframe(pd.DataFrame(fc), hide_index=True, width="stretch", column_config={
        "field": "분야", "rival": "비교 대학 중 1위", "gap": st.column_config.NumberColumn("현재 격차 (1인당/년)", format="%.3f"),
        "base_slope": st.column_config.NumberColumn(f"{S[base]} 추세 (매년)", format="%+.3f"),
        "rival_slope": st.column_config.NumberColumn("1위 추세 (매년)", format="%+.3f"),
        "when": st.column_config.TextColumn("따라잡는 시점", help="최근 6년 직선 추세가 그대로 이어진다고 가정한 단순 추정")})
    st.caption("최근 6년(3년 이동평균) 직선 추세로만 계산한 단순 추정입니다 — 실제 전망이 아닙니다.")

    mid = (y0 + y1) // 2
    h = []
    for k in FK:
        dk = pap[pap.field == k]
        a_ = dk[dk.year <= mid].groupby("institution").key.count().reindex(schools, fill_value=0) / NF
        b_ = dk[dk.year > mid].groupby("institution").key.count().reindex(schools, fill_value=0) / NF
        ra = a_.iloc[0] / a_.iloc[1:].median() if a_.iloc[1:].median() > 0 else np.nan
        rb = b_.iloc[0] / b_.iloc[1:].median() if b_.iloc[1:].median() > 0 else np.nan
        h.append({"field": k, "first": ra, "second": rb, "change": rb - ra})
    st.markdown(f"**전반 {y0}–{mid} → 후반 {mid + 1}–{y1}** (중앙값 대비 배수)")
    st.dataframe(pd.DataFrame(h).sort_values("change", ascending=False), hide_index=True, width="stretch",
                 column_config={"field": "분야", "first": st.column_config.NumberColumn("전반", format="×%.2f"),
                                "second": st.column_config.NumberColumn("후반", format="×%.2f"),
                                "change": st.column_config.NumberColumn("변화", format="%+.2f",
                                                                        help="+ = 비교 대학 대비 좋아짐")})

# ════════════════════════════ 그룹 ════════════════════════════
elif view == "그룹":
    ppl = _people(tuple(schools))
    pc = raw.groupby(["institution", "name"]).key.nunique().rename("papers").reset_index()
    ppl = ppl.merge(pc, on=["institution", "name"], how="left").fillna({"papers": 0})

    st.subheader("경력 단계별 실적 (C2)")
    bins = [-100, 5, 10, 20, 100]
    BL = ["0–5년", "6–10년", "11–20년", "21년+"]
    ppl["band"] = pd.cut(ppl.academic_age, bins=bins, labels=BL).astype(object).fillna("미상")
    bt = ppl.groupby(["institution", "band"]).agg(n=("name", "count"), papers=("papers", "sum"),
                                                  med=("papers", "median")).reset_index()
    bt["per"] = bt.papers / bt.n
    fig = go.Figure()
    for s in schools:
        d = bt[bt.institution == s].set_index("band").reindex(BL)
        fig.add_bar(x=BL, y=d.per, name=S[s], marker=dict(color=color[s], cornerradius=4), customdata=d.n,
                    hovertemplate=f"{S[s]} %{{x}}: 1인당 %{{y:.1f}}편 (%{{customdata}}명)<extra></extra>")
    fig.update_layout(barmode="group", title=f"학문적 나이(현재, 추정)별 교수 1인당 논문 ({y0}–{y1})")
    ui.plot(fig, height=380, key="bm_band")
    piv = bt.pivot_table(index="institution", columns="band", values="n", fill_value=0).reindex(
        index=schools, columns=BL + ["미상"], fill_value=0).astype(int).rename(index=S)
    st.markdown("**경력 단계별 교수 수** (구성)")
    st.dataframe(piv, width="stretch")
    st.caption("학문적 나이 = 올해 − 박사 연도 (모르면 첫 top 논문+2년으로 추정). 해외 교수는 대부분 추정값입니다.")

    st.subheader("학과 그룹별 (C1 · 국내 학교만)")
    dd = ppl[ppl.dept.notna()]
    if dd.empty:
        st.info("학과 정보가 있는 학교가 없습니다 (국내 교수만 학과 데이터가 있습니다).")
    else:
        dt = dd.groupby(["institution", "dept"]).agg(n=("name", "count"), papers=("papers", "sum")).reset_index()
        dt["per"] = dt.papers / dt.n
        groups = dt.groupby("dept").n.sum().sort_values(ascending=False).index.tolist()
        fig = go.Figure()
        for s in [s for s in schools if s in set(dt.institution)]:
            d = dt[dt.institution == s].set_index("dept").reindex(groups)
            fig.add_bar(x=groups, y=d.per, name=S[s], marker=dict(color=color[s], cornerradius=4), customdata=d.n,
                        hovertemplate=f"{S[s]} %{{x}}: 1인당 %{{y:.1f}}편 (%{{customdata}}명)<extra></extra>")
        fig.update_layout(barmode="group", title="학과 그룹별 교수 1인당 논문")
        ui.plot(fig, height=380, key="bm_dept")
        st.dataframe(dt.pivot_table(index="dept", columns="institution", values="n", fill_value=0)
                     .rename(columns=S).astype(int), width="stretch")
        st.caption("학과는 공개 정보 기반 LLM 추정 (국내 교수만).")

    st.subheader("연구 그룹 — 같은 학교 교수끼리 공저로 묶기 (C3)")
    g1, g2 = st.columns([2, 2])
    gs = g1.selectbox("학교", schools, key="bm_c3s", format_func=lambda s: S[s])
    thr = g2.slider("같은 그룹으로 볼 최소 공저 편수", 1, 10, 3, key="bm_c3t")
    d = raw[raw.institution == gs][["key", "name"]].drop_duplicates()
    pairs = d.merge(d, on="key")
    pairs = pairs[pairs.name_x < pairs.name_y].groupby(["name_x", "name_y"]).size()
    pairs = pairs[pairs >= thr]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a_, b_), _ in pairs.items():
        parent[find(a_)] = find(b_)
    comp = {}
    for n in set(d.name):
        comp.setdefault(find(n), []).append(n)
    gp = d.groupby("name").key.nunique()
    gc = raw[raw.institution == gs].drop_duplicates(["name", "key"])
    grp_rows = []
    for members in comp.values():
        if len(members) < 2:
            continue
        keys = set(d[d.name.isin(members)].key)
        sub = pap[(pap.institution == gs) & pap.key.isin(keys)]
        mem = sorted(members, key=lambda n: -gp.get(n, 0))
        grp_rows.append({"group": ui.disp(mem[0]) + " 그룹", "size": len(members), "papers": len(keys),
                         "per": len(keys) / len(members), "cites": int(sub.cited_by.sum()),
                         "field": sub.field.value_counts().index[0] if len(sub) else "",
                         "members": ", ".join(ui.disp(n) for n in mem[:8]) + (" …" if len(mem) > 8 else "")})
    if grp_rows:
        gt = pd.DataFrame(grp_rows).sort_values("papers", ascending=False)
        solo = len([m for m in comp.values() if len(m) == 1])
        st.caption(f"{S[gs]}: 공저 {thr}편 이상으로 이어진 그룹 {len(gt)}개, 혼자 연구(이 기준) {solo}명. "
                   "그룹 논문은 멤버 누구라도 저자인 논문 (공저 1편).")
        st.dataframe(gt, hide_index=True, width="stretch", column_config={
            "group": "그룹 (대표 = 논문 최다)", "size": st.column_config.NumberColumn("교수", format="%d명"),
            "papers": st.column_config.NumberColumn("논문", format="%d"),
            "per": st.column_config.NumberColumn("1인당", format="%.1f"),
            "cites": st.column_config.NumberColumn("피인용", format="localized"), "field": "주 분야",
            "members": st.column_config.TextColumn("멤버", width="large")})
    else:
        st.info("이 기준으로는 묶이는 그룹이 없습니다. 최소 공저 편수를 낮춰 보세요.")

    st.subheader("핵심 인력 의존도 (C4)")
    rows_ = []
    for s in schools:
        for k in FK:
            d = raw[(raw.institution == s) & (raw.field == k)].groupby("name").key.nunique().sort_values(ascending=False)
            tot = len(pap[(pap.institution == s) & (pap.field == k)])
            if tot == 0:
                continue
            rows_.append({"school": S[s], "field": k, "core": int((d >= 5).sum()),
                          "top3": d.head(3).sum() / max(d.sum(), 1) * 100})
    dep = pd.DataFrame(rows_)
    z = dep.pivot(index="school", columns="field", values="top3").reindex(index=[S[s] for s in schools], columns=FK)
    cz = dep.pivot(index="school", columns="field", values="core").reindex(index=[S[s] for s in schools], columns=FK)
    fig = go.Figure(go.Heatmap(
        z=z.values, x=FK, y=z.index, colorscale=ui.seq_scale(), zmin=0, zmax=100, showscale=False,
        text=[[f"{v:.0f}%<br>{int(n) if pd.notna(n) else 0}명" if pd.notna(v) else "" for v, n in zip(r1, r2)]
              for r1, r2 in zip(z.values, cz.values)], texttemplate="%{text}",
        hovertemplate="%{y} %{x}: 상위 3명 몫 %{z:.0f}%<extra></extra>"))
    fig.update_layout(title="분야 논문(교수 기여 합) 중 상위 3명 몫 · 핵심 교수(5편 이상) 수 — 진할수록 소수 의존",
                      yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=60 * len(schools) + 140, key="bm_dep")

    st.subheader("신진 교수 기여 (C5)")
    young = set(ppl[ppl.academic_age <= 6].name)
    ys = []
    for s in schools:
        d = raw[raw.institution == s]
        yk = d[d.name.isin(young)].groupby("year").key.nunique().reindex(YR, fill_value=0)
        allk = pap[pap.institution == s].groupby("year").key.count().reindex(YR, fill_value=0)
        ys.append((s, yk / allk.replace(0, np.nan) * 100, int(len(young & set(ppl[ppl.institution == s].name)))))
    fig = go.Figure()
    for s, v, n in ys:
        line(fig, v.index, v.values, s, hovertemplate=f"{S[s]} %{{x}}: %{{y:.0f}}% (신진 {n}명)<extra></extra>")
    fig.update_layout(title="학교 논문 중 신진 교수(현재 학문적 나이 6년 이하)가 저자인 비율", hovermode="x unified")
    fig.update_yaxes(ticksuffix="%")
    ui.plot(fig, height=360, key="bm_young")
    st.caption("현재 기준 신진 교수의 과거 논문(박사·포닥 시절, 다른 기관)도 이 학교 교수로 잡힙니다 — 최근 몇 년만 보세요.")

    st.subheader("협업 구조 (C6)")
    ca = _coauth(tuple(schools), y0, y1, nt, TV)
    ctry = fac_all.set_index("institution").country
    cr = []
    for s in schools:
        ks = set(pap[pap.institution == s].key)
        o = ca[ca.key.isin(ks) & (ca.institution != s)]
        home = ctry.get(s)
        foreign = set(o[o.country != home].key)
        dom = set(o[o.country == home].key) - foreign
        cr.append({"school": S[s], "단독(교수 기준)": (len(ks) - len(foreign) - len(dom)) / max(len(ks), 1) * 100,
                   "국내 다른 기관과": len(dom) / max(len(ks), 1) * 100,
                   "해외 기관과": len(foreign) / max(len(ks), 1) * 100})
    cr = pd.DataFrame(cr)
    fig = go.Figure()
    for i, k in enumerate(["단독(교수 기준)", "국내 다른 기관과", "해외 기관과"]):
        fig.add_bar(y=cr.school, x=cr[k], name=k, orientation="h", marker=dict(color=pal[i]),
                    hovertemplate=f"%{{y}} {k}: %{{x:.0f}}%<extra></extra>")
    fig.update_layout(barmode="stack", title="논문의 공저 교수 소속 (CSrankings 교수만 셈 — 학생·산업체 공저자는 모름)",
                      yaxis=dict(autorange="reversed"))
    fig.update_xaxes(ticksuffix="%", range=[0, 100])
    ui.plot(fig, height=50 * len(schools) + 140, key="bm_collab")

# ════════════════════════════ 피인용·영향력 ════════════════════════════
elif view == "피인용·영향력":
    st.caption("피인용 = OpenAlex 현재 값 (DOI 또는 제목으로 찾은 논문만). 최근 논문일수록 작으니 같은 연도끼리 비교하세요.")
    st.caption("피인용 확인 비율: " + " · ".join(f"{S[s]} {cov[s]:.0f}%" for s in schools))
    pc = pap.dropna(subset=["cited_by"]).astype({"cited_by": float})
    pc["pct"] = pc.groupby(["year", "field"]).cited_by.rank(pct=True)
    st.subheader("상위 논문 수 추이 (D1)")
    lvl = st.radio("기준", ["상위 10%", "상위 1%"], horizontal=True, key="bm_d1")
    q = 0.9 if lvl == "상위 10%" else 0.99
    top = pc[pc.pct > q]
    ty = per_year(top).div(NF, axis=1).rolling(3, min_periods=1).mean()
    fig = go.Figure()
    for s in schools:
        line(fig, ty.index, ty[s], s, hovertemplate=f"{S[s]} %{{x}}: 교수 1인당 %{{y:.2f}}편<extra></extra>")
    fig.update_layout(title=f"교수 1인당 {lvl} 논문 (같은 연도·분야의 비교 대학 논문 중, 3년 이동평균)",
                      hovermode="x unified")
    ui.plot(fig, height=380, key="bm_top")
    hi = pd.DataFrame([{"school": S[s], "papers": int((pc.institution == s).sum()),
                        "top10": int(((pc.institution == s) & (pc.pct > 0.9)).sum()),
                        "top1": int(((pc.institution == s) & (pc.pct > 0.99)).sum())} for s in schools])
    hi["top10_share"] = hi.top10 / hi.papers.replace(0, np.nan) * 100
    st.dataframe(hi, hide_index=True, width="stretch", column_config={
        "school": "학교", "papers": st.column_config.NumberColumn("피인용 확인 논문", format="localized"),
        "top10": st.column_config.NumberColumn("상위 10%", format="%d편"),
        "top1": st.column_config.NumberColumn("상위 1%", format="%d편"),
        "top10_share": st.column_config.NumberColumn("상위 10% 비율", format="%.1f%%", help="10%면 평균")})

    st.subheader("연도별 논문당 피인용 중앙값")
    med_y = pc.groupby(["year", "institution"]).cited_by.median().unstack().reindex(index=YR, columns=schools)
    fig = go.Figure()
    for s in schools:
        line(fig, med_y.index, med_y[s], s, hovertemplate=f"{S[s]} %{{x}}년 논문: 중앙값 %{{y:.0f}}회<extra></extra>")
    fig.update_layout(title="출판 연도별 논문당 피인용 중앙값", hovermode="x unified")
    fig.update_yaxes(type="log")
    ui.plot(fig, height=360, key="bm_cite_y")

    st.subheader("분야별 피인용 분포 (D2)")
    df2 = st.selectbox("분야", FK, key="bm_d2", format_func=lambda k: FLAB[k])
    fig = go.Figure()
    for s in schools:
        d = pc[(pc.institution == s) & (pc.field == df2)]
        fig.add_box(x=d.cited_by.clip(lower=0.5), name=S[s], marker_color=color[s], orientation="h",
                    boxpoints="outliers", hovertemplate=f"{S[s]}: %{{x:.0f}}회<extra></extra>")
    fig.update_layout(title=f"{df2} 논문당 피인용 분포 ({y0}–{y1})", showlegend=False)
    fig.update_xaxes(type="log", title="피인용 (로그)")
    ui.plot(fig, height=60 * len(schools) + 120, legend=False, key="bm_box2")

    st.subheader("h-index (D3)")
    H = pd.DataFrame({k: [hindex(pc[(pc.institution == s) & (pc.field == k)].cited_by) for s in schools]
                      for k in FK}, index=[S[s] for s in schools])
    H.insert(0, "전체", [hindex(pc[pc.institution == s].cited_by) for s in schools])
    fig = go.Figure(go.Heatmap(z=H.div(H.max().replace(0, np.nan), axis=1).values, x=list(H.columns), y=H.index,
                               colorscale=ui.seq_scale(), showscale=False, text=H.values, texttemplate="%{text}",
                               hovertemplate="%{y} %{x}: h=%{text}<extra></extra>"))
    fig.update_layout(title=f"기간 내 top 학회 논문만으로 계산한 h-index (열마다 1위 = 가장 진함)",
                      yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=60 * len(schools) + 120, key="bm_h")

    st.subheader("최고 영향력 논문 (D4)")
    ts = st.selectbox("학교", schools, key="bm_d4", format_func=lambda s: S[s])
    tp = pc[pc.institution == ts].sort_values("cited_by", ascending=False).head(20)
    if len(tp):
        meta = ui.con().execute("SELECT key, title FROM dblp_paper WHERE key IN (SELECT unnest(?))",
                                [list(tp.key)]).df().set_index("key").title
        au = raw[(raw.institution == ts) & raw.key.isin(tp.key)].groupby("key").name.apply(
            lambda x: ", ".join(ui.disp(n) for n in sorted(set(x))))
        st.dataframe(pd.DataFrame({"title": tp.key.map(meta), "venue": tp.venue.str.upper(), "year": tp.year,
                                   "cites": tp.cited_by.astype(int), "pct": (tp.pct * 100).round(1),
                                   "who": tp.key.map(au)}), hide_index=True, width="stretch", column_config={
            "title": st.column_config.TextColumn("제목", width="large"), "venue": "학회",
            "year": st.column_config.NumberColumn("연도", format="%d"),
            "cites": st.column_config.NumberColumn("피인용", format="localized"),
            "pct": st.column_config.NumberColumn("같은 연도·분야 백분위", format="%.1f"), "who": f"{S[ts]} 교수"})

    st.subheader("분야별 교수 1인당 피인용")
    st.dataframe(cit_per.rename(index=S).round(0), width="stretch")

# ════════════════════════════ 교수 ════════════════════════════
elif view == "교수":
    pp = raw.groupby(["institution", "name"]).key.nunique().reset_index(name="n")
    st.subheader("교수별 논문 분포")
    fig = go.Figure()
    allv = {}
    for s in schools:
        d = pp[pp.institution == s]
        vals = np.concatenate([d.n.values, np.zeros(max(0, int(nfac.get(s, 0)) - len(d)))])
        allv[s] = vals
        fig.add_box(x=vals + 0.5, name=S[s], marker_color=color[s], boxpoints="outliers", orientation="h",
                    hovertemplate=f"{S[s]}: %{{x:.0f}}편<extra></extra>")
    fig.update_layout(title=f"교수별 논문 수 ({y0}–{y1}, 0편 교수 포함)", showlegend=False)
    fig.update_xaxes(type="log", title="논문 수 (로그, +0.5)")
    ui.plot(fig, height=60 * len(schools) + 120, legend=False, key="bm_box")
    dist = []
    for s in schools:
        v = np.sort(allv[s])[::-1]
        n = len(v)
        dist.append({"school": S[s], "faculty": n, "median": float(np.median(v)) if n else 0,
                     "p75": float(np.percentile(v, 75)) if n else 0, "p90": float(np.percentile(v, 90)) if n else 0,
                     "zero": (v == 0).mean() * 100 if n else 0,
                     "top10": v[:max(1, round(n * 0.1))].sum() / max(v.sum(), 1) * 100,
                     "core": int((v >= 2 * years).sum())})
    st.dataframe(pd.DataFrame(dist), hide_index=True, width="stretch", column_config={
        "school": "학교", "faculty": st.column_config.NumberColumn("교수", format="%d명"),
        "median": st.column_config.NumberColumn("중앙값", format="%.0f"),
        "p75": st.column_config.NumberColumn("상위 25%", format="%.0f"),
        "p90": st.column_config.NumberColumn("상위 10%", format="%.0f"),
        "zero": st.column_config.NumberColumn("논문 0편 교수", format="%.0f%%"),
        "top10": st.column_config.NumberColumn("상위 10% 교수 몫", format="%.0f%%"),
        "core": st.column_config.NumberColumn("연 2편 이상 교수", format="%d명")})

    st.subheader("실적 집중도 (E2 · 로렌츠 곡선)")
    fig = go.Figure()
    for s in schools:
        v = np.sort(allv[s])
        if not len(v) or v.sum() == 0:
            continue
        cx = np.linspace(0, 100, len(v) + 1)
        cy = np.concatenate([[0], np.cumsum(v) / v.sum() * 100])
        gini = 1 - 2 * np.trapezoid(cy / 100, cx / 100)
        fig.add_scatter(x=cx, y=cy, name=f"{S[s]} (지니 {gini:.2f})", mode="lines",
                        line=dict(color=color[s], width=3 if s == base else 2),
                        hovertemplate=f"{S[s]}: 하위 %{{x:.0f}}% 교수가 논문의 %{{y:.0f}}%<extra></extra>")
    fig.add_scatter(x=[0, 100], y=[0, 100], name="완전 균등", mode="lines", line=dict(color=ui.gray(), dash="dot"))
    fig.update_layout(title="교수 실적 분포 — 대각선에서 멀수록 소수에 집중 (지니 0 = 균등, 1 = 한 명)")
    fig.update_xaxes(title="교수 누적 비율 (%, 적게 낸 순)")
    fig.update_yaxes(title="논문 누적 비율 (%)")
    ui.plot(fig, height=440, key="bm_lorenz")

    st.subheader("분야별 대표 교수 (E1)")
    st.caption("분야마다 논문 수 차이가 커서(VC·ML은 많고 TC·DB는 적음) 대표 교수는 분야 안에서만 비교합니다.")
    ppl_age = _people(tuple(schools)).set_index("name").academic_age
    fx = raw.drop_duplicates(["institution", "name", "key"]).groupby(["field", "institution", "name"]).agg(
        papers=("key", "nunique"), cites=("cited_by", "sum")).reset_index()
    nmax = st.slider("학교·분야마다 몇 명", 1, 5, 3, key="bm_e1n")
    mat = {}
    for k in FK:
        row = {}
        for s_ in schools:
            t = fx[(fx.field == k) & (fx.institution == s_)].sort_values(["papers", "cites"], ascending=False)
            row[S[s_]] = " · ".join(f"{ui.disp(r.name)} {r.papers}" for r in t.head(nmax).itertuples()) or "-"
        mat[FLAB[k]] = row
    st.dataframe(pd.DataFrame(mat).T, width="stretch", height=36 * len(FK) + 40,
                 column_config={S[s_]: st.column_config.TextColumn(S[s_], width="large") for s_ in schools})
    st.caption("칸 = 그 학교에서 그 분야 논문이 가장 많은 교수 (이름 뒤 숫자 = 분야 논문 수, 공저 포함).")
    ef = st.selectbox("한 분야 자세히", FK, key="bm_e1", format_func=lambda k: FLAB.get(k, k))
    tp = fx[fx.field == ef].sort_values(["papers", "cites"], ascending=False).head(30).copy()
    tp["school"] = tp.institution.map(S)
    tp["per_paper"] = (tp.cites / tp.papers).round(1)
    tp["acad_age"] = tp.name.map(ppl_age)
    tp.insert(0, "rank", range(1, len(tp) + 1))
    ui.people_table(tp[["rank", "name", "school", "acad_age", "papers", "cites", "per_paper"]].reset_index(drop=True),
                    "bm_e1_tab", B.BF(start=y0, end=y1, next_tier=nt, fields=[ef]),
                    height=min(700, 36 * len(tp) + 40), column_config={
        "rank": st.column_config.NumberColumn("#", width="small"), "school": "학교",
        "acad_age": st.column_config.NumberColumn("학문적 나이 (추정)", format="%d"),
        "papers": st.column_config.NumberColumn(f"{ef} 논문", format="%d"),
        "cites": st.column_config.NumberColumn("피인용", format="localized"),
        "per_paper": st.column_config.NumberColumn("편당", format="%.1f")})

    st.subheader("동년차 비교 (E3)")
    pe = _people(tuple(schools)).merge(pp.rename(columns={"n": "papers"}), on=["institution", "name"], how="left")
    pe["papers"] = pe.papers.fillna(0)
    pe["active_yrs"] = np.clip(pe.academic_age.fillna(years), 1, years)
    pe["per_yr"] = pe.papers / pe.active_yrs
    BAND = [(0, 3), (4, 6), (7, 10), (11, 15), (16, 20), (21, 30), (31, 60)]
    lbl = [f"{a}–{b}년" for a, b in BAND]
    fig = go.Figure()
    for s in schools:
        d = pe[pe.institution == s]
        ys_ = [d[(d.academic_age >= a) & (d.academic_age <= b)].per_yr.median() for a, b in BAND]
        ns_ = [int(((d.academic_age >= a) & (d.academic_age <= b)).sum()) for a, b in BAND]
        line(fig, lbl, ys_, s, customdata=ns_,
             hovertemplate=f"{S[s]} %{{x}}: 연 %{{y:.2f}}편 (%{{customdata}}명)<extra></extra>")
    fig.update_layout(title="학문적 나이 구간별 교수 연평균 논문 중앙값 (같은 경력끼리 비교)", hovermode="x unified")
    fig.update_xaxes(title="학문적 나이 (추정)")
    ui.plot(fig, height=380, key="bm_cohort")

    st.subheader("교수 이동 (E4 · 데이터 있는 사람만)")
    hist = ui.con().execute(
        """SELECT a.person, a.institution, min(a.year) AS y0, max(a.year) AS y1, count(*) AS n
           FROM pii.affiliation a WHERE a.person IN (SELECT unnest(?)) GROUP BY 1, 2""",
        [list(set(raw.name))]).df()
    if hist.empty:
        st.info("소속 이력이 있는 교수가 없습니다.")
    else:
        hist = hist[hist.n >= 2].sort_values(["person", "y0"])
        cur = raw.drop_duplicates("name").set_index("name").institution
        mv = []
        for p_, g in hist.groupby("person"):
            if g.institution.nunique() < 2:
                continue
            mv.append({"school": S.get(cur.get(p_), cur.get(p_)), "name": p_,
                       "path": " → ".join(f"{r.institution} ({r.y0}–{r.y1})" for r in g.itertuples())})
        mv = pd.DataFrame(mv)
        if mv.empty:
            st.info("여러 기관을 거친 기록이 있는 교수가 없습니다.")
        else:
            st.dataframe(ui.with_korean(mv.sort_values(["school", "name"])), hide_index=True, width="stretch",
                         column_config={"school": "현재 학교", "kname": ui.KNAME_COL, "name": "교수",
                                        "path": st.column_config.TextColumn("논문 소속 이력 (OpenAlex)", width="large")})
        st.caption("OpenAlex 논문 소속 이력은 한국인 연구자만 모아 두어서, 해외 교수는 대부분 빠집니다.")

    st.subheader("학교별 상위 10명 (분야 안에서)")
    tf2 = st.selectbox("분야", FK, key="bm_pf", format_func=lambda k: FLAB.get(k, k))
    d = raw[raw.field == tf2]
    tp = d.drop_duplicates(["institution", "name", "key"]).groupby(["institution", "name"]).agg(
        papers=("key", "nunique"), cites=("cited_by", "sum")).reset_index()
    cols = st.columns(min(3, len(schools)))
    for i, s in enumerate(schools):
        t = tp[tp.institution == s].sort_values(["papers", "cites"], ascending=False).head(10)
        with cols[i % len(cols)]:
            st.markdown(f"**{S[s]}** ({int(nfac.get(s, 0))}명)")
            st.dataframe(ui.with_korean(t[["name", "papers", "cites"]]), hide_index=True, width="stretch",
                         column_config={"kname": ui.KNAME_COL, "name": "교수",
                                        "papers": st.column_config.NumberColumn(f"{tf2} 논문", format="%d"),
                                        "cites": st.column_config.NumberColumn("피인용", format="localized")})

# ════════════════════════════ 학회 ════════════════════════════
elif view == "학회":
    vf = st.selectbox("분야", FK, key="bm_vf", format_func=lambda k: FLAB[k])
    d = pap[pap.field == vf]
    vp = d.groupby(["venue", "institution"]).key.count().unstack(fill_value=0).reindex(columns=schools, fill_value=0)
    vp = vp.loc[vp.sum(axis=1).sort_values(ascending=False).index]
    vpf = vp.div(NF, axis=1)
    zn = vpf.div(vpf.max(axis=1).replace(0, np.nan), axis=0)
    fig = go.Figure(go.Heatmap(z=zn.values, x=[S[s] for s in schools], y=[v.upper() for v in vp.index],
                               colorscale=ui.seq_scale(), showscale=False, text=vp.values.astype(int),
                               texttemplate="%{text}", customdata=vpf.values,
                               hovertemplate="%{y} · %{x}: %{text}편 (1인당 %{customdata:.2f})<extra></extra>"))
    fig.update_layout(title=f"{vf} 학회별 논문 수 (색 = 학회마다 교수 1인당 1위 대비)", yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=max(300, 30 * len(vp) + 100), key="bm_venue")

    st.subheader("학회별 점유율 추이 (F1)")
    vs = pap.venue.value_counts().index[:40].tolist()
    vsel = st.selectbox("학회", vs, key="bm_f1", format_func=str.upper)
    vy = per_year(pap[pap.venue == vsel])
    sh = vy.rolling(3, min_periods=1).sum()
    sh = sh.div(sh.sum(axis=1).replace(0, np.nan), axis=0) * 100
    fig = go.Figure()
    for s in schools:
        line(fig, sh.index, sh[s], s, customdata=vy[s],
             hovertemplate=f"{S[s]} %{{x}}: %{{y:.0f}}% (그해 %{{customdata}}편)<extra></extra>")
    fig.update_layout(title=f"{vsel.upper()}: 비교 대학 합계 중 각 학교 비중 (%, 3년 합)", hovermode="x unified")
    fig.update_yaxes(ticksuffix="%")
    ui.plot(fig, height=380, key="bm_vshare")

    st.subheader(f"학회 포트폴리오 차이 (F2) — {S[base]}가 상대적으로 많이/적게 내는 학회")
    vshare = pap.groupby(["institution", "venue"]).key.count().unstack(fill_value=0).reindex(schools).fillna(0)
    vshare = vshare.div(vshare.sum(axis=1).replace(0, np.nan), axis=0) * 100
    diff = (vshare.loc[base] - vshare.loc[peers].median()).sort_values()
    diff = pd.concat([diff.head(10), diff.tail(10)]).drop_duplicates()
    diff = diff[diff.abs() > 0.05]
    fig = go.Figure(go.Bar(
        y=[v.upper() for v in diff.index], x=diff.values, orientation="h",
        marker=dict(color=[("#2a78d6" if v > 0 else "#eb6834") for v in diff.values], cornerradius=4),
        hovertemplate="%{y}: %{x:+.1f}%p<extra></extra>"))
    fig.update_layout(title=f"{S[base]}의 학회 비중 − 비교 대학 중앙값 (%p) — 파랑 = 더 많이 냄")
    fig.update_xaxes(ticksuffix="%p", zeroline=True)
    ui.plot(fig, height=max(320, 26 * len(diff) + 100), key="bm_vdiff")
