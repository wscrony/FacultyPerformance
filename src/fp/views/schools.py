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

inst, total = B.institution_share(c, f, 100000)
scope = '+'.join(f.fields) if f.fields else ('선택 분야' if (f.areas or f.venues) else '전 분야')
basis = (f"{f.start}–{f.end}, {scope}, {'확장 학회 포함' if f.next_tier else 'top 학회만'}. 논문 = 그 기관 현재 교수"
         "(CSrankings)가 저자로 참여한 고유 논문 수. 교수 = 이 조건에서 논문이 1편 이상인 교수만 (학생·연구원 제외).")

ui.top_venue_status()

# ── KR institution ranking ──
st.subheader("국내 기관 순위")
st.caption("검색·비교 페이지의 '학교 비교'는 이 표로 합쳤습니다 (피인용 포함).")
kr_tab = inst[inst.country == "kr"].sort_values("papers", ascending=False).copy()
from fp import metrics as _M  # citations (merged in from the former 검색·비교 → 학교 비교 tab)

_sc = _M.school_citations(c, ui._as_filter(f)).set_index("institution")
kr_tab["cites"] = kr_tab.institution.map(_sc.cites).fillna(0).astype(int)
kr_tab["cites_per_faculty"] = (kr_tab.cites / kr_tab.faculty.where(kr_tab.faculty > 0)).round(0)
kr_tab["cite_cov"] = (100 * kr_tab.institution.map(_sc.cited_papers) / kr_tab.institution.map(_sc.papers)).round(0)
pick_i = ui.clickable_table(
    kr_tab[["institution", "rank", "papers", "faculty", "per_faculty", "rank_norm",
            "cites", "cites_per_faculty", "cite_cov"]].reset_index(drop=True),
    "s_rank_tab", "institution", link_label="기관", link_help="클릭하면 교수 수에 포함된 교수 명단이 아래에 열립니다",
    height=min(460, 36 * len(kr_tab) + 40), extra_links=["faculty"],
    column_config={
        "rank": st.column_config.NumberColumn("세계 순위 (논문 수)", format="%d위"),
        "papers": st.column_config.ProgressColumn("논문", format="%d", min_value=0,
                                                  max_value=int(kr_tab.papers.max() or 1)),
        "faculty": st.column_config.NumberColumn("교수 ↗", format="%d명",
                                                 help="클릭하면 이 숫자에 들어간 교수 전원의 이름·학과·논문 수가 아래에 나옵니다"),
        "per_faculty": st.column_config.NumberColumn("교수 1인당 논문", format="%.1f"),
        "rank_norm": st.column_config.NumberColumn(
            "세계 순위 (1인당)", format="%d위",
            help=f"교수 {B.MIN_FACULTY_NORM}명 이상 기관 중 1인당 논문 수 순위 (미만이면 빈칸)"),
        "cites": st.column_config.NumberColumn(
            "피인용", format="localized",
            help="이 기간·분야에서 그 기관 교수들이 쓴 고유 논문의 OpenAlex 피인용 합 (공저 논문은 한 번만)"),
        "cites_per_faculty": st.column_config.NumberColumn("교수 1인당 피인용", format="localized"),
        "cite_cov": st.column_config.NumberColumn(
            "피인용 확보율", format="%d%%",
            help="피인용 값을 찾은 논문 비율. DOI 없는 논문(NeurIPS 등)은 제목 매칭이 진행 중이라 낮을 수 있음"),
    },
)
st.caption(basis + f" 1인당 순위는 이 조건의 교수가 {B.MIN_FACULTY_NORM}명 이상인 전 세계 기관끼리 비교합니다. "
           "파란 기관명이나 교수 수를 클릭하면 그 숫자에 포함된 교수 전원의 명단이 아래에 열립니다.")
if pick_i is not None:
    pick = kr_tab.iloc[pick_i]
    st.markdown(f"▼ **{pick.institution}** 선택됨 — 교수 명단 (다른 기관을 클릭하면 바뀝니다)")
    fl = B.institution_faculty(c, pick.institution, f)
    with st.container(border=True):
        st.markdown(f"**{pick.institution}** · 교수 {len(fl)}명 · 논문 {int(pick.papers):,}편 ({scope}, {f.start}–{f.end})")
        if summ := B.institution_dept_summary(fl):
            st.markdown(f"소속 학과 (추정): {summ}")
            st.caption("CSrankings는 학과가 아니라 사람 단위라서, 컴퓨터공학과 외에 전기·전자, AI 대학원, 산업공학, "
                       "디자인 등 top CS 학회에 논문을 내는 교수가 모두 '교수' 수에 들어갑니다. 학과는 LLM 지식 기반 추정입니다.")
        ui.people_table(
            ui.add_age(fl, after="name"), "s_rank_fac", f, height=min(520, 36 * len(fl) + 40),
            column_config={
                "no": st.column_config.NumberColumn("#", width="small"), "name": "교수",
                "papers": st.column_config.ProgressColumn("논문", format="%d", min_value=0,
                                                          max_value=int(fl.papers.max() or 1)),
                "main_field": "주 분야", "main_papers": "주 분야 논문", "by_field": "분야별 논문",
                "dept_group": "학과 구분 (추정)", "department": "학과 (추정)",
            },
        )
        st.caption("교수 = CSrankings 명단의 현재 소속 기준, 이 조건에서 논문이 1편 이상인 사람. 개인 논문 수의 합은 "
                   "교수끼리의 공저 때문에 기관 논문 수보다 클 수 있습니다.")

