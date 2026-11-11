"""University benchmark: one base school (default POSTECH) against chosen peers (default SNU, KAIST, Stanford,
Princeton, U Tokyo) — per-faculty output, field strengths/weaknesses, finer CSrankings areas, trends, faculty depth,
venues, citations and top people."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fp import age
from fp import boards as B
from fp import ui

ui.require_db()
c = ui.con()
B.ensure_pinst(c)
age.create_views(c)  # paper_cites
Y = ui.THIS_YEAR

st.title("대학 벤치마크")
st.caption("기준 학교 하나를 비교 대학들과 나란히 놓고, 교수 1인당 실적과 분야별 강점·약점을 봅니다. "
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


@st.cache_data(ttl=3600, max_entries=8)
def _load(schools: tuple, y0: int, y1: int, nt: bool, tv: float):
    cc = ui._root_con().cursor()
    try:
        B.ensure_pinst(cc)
        age.create_views(cc)
        return cc.execute(
            f"""SELECT x.institution, x.key, x.year, x.name, x.is_last, x.venue, v.area, v.area_title,
                       ({FS}) AS field, pc.cited_by
                FROM pinst x JOIN venue v ON v.venue = x.venue LEFT JOIN paper_cites pc ON pc.key = x.key
                WHERE x.institution IN (SELECT unnest(?)) AND x.year BETWEEN ? AND ? AND (? OR NOT v.next_tier)""",
            [list(schools), y0, y1, nt],
        ).df()
    finally:
        cc.close()


from fp import topvenues  # noqa: E402

raw = _load(tuple(schools), y0, y1, nt, topvenues.version())
if raw.empty:
    st.info("이 조건에 논문이 없습니다.")
    st.stop()
pap = raw.drop_duplicates(["institution", "key"])  # one paper per school
nfac = fac_all.set_index("institution").faculty
pal = ui.series()
color = {s: pal[i % len(pal)] for i, s in enumerate(schools)}
S = {s: ui.short(s) for s in schools}
years = y1 - y0 + 1
FK = [k for k in B.FIELDS if k in set(pap.field)]


def div_scale():
    mid = "#e8e6df" if ui.mode() == "light" else "#3a3935"
    return [[0, "#eb6834"], [0.5, mid], [1, "#2a78d6"]]


# ── overview numbers per school ──
rows = []
for s in schools:
    p = pap[pap.institution == s]
    a = raw[raw.institution == s]
    n = int(nfac.get(s, 0))
    act = a.name.nunique()
    cites = p.cited_by.dropna()
    rows.append({
        "school": s, "faculty": n, "active": act, "papers": len(p),
        "per_fac": len(p) / max(n, 1), "per_fac_yr": len(p) / max(n, 1) / years,
        "cites": int(cites.sum()), "cites_per_fac": cites.sum() / max(n, 1),
        "med_cites": float(cites.median()) if len(cites) else None,
        "pi_share": p.key.isin(a[a.is_last].key).mean() * 100 if len(p) else None,
        "fields": int((p.groupby("field").key.count() >= 5).sum()),
    })
ov = pd.DataFrame(rows)
me = ov.iloc[0]
rk = ov.per_fac.rank(ascending=False, method="min").astype(int)
ui.kpis([
    (f"{S[base]} 교수", f"{int(me.faculty)}명", f"비교 대학 중앙값 {ov.faculty.iloc[1:].median():.0f}명"),
    ("교수 1인당 논문", f"{me.per_fac:.1f}편", f"{len(schools)}개 대학 중 {rk.iloc[0]}위 · {y0}–{y1}"),
    ("교수 1인당 피인용", f"{me.cites_per_fac:,.0f}",
     f"{len(schools)}개 중 {int(ov.cites_per_fac.rank(ascending=False, method='min').iloc[0])}위"),
    ("논문당 피인용 중앙값", f"{me.med_cites:.0f}" if pd.notna(me.med_cites) else "-",
     f"{len(schools)}개 중 {int(ov.med_cites.rank(ascending=False, method='min').iloc[0])}위"),
])

tabs = st.tabs(["요약", "분야별 강점·약점", "세부 분야", "추세", "교수 분포", "학회", "피인용", "대표 교수"])

# ───────── 요약 ─────────
with tabs[0]:
    st.dataframe(ov, hide_index=True, width="stretch", column_config={
        "school": "학교", "faculty": st.column_config.NumberColumn("교수", format="%d명"),
        "active": st.column_config.NumberColumn("논문 있는 교수", format="%d명", help="기간 내 1편 이상"),
        "papers": st.column_config.NumberColumn(f"논문 {y0}–{y1}", format="localized"),
        "per_fac": st.column_config.NumberColumn("교수 1인당", format="%.1f"),
        "per_fac_yr": st.column_config.NumberColumn("1인당 연간", format="%.2f"),
        "cites": st.column_config.NumberColumn("피인용 합", format="localized"),
        "cites_per_fac": st.column_config.NumberColumn("1인당 피인용", format="%.0f"),
        "med_cites": st.column_config.NumberColumn("논문당 피인용 중앙값", format="%.0f"),
        "pi_share": st.column_config.NumberColumn("교신(마지막) 저자 비율", format="%.0f%%",
                                                  help="마지막 저자가 그 학교 교수인 논문 비율 — 주도 연구 비중"),
        "fields": st.column_config.NumberColumn("5편 이상 분야 수", help="연구 폭")})
    g1, g2 = st.columns(2)
    for col_, m, lbl, fmt in [(g1, "per_fac", "교수 1인당 논문", ".1f"), (g2, "cites_per_fac", "교수 1인당 피인용", ",.0f")]:
        fig = go.Figure(go.Bar(
            x=[S[s] for s in ov.school], y=ov[m], marker=dict(color=[color[s] for s in ov.school], cornerradius=4),
            text=[format(v, fmt) for v in ov[m]], textposition="outside",
            hovertemplate="%{x}: %{y:" + fmt + "}<extra></extra>"))
        fig.update_layout(title=f"{lbl} ({y0}–{y1})")
        with col_:
            ui.plot(fig, height=340, key=f"bm_ov_{m}")
    st.caption("교수 수는 지금 CSrankings 명단이라, 과거 연도 논문을 지금 교수 수로 나눈 값입니다. 학교마다 "
               "CSrankings에 들어간 학과 범위가 달라(예: 전기전자·산업공학 포함 여부) 1인당 값에 영향이 있습니다.")

# ───────── 분야별 강점·약점 ─────────
fp_ = pap.groupby(["institution", "field"]).key.count().unstack(fill_value=0).reindex(schools).fillna(0)
fp_ = fp_[[k for k in FK]]
actf = raw.groupby(["institution", "field"]).name.nunique().unstack(fill_value=0).reindex(schools).fillna(0)
actf = actf.reindex(columns=fp_.columns, fill_value=0)
if den == "all":
    per = fp_.div(nfac.reindex(schools).replace(0, np.nan), axis=0)
else:
    per = fp_ / actf.replace(0, np.nan)
cit = raw.drop_duplicates(["institution", "key"]).groupby(["institution", "field"]).cited_by.sum().unstack(
    fill_value=0).reindex(schools).reindex(columns=fp_.columns, fill_value=0).fillna(0)
cit_per = cit.div(nfac.reindex(schools).replace(0, np.nan), axis=0)
share = fp_.div(fp_.sum(axis=1).replace(0, np.nan), axis=0) * 100

with tabs[1]:
    MET = {"per": f"교수 1인당 논문 ({DEN[den]})", "cit": "교수 1인당 피인용", "abs": "논문 수 (절대)",
           "share": "분야 비중 (학교 논문 중 %)"}
    met = st.radio("지표", list(MET), format_func=MET.get, horizontal=True, key="bm_met")
    M = {"per": per, "cit": cit_per, "abs": fp_, "share": share}[met].astype(float)
    peer = M.iloc[1:]
    med = peer.median()
    ratio = (M.iloc[0] / med.replace(0, np.nan))
    rank = M.rank(ascending=False, method="min").iloc[0]
    tb = pd.DataFrame({
        "field": [f"{k} · {B.FIELDS[k][0]}" for k in M.columns], "key": list(M.columns),
        "base": M.iloc[0].values, "peer_med": med.values, "peer_max": peer.max().values,
        "leader": [S[M[k].idxmax()] if M[k].max() > 0 else "" for k in M.columns],
        "ratio": ratio.values, "rank": rank.values.astype(int), "papers": fp_.iloc[0].values.astype(int),
    })
    tb["ratio"] = tb.ratio.astype(float)
    tb["verdict"] = np.select([(tb.ratio >= 1.2).to_numpy(bool), (tb.ratio <= 0.6).to_numpy(bool)], ["강점", "약점"],
                              "비슷")
    tb.loc[tb.peer_med == 0, "verdict"] = "비교 불가"
    tb = tb.sort_values("ratio", ascending=False, na_position="last")
    strong = tb[tb.verdict == "강점"].key.tolist()
    weak = tb[tb.verdict == "약점"].key.tolist()
    st.markdown(f"**{S[base]}의 강점** (비교 대학 중앙값의 1.2배 이상): {', '.join(strong) or '없음'}  \n"
                f"**약점** (0.6배 이하): {', '.join(weak) or '없음'}  — 지표: {MET[met]}")
    lr = np.log2(tb.ratio.clip(lower=1 / 8, upper=8).fillna(1))
    fig = go.Figure(go.Bar(
        y=tb.key, x=lr, orientation="h", marker=dict(color=lr, colorscale=div_scale(), cmin=-2, cmax=2,
                                                     cornerradius=4),
        customdata=np.stack([tb.base, tb.peer_med, tb.ratio.fillna(0)], axis=1),
        hovertemplate="%{y}: " + S[base] + " %{customdata[0]:.2f} vs 중앙값 %{customdata[1]:.2f} "
                      "(×%{customdata[2]:.2f})<extra></extra>"))
    fig.update_layout(title=f"{S[base]} ÷ 비교 대학 중앙값 (log₂, 오른쪽 = 강점)", yaxis=dict(autorange="reversed"))
    fig.update_xaxes(tickvals=[-2, -1, 0, 1, 2], ticktext=["×¼", "×½", "×1", "×2", "×4"], zeroline=True)
    ui.plot(fig, height=max(320, 30 * len(tb) + 80), key="bm_ratio")
    st.dataframe(tb.drop(columns="key"), hide_index=True, width="stretch", column_config={
        "field": "분야", "base": st.column_config.NumberColumn(S[base], format="%.2f"),
        "peer_med": st.column_config.NumberColumn("비교 대학 중앙값", format="%.2f"),
        "peer_max": st.column_config.NumberColumn("비교 대학 최고", format="%.2f"), "leader": "1위 학교",
        "ratio": st.column_config.NumberColumn("중앙값 대비", format="×%.2f"),
        "rank": st.column_config.NumberColumn(f"순위 / {len(schools)}", format="%d위"),
        "papers": st.column_config.NumberColumn(f"{S[base]} 논문", format="%d편"), "verdict": "판정"})
    z = M.T
    zn = z.div(z.max(axis=1).replace(0, np.nan), axis=0)  # each field scaled to its best school
    fig = go.Figure(go.Heatmap(
        z=zn.values, x=[S[s] for s in z.columns], y=list(z.index), colorscale=ui.seq_scale(),
        text=[[f"{v:.1f}" if met != "abs" else f"{int(v)}" for v in row] for row in z.values],
        texttemplate="%{text}", hovertemplate="%{y} · %{x}: %{text}<extra></extra>", showscale=False))
    fig.update_layout(title=f"{MET[met]} — 분야마다 1위 학교 = 가장 진하게", yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=max(340, 30 * len(z) + 100), key="bm_heat")

# ───────── 세부 분야 (CSrankings areas) ─────────
with tabs[2]:
    ap = pap.groupby(["institution", "area"]).key.count().unstack(fill_value=0).reindex(schools).fillna(0)
    title = dict(zip(pap.area, pap.area_title))
    ap = ap.loc[:, ap.sum() >= 5]
    apf = ap.div(nfac.reindex(schools).replace(0, np.nan), axis=0)
    pm = apf.iloc[1:].median()
    t2 = pd.DataFrame({"area": [f"{a} · {title.get(a, '')}" for a in apf.columns],
                       "field": [B.AREA_FIELD.get(a, "기타") for a in apf.columns],
                       "base": apf.iloc[0].values, "peer_med": pm.values,
                       "ratio": (apf.iloc[0] / pm.replace(0, np.nan)).values,
                       "rank": apf.rank(ascending=False, method="min").iloc[0].astype(int).values,
                       "papers": ap.iloc[0].astype(int).values,
                       "leader": [S[apf[a].idxmax()] for a in apf.columns]})
    t2 = t2.sort_values("ratio", ascending=False, na_position="last")
    st.caption("CSrankings 세부 분야 (기간 내 합계 5편 이상인 것만), 교수 1인당 논문. 중앙값 대비 ×1 = 비교 대학 수준.")
    st.dataframe(t2, hide_index=True, width="stretch", height=min(700, 36 * len(t2) + 40), column_config={
        "area": "세부 분야", "field": "분야", "base": st.column_config.NumberColumn(S[base], format="%.3f"),
        "peer_med": st.column_config.NumberColumn("비교 중앙값", format="%.3f"),
        "ratio": st.column_config.ProgressColumn("중앙값 대비", format="×%.2f", min_value=0,
                                                 max_value=float(max(2.0, t2.ratio.max() or 2))),
        "rank": st.column_config.NumberColumn(f"순위 / {len(schools)}", format="%d위"),
        "papers": st.column_config.NumberColumn(f"{S[base]} 논문", format="%d편"), "leader": "1위"})

# ───────── 추세 ─────────
with tabs[3]:
    f1, f2 = st.columns([2, 2])
    tf = f1.selectbox("분야", ["전체"] + FK, key="bm_tf",
                      format_func=lambda k: k if k == "전체" else f"{k} · {B.FIELDS[k][0]}")
    sm = f2.radio("표시", ["연도별", "3년 이동평균"], horizontal=True, key="bm_sm", index=1)
    d = pap if tf == "전체" else pap[pap.field == tf]
    yr = d.groupby(["year", "institution"]).key.count().unstack(fill_value=0).reindex(
        index=range(y0, y1 + 1), columns=schools, fill_value=0)
    pf = yr.div(nfac.reindex(schools).replace(0, np.nan), axis=1)
    if sm != "연도별":
        pf = pf.rolling(3, min_periods=1).mean()
    fig = go.Figure()
    for s in schools:
        fig.add_scatter(x=pf.index, y=pf[s], name=S[s], mode="lines+markers",
                        line=dict(color=color[s], width=3 if s == base else 2), marker=dict(size=8 if s == base else 6),
                        hovertemplate=f"{S[s]} %{{x}}: %{{y:.2f}}편/교수<extra></extra>")
    fig.update_layout(title=f"교수 1인당 논문 — {tf} ({sm})", hovermode="x unified")
    ui.plot(fig, height=400, key="bm_trend")
    gap = pf[base] / pf[peers].median(axis=1).replace(0, np.nan)
    rk_y = pf.rank(axis=1, ascending=False, method="min")[base]
    fig = go.Figure()
    fig.add_scatter(x=gap.index, y=gap.values, mode="lines+markers", name="중앙값 대비",
                    line=dict(color=color[base], width=2), marker=dict(size=8),
                    customdata=rk_y.values, hovertemplate="%{x}: ×%{y:.2f} (순위 %{customdata:.0f})<extra></extra>")
    fig.add_hline(y=1, line=dict(color=ui.gray(), dash="dot"))
    fig.update_layout(title=f"{S[base]} ÷ 비교 대학 중앙값 — 1 위로 올라가면 따라잡는 중")
    ui.plot(fig, height=320, key="bm_gap")
    mid = (y0 + y1) // 2
    h = []
    for k in FK:
        dk = pap[pap.field == k]
        a_ = dk[dk.year <= mid].groupby("institution").key.count().reindex(schools, fill_value=0)
        b_ = dk[dk.year > mid].groupby("institution").key.count().reindex(schools, fill_value=0)
        pa, pb = a_ / nfac.reindex(schools), b_ / nfac.reindex(schools)
        ra = pa.iloc[0] / pa.iloc[1:].median() if pa.iloc[1:].median() > 0 else np.nan
        rb = pb.iloc[0] / pb.iloc[1:].median() if pb.iloc[1:].median() > 0 else np.nan
        h.append({"field": k, "first": ra, "second": rb, "change": rb - ra,
                  "growth": (b_.iloc[0] - a_.iloc[0]) / max(a_.iloc[0], 1) * 100})
    h = pd.DataFrame(h).sort_values("change", ascending=False)
    st.markdown(f"**전반 {y0}–{mid} → 후반 {mid + 1}–{y1}: 분야별로 격차가 좁혀졌나** (중앙값 대비 배수)")
    st.dataframe(h, hide_index=True, width="stretch", column_config={
        "field": "분야", "first": st.column_config.NumberColumn("전반", format="×%.2f"),
        "second": st.column_config.NumberColumn("후반", format="×%.2f"),
        "change": st.column_config.NumberColumn("변화", format="%+.2f", help="+ = 비교 대학 대비 좋아짐"),
        "growth": st.column_config.NumberColumn(f"{S[base]} 논문 증가율", format="%+.0f%%")})

# ───────── 교수 분포 ─────────
with tabs[4]:
    pp = raw.groupby(["institution", "name"]).key.nunique().reset_index(name="n")
    fig = go.Figure()
    for s in schools:
        d = pp[pp.institution == s]
        zeros = max(0, int(nfac.get(s, 0)) - len(d))
        vals = np.concatenate([d.n.values, np.zeros(zeros)])
        fig.add_box(x=vals, name=S[s], marker_color=color[s], boxpoints="outliers", orientation="h",
                    hovertemplate=f"{S[s]}: %{{x}}편<extra></extra>")
    fig.update_layout(title=f"교수별 논문 수 분포 ({y0}–{y1}, 논문 0편 교수 포함)", showlegend=False)
    fig.update_xaxes(type="log", title="논문 수 (로그)")
    ui.plot(fig, height=60 * len(schools) + 120, key="bm_box")
    dist = []
    for s in schools:
        d = pp[pp.institution == s].n.sort_values(ascending=False)
        n = int(nfac.get(s, 0))
        tot = int(pap[pap.institution == s].key.count())
        allv = np.concatenate([d.values, np.zeros(max(0, n - len(d)))])
        dist.append({"school": s, "faculty": n, "median": float(np.median(allv)) if len(allv) else 0,
                     "p75": float(np.percentile(allv, 75)) if len(allv) else 0,
                     "p90": float(np.percentile(allv, 90)) if len(allv) else 0,
                     "zero": (n - len(d)) / max(n, 1) * 100,
                     "top10": d.head(max(1, round(n * 0.1))).sum() / max(d.sum(), 1) * 100,
                     "core": int((d >= 2 * years).sum()), "tot": tot})
    st.dataframe(pd.DataFrame(dist), hide_index=True, width="stretch", column_config={
        "school": "학교", "faculty": st.column_config.NumberColumn("교수", format="%d명"),
        "median": st.column_config.NumberColumn("교수 논문 중앙값", format="%.0f"),
        "p75": st.column_config.NumberColumn("상위 25%", format="%.0f"),
        "p90": st.column_config.NumberColumn("상위 10%", format="%.0f"),
        "zero": st.column_config.NumberColumn("논문 0편 교수", format="%.0f%%"),
        "top10": st.column_config.NumberColumn("상위 10% 교수 몫", format="%.0f%%",
                                               help="교수 논문 합 중 상위 10% 교수 비중 — 높으면 소수 의존"),
        "core": st.column_config.NumberColumn("연 2편 이상 교수", format="%d명"), "tot": None})
    st.markdown("**분야별 논문 있는 교수 수** (그 분야 인력 규모)")
    st.dataframe(actf.rename(index=S).astype(int), width="stretch")

# ───────── 학회 ─────────
with tabs[5]:
    vf = st.selectbox("분야", FK, key="bm_vf", format_func=lambda k: f"{k} · {B.FIELDS[k][0]}")
    d = pap[pap.field == vf]
    vp = d.groupby(["venue", "institution"]).key.count().unstack(fill_value=0).reindex(columns=schools, fill_value=0)
    vp = vp.loc[vp.sum(axis=1).sort_values(ascending=False).index]
    vpf = vp.div(nfac.reindex(schools).replace(0, np.nan), axis=1)
    zn = vpf.div(vpf.max(axis=1).replace(0, np.nan), axis=0)
    fig = go.Figure(go.Heatmap(z=zn.values, x=[S[s] for s in schools], y=[v.upper() for v in vp.index],
                               colorscale=ui.seq_scale(), showscale=False,
                               text=vp.values.astype(int), texttemplate="%{text}",
                               customdata=vpf.values,
                               hovertemplate="%{y} · %{x}: %{text}편 (1인당 %{customdata:.2f})<extra></extra>"))
    fig.update_layout(title=f"{vf} 학회별 논문 수 (색 = 학회마다 교수 1인당 1위 대비)", yaxis=dict(autorange="reversed"))
    ui.plot(fig, height=max(300, 30 * len(vp) + 100), key="bm_venue")

# ───────── 피인용 ─────────
with tabs[6]:
    st.caption("피인용 = OpenAlex 현재 값 (DOI 또는 제목으로 찾은 논문만). 최근 논문일수록 작으니 같은 연도끼리 비교하세요.")
    pc = pap.dropna(subset=["cited_by"])
    med_y = pc.groupby(["year", "institution"]).cited_by.median().unstack().reindex(
        index=range(y0, y1 + 1), columns=schools)
    fig = go.Figure()
    for s in schools:
        fig.add_scatter(x=med_y.index, y=med_y[s], name=S[s], mode="lines+markers",
                        line=dict(color=color[s], width=3 if s == base else 2),
                        hovertemplate=f"{S[s]} %{{x}}년 논문: 중앙값 %{{y:.0f}}회<extra></extra>")
    fig.update_layout(title="출판 연도별 논문당 피인용 중앙값", hovermode="x unified")
    fig.update_yaxes(type="log")
    ui.plot(fig, height=380, key="bm_cite_y")
    hi = []
    for s in schools:
        d = pc[pc.institution == s]
        # top-10% papers of all compared schools, per publication year (field-agnostic impact)
        thr = pc.groupby("year").cited_by.quantile(0.9)
        top = (d.cited_by >= d.year.map(thr)).sum()
        hi.append({"school": s, "papers": len(d), "top10": int(top), "top10_share": top / max(len(d), 1) * 100,
                   "top10_per_fac": top / max(int(nfac.get(s, 0)), 1),
                   "best": int(d.cited_by.max()) if len(d) else None})
    st.dataframe(pd.DataFrame(hi), hide_index=True, width="stretch", column_config={
        "school": "학교", "papers": st.column_config.NumberColumn("피인용 확인된 논문", format="localized"),
        "top10": st.column_config.NumberColumn("상위 10% 논문", format="%d편",
                                               help="비교 대학 전체 논문 중 같은 출판 연도 피인용 상위 10%"),
        "top10_share": st.column_config.NumberColumn("상위 10% 비율", format="%.1f%%"),
        "top10_per_fac": st.column_config.NumberColumn("교수 1인당 상위 10% 논문", format="%.2f"),
        "best": st.column_config.NumberColumn("최다 피인용", format="localized")})
    st.markdown("**분야별 교수 1인당 피인용**")
    st.dataframe(cit_per.rename(index=S).round(0), width="stretch")

# ───────── 대표 교수 ─────────
with tabs[7]:
    tf2 = st.selectbox("분야", ["전체"] + FK, key="bm_pf",
                       format_func=lambda k: k if k == "전체" else f"{k} · {B.FIELDS[k][0]}")
    d = raw if tf2 == "전체" else raw[raw.field == tf2]
    tp = d.groupby(["institution", "name"]).agg(papers=("key", "nunique"), cites=("cited_by", "sum")).reset_index()
    cols = st.columns(min(3, len(schools)))
    for i, s in enumerate(schools):
        t = tp[tp.institution == s].sort_values(["papers", "cites"], ascending=False).head(10)
        with cols[i % len(cols)]:
            st.markdown(f"**{S[s]}** ({int(nfac.get(s, 0))}명)")
            t = t.assign(name=t.name.map(ui.disp))
            st.dataframe(t[["name", "papers", "cites"]], hide_index=True, width="stretch", column_config={
                "name": "교수", "papers": st.column_config.NumberColumn("논문", format="%d"),
                "cites": st.column_config.NumberColumn("피인용", format="localized")})