# ── school profile ──
st.subheader("학교 프로필")
school = st.selectbox("학교", schools, key="s_school")
prof = B.school_profile(c, school, f)
active = prof[prof.papers_career > 0]
row = inst[inst.institution == school]
ages = active.academic_age.dropna()
rn = row.rank_norm.iloc[0] if len(row) else None
ui.kpis([
    (f"교수 · {scope}", f"{int(row.faculty.iloc[0]) if len(row) else 0}명", "이 조건에서 논문이 1편 이상인 CSrankings 교수"),
    (f"논문 {f.start}–{f.end}", f"{int(row.papers.iloc[0]) if len(row) else 0:,}편", "교수 참여 고유 논문"),
    (f"세계 순위 · {scope}", f"{int(row['rank'].iloc[0])}위" if len(row) else "-", basis + " 동률은 같은 순위."),
    ("세계 순위 · 교수 1인당", f"{int(rn)}위" if rn is not None and pd.notna(rn) else "-",
     (f"{float(row.per_faculty.iloc[0]):.1f}편/인. " if len(row) else "") + basis),
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
kr = B.kr_faculty_field_rank(c, f).set_index(["name", "field"])
info = active.set_index("name")[["academic_age", "age_source", "papers_window", "main_area"]]
fields_here = [k for k in B.FIELDS if k in set(ff[ff.papers_window > 0].field)]
tabs = st.tabs(["전체"] + [f"{k} ({int((ff[(ff.field == k)].papers_window > 0).sum())})" for k in fields_here])


def with_kr_rank(d, field: str):
    r = kr.xs(field, level="field")[["kr_rank", "kr_n"]] if field in kr.index.get_level_values("field") else None
    d = d.join(r, on="name") if r is not None else d.assign(kr_rank=None, kr_n=None)
    return d.assign(kr_rank=[f"{int(a)}위 / {int(n)}명" if pd.notna(a) else "" for a, n in zip(d.kr_rank, d.kr_n)])


def fac_table(df, label: str, key: str, field: str):
    mx = int(df.papers.max() or 1) if len(df) else 1
    ui.people_table(
        ui.add_age(df.drop(columns=["kr_n"], errors="ignore"), after="academic_age"), key, f,
        height=min(420, 36 * len(df) + 40),
        column_config={
            "rank": st.column_config.NumberColumn("교내 순위", width="small"), "name": "이름",
            "kr_rank": st.column_config.TextColumn(f"국내 순위 ({field})",
                                                   help="국내 대학 교수(CSrankings) 전체 중 같은 기간·분야 논문 수 순위"),
            "papers": st.column_config.ProgressColumn(label, format="%d", min_value=0, max_value=mx),
            "papers_window": st.column_config.NumberColumn(f"전체 분야 {f.start}–{f.end}"),
            "academic_age": st.column_config.NumberColumn("학문적 나이"), "age_source": "나이 출처",
            "main_area": "주 분야",
        },
    )


# main field = field with most papers in the window (ties → taxonomy order)
fw = ff[ff.papers_window > 0].copy()
fw["fo"] = fw.field.map({k: n for n, k in enumerate(B.FIELDS)})
main_all = fw.sort_values(["name", "papers_window", "fo"], ascending=[True, False, True]).drop_duplicates("name")
info["main_area"] = info.index.map(main_all.set_index("name").field)

with tabs[0]:
    main = main_all
    main = main.rename(columns={"field": "main_field", "papers_window": "papers"})[["name", "main_field", "papers"]]
    main["rank"] = main.groupby("main_field").papers.rank(method="min", ascending=False).astype(int)
    main = main.join(kr[["kr_rank", "kr_n"]], on=["name", "main_field"])
    main["kr_rank"] = [f"{int(a)}위 / {int(n)}명" if pd.notna(a) else "" for a, n in zip(main.kr_rank, main.kr_n)]
    main = main.join(info[["papers_window", "academic_age", "age_source"]], on="name")
    main["fo"] = main.main_field.map({k: n for n, k in enumerate(B.FIELDS)})
    main = main.sort_values(["fo", "rank"])
    mx = int(main.papers.max() or 1) if len(main) else 1
    st.caption(f"주 분야 = {f.start}–{f.end} 논문이 가장 많은 분야. 순위는 그 분야 논문 수 기준.")
    ui.people_table(
        ui.add_age(main[["main_field", "kr_rank", "rank", "name", "papers", "papers_window", "academic_age",
                         "age_source"]], after="academic_age"),
        "s_fac_all", f, height=420,
        column_config={
            "main_field": "주 분야",
            "kr_rank": st.column_config.TextColumn("분야 내 국내 순위",
                                                   help="국내 대학 교수(CSrankings) 중 같은 분야·기간 논문 수 순위"),
            "rank": st.column_config.NumberColumn("분야 내 교내 순위", width="small"), "name": "이름",
            "papers": st.column_config.ProgressColumn(f"주 분야 논문 {f.start}–{f.end}", format="%d",
                                                      min_value=0, max_value=mx),
            "papers_window": st.column_config.NumberColumn("전체 분야 논문"),
            "academic_age": st.column_config.NumberColumn("학문적 나이"), "age_source": "나이 출처",
        },
    )
for t, k in zip(tabs[1:], fields_here):
    with t:
        d = ff[(ff.field == k) & (ff.papers_window > 0)].sort_values("papers_window", ascending=False)
        d = d.rename(columns={"papers_window": "papers"}).drop(columns=["papers_career"]).join(info, on="name")
        d = with_kr_rank(d.assign(rank=range(1, len(d) + 1)), k)
        st.caption(f"{k} · {B.FIELDS[k][0]} — 이 분야 논문이 {f.start}–{f.end}에 1편 이상인 교수, 이 분야 논문 수 순")
        fac_table(d[["rank", "kr_rank", "name", "papers", "papers_window", "main_area", "academic_age", "age_source"]],
                  f"{k} 논문 {f.start}–{f.end}", f"s_fac_{k}", k)

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
