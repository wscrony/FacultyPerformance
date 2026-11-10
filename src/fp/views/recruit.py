"""Faculty recruiting: per department field, Korean researchers not yet on a KR faculty (abroad faculty, postdocs,
PhD students near graduation, industry, researchers), ranked on recent output in that field."""

import pandas as pd
import streamlit as st

from fp import age, metrics
from fp import boards as B
from fp import ui

ui.require_db()
c = ui.con()
Y = ui.THIS_YEAR
st.title("교수 초빙 후보")
st.caption("국내 대학 교수(CSrankings 명단)가 아닌 한국인 연구자 — 해외 교수·포닥·졸업 임박 박사과정·산업체·연구원 — 를 "
           "분야별로 최근 실적 순으로 보여줍니다. 한국인 여부·경력 단계·나이는 모두 추정입니다.")
ui.top_venue_status()

TYPES = ["해외 교수", age.POSTDOC, age.STUDENT, age.INDUSTRY, age.SENIOR, age.KR_PROF, "미상"]
a1, a2, a3 = st.columns([2.2, 2.2, 3])
y0, y1 = a1.slider("실적 기간", 2000, Y, (Y - 4, Y), key="rc_years", help="기본: 최근 5년")
amin, amax = a2.slider("학문적 나이 (PhD 후 연차)", -6, 30, (-2, 10), key="rc_age",
                       help="음수 = 아직 PhD 전 (박사과정). 기본 −2~10: 졸업 임박 박사과정 ~ 조교수급")
types = a3.multiselect("후보 유형", TYPES, default=[t for t in TYPES if t not in (age.SENIOR, age.KR_PROF, "미상")],
                       key="rc_types", help=f"{age.KR_PROF} = CSrankings 명단에는 없지만 웹 확인 결과 이미 국내 대학 교수인 사람")
b1, b2, b3, b4 = st.columns([1.6, 1.6, 2.2, 1.6])
min_p = b1.number_input("분야 논문 최소", 1, 50, 3, key="rc_min")
loc = b2.radio("현재 위치", ["전체", "해외", "국내"], horizontal=True, key="rc_loc")
SORTS = {"field": "분야 논문", "per_year": "분야 연평균 (활동 연수 기준)", "first": "1저자 논문", "cites": "피인용",
         "recent": "최근 2년 분야 논문"}
sort = b3.selectbox("정렬", list(SORTS), format_func=SORTS.get, key="rc_sort")
nt = b4.toggle("확장 학회 포함", value=False, key="rc_nt")

@st.cache_data(ttl=3600, max_entries=6, show_spinner="후보 계산 중…")
def _compute(y0: int, y1: int, amin: int, amax: int, nt: bool, min_p: int, tv: float):
    """All candidates per field (before the type/location filters). Cached: a click only reruns the page."""
    from fp import age as _age

    c = ui._root_con().cursor()
    _age.create_views(c)
    F = metrics.Filter(start=y0, end=y1, next_tier=nt, country=None)
    rows, cols = _age.candidates(c, F, age_min=amin, age_max=amax, scope="korean", limit=None)
    cand = pd.DataFrame(rows, columns=cols)
    cand = cand[cand.role != "KR faculty"]
    st_ = c.execute("SELECT person, stage, stage_source, pos_title, pos_inst, pos_since, pos_url FROM person_stage"
                    ).df().set_index("person")
    stage = st_.stage
    cand["type"] = ["해외 교수" if r == "faculty abroad" or stage.get(n) == _age.ABROAD_PROF else (stage.get(n) or "미상")
                    for n, r in zip(cand.name, cand.role)]
    web = st_[st_.stage_source == "web"]
    vmap = {n: " · ".join(str(x) for x in (r.pos_title, r.pos_inst) if pd.notna(x) and x)
            + (f" ({int(r.pos_since)}~)" if pd.notna(r.pos_since) else "") for n, r in web.iterrows()}
    umap = {n: u for n, u in web.pos_url.items() if pd.notna(u)}
    cand["verified"] = [vmap.get(n, "") for n in cand.name]
    cand["pos_url"] = [umap.get(n, "") for n in cand.name]
    cand["where"] = cand.role.map(lambda r: "해외" if "abroad" in r else ("국내" if "KR" in r else "미상"))
    bf = B.BF(start=y0, end=y1, next_tier=nt)
    pf = B.person_field_counts(c, list(cand.name), bf)
    rc = B.person_field_counts(c, list(cand.name), B.BF(start=max(y0, y1 - 1), end=y1, next_tier=nt))
    rc = rc.set_index(["name", "field"]).papers
    main = pf.sort_values(["papers", "field"], ascending=[False, True]).drop_duplicates("name").set_index("name").field
    krp = B.kr_faculty_field_rank(c, bf)
    krp = krp[krp.field != "전체"]
    out = {}
    for k in B.FIELDS:
        d = pf[(pf.field == k) & (pf.papers >= min_p)][["name", "papers"]].rename(columns={"papers": "field"})
        d = d.merge(cand, on="name")
        if d.empty:
            continue
        d["recent"] = [int(rc.get((n, k), 0)) for n in d.name]
        d["active"] = (d.acad_age.fillna(0).clip(lower=-4) + 5).clip(lower=1, upper=y1 - y0 + 1)
        d["per_year"] = (d.field / d.active).round(2)
        d["first"] = d.first_au
        d["main"] = d.name.map(main)
        kp = krp[krp.field == k].papers
        d["kr_equiv"] = [f"{1 + int((kp > n).sum())}위 / {len(kp)}명" for n in d.field]
        d["kr_pct"] = [max(1, round(100 * (kp >= n).mean())) if len(kp) else None for n in d.field]
        out[k] = d
    c.close()
    return cand, out, krp


from fp import topvenues  # noqa: E402

cand, per_all, krp_all = _compute(y0, y1, amin, amax, nt, int(min_p), topvenues.version())
cand = cand[cand.type.isin(types)]
if loc != "전체":
    cand = cand[cand["where"] == loc]
if cand.empty:
    st.info("조건에 맞는 후보가 없습니다.")
    st.stop()
per_field = {}
for k, d in per_all.items():
    d = d[d.name.isin(cand.name)]
    if len(d):
        # deterministic order (name breaks ties) so a clicked row stays the same person
        per_field[k] = d.sort_values([sort, "field", "name"], ascending=[False, False, True],
                                     kind="mergesort").reset_index(drop=True)

# ── overview ──
ov = pd.DataFrame([{"field": k, "n": int((d["main"] == k).sum()), "n_any": len(d),
                    **{t: int(((d.type == t) & (d["main"] == k)).sum()) for t in types},
                    "top": ", ".join(d[d["main"] == k].name.head(3))} for k, d in per_field.items()])
ui.kpis([
    ("후보 (중복 제외)", f"{cand[cand.name.isin(pd.concat(per_field.values()).name)].name.nunique():,}명",
     f"분야 논문 {min_p}편 이상인 사람만"),
    ("해외 교수", f"{int((cand.type == '해외 교수').sum())}명", None),
    ("포닥 (추정)", f"{int((cand.type == age.POSTDOC).sum())}명", None),
    ("박사과정 (추정)", f"{int((cand.type == age.STUDENT).sum())}명", None),
])
tabs = st.tabs(["요약", "🔎 이름으로 찾기"] + [f"{k}" for k in per_field])
with tabs[0]:
    st.dataframe(ov, hide_index=True, width="stretch", column_config={
        "field": "분야", "n": st.column_config.NumberColumn("후보 (주 분야)", format="%d명"),
        "n_any": st.column_config.NumberColumn("이 분야 논문도 있는 사람 포함", format="%d명"),
        "top": f"상위 3명 ({SORTS[sort]} 순)", **{t: st.column_config.NumberColumn(t, format="%d") for t in types}})
    st.caption("한 사람이 여러 분야에 논문이 있으면 각 분야에 모두 나옵니다 ('주 분야' 열로 구분). 분야 탭에서 이름을 누르면 "
               "논문 목록·피인용·나이 근거가 열립니다.")



def _norm(x: str) -> str:
    import re

    return re.sub(r"[^a-z]", "", re.sub(r"\s+\d{4}$", "", x.lower()))


def _search(q: str) -> pd.DataFrame:
    """DBLP people whose name (without the DBLP homonym number) contains the query, ignoring case/space/hyphen.
    One row per DBLP person: same base name with different numbers = different people (homonyms)."""
    return c.execute(
        r"""WITH m AS (SELECT DISTINCT name FROM author_pub
                      WHERE regexp_replace(lower(regexp_replace(name, '\s+\d{4}$', '')), '[^a-z]', '', 'g')
                            LIKE '%' || ? || '%'),
           pub AS (SELECT a.name, min(a.year) AS y0, max(a.year) AS y1, sum(a.count) AS papers_all
                   FROM author_pub a JOIN venue v USING (venue) WHERE a.name IN (SELECT name FROM m)
                   GROUP BY 1),
           last AS (SELECT name, arg_max(p.title || ' (' || p.venue || ' ' || p.year || ')', p.year) AS last_paper
                    FROM dblp_authorship a JOIN dblp_paper p USING (key) WHERE a.name IN (SELECT name FROM m)
                    GROUP BY 1)
           SELECT m.name, regexp_replace(m.name, '\s+\d{4}$', '') AS base, r.role, r.is_korean,
                  coalesce(r.affiliation, s.pos_inst, r.affil_raw) AS affiliation, r.affil_year,
                  s.stage, s.stage_source, s.pos_title, pa.academic_age, pub.y0, pub.y1, pub.papers_all, last.last_paper
           FROM m LEFT JOIN pub USING (name) LEFT JOIN last USING (name)
           LEFT JOIN person_role r ON r.person = m.name LEFT JOIN person_stage s ON s.person = m.name
           LEFT JOIN person_age pa ON pa.person = m.name
           ORDER BY pub.papers_all DESC NULLS LAST LIMIT 40""",
        [_norm(q)],
    ).df()


with tabs[1]:
    st.caption("이름으로 찾아 동명이인을 구분하고, 지금 위 조건(기간·학회·유형)에서 분야별로 몇 등인지 보여줍니다. "
               "DBLP는 동명이인을 'Name 0001', 'Name 0002'처럼 번호로 나눕니다 — 같은 이름이 여러 줄이면 소속·활동 "
               "연도·최근 논문으로 구분하세요.")
    q = st.text_input("이름 (영문, 예: Hwanjun Song)", key="rc_q")
    if q and len(_norm(q)) >= 3:
        m = _search(q)
        if m.empty:
            st.info("DBLP top 학회 논문 저자 중 그 이름이 없습니다.")
        else:
            m["key"] = m.base.map(_norm)  # "Jinwoo Shin" / "Jin Woo Shin" / "Jinwoo Shin 0002" → same name
            m["homonyms"] = m.groupby("key").name.transform("count")
            m["who"] = [("국내 교수" if r == "KR faculty" else "해외 교수" if r == "faculty abroad" else (s or "미상"))
                        + (f" — {t}" if src == "web" and t else "")
                        for r, s, src, t in zip(m.role, m.stage, m.stage_source, m.pos_title)]
            m["korean"] = m.is_korean.map({True: "예", False: "아니오"}).fillna("미상")
            m["active"] = [f"{int(a)}–{int(b)}" if pd.notna(a) else "" for a, b in zip(m.y0, m.y1)]
            dup = m[m.homonyms > 1].groupby("key").name.apply(list)
            if len(dup):
                st.warning("동명이인(또는 표기만 다른 이름) 있음: "
                           + "; ".join(" / ".join(f"**{n}**" for n in v) for v in dup)
                           + " — 아래 표에서 소속·활동 연도·최근 논문으로 구분하세요.")
            st.dataframe(
                m[["name", "homonyms", "who", "affiliation", "korean", "academic_age", "active", "papers_all",
                   "last_paper"]],
                hide_index=True, width="stretch", height=min(400, 36 * len(m) + 40), column_config={
                    "name": "DBLP 이름", "homonyms": st.column_config.NumberColumn("같은 이름", format="%d명"),
                    "who": "구분", "affiliation": "소속", "korean": "한국인", "academic_age": "학문적 나이 (추정)",
                    "active": "활동 연도", "papers_all": st.column_config.NumberColumn("top 논문 (전체 기간)"),
                    "last_paper": st.column_config.TextColumn("최근 논문", width="large")})
            w1, w2 = st.columns([4, 1])
            who = w1.selectbox("순위를 볼 사람", list(m.name), key="rc_q_pick",
                               format_func=lambda n: f"{n} — {m.set_index('name').affiliation.get(n) or '소속 미상'}")
            win = int(w2.number_input("동년차 범위 ±", 1, 10, 3, key="rc_q_win", help="학문적 나이 차이가 이 안인 사람끼리 비교"))
            my_age = m.set_index("name").academic_age.get(who)
            ages = dict(c.execute("SELECT person, academic_age FROM person_age WHERE academic_age IS NOT NULL").fetchall())
            pf = B.person_field_counts(c, [who], B.BF(start=y0, end=y1, next_tier=nt))
            if pf.empty:
                st.info(f"{y0}–{y1}에 선택한 학회 논문이 없습니다. 위에서 기간을 넓혀 보세요.")
            else:
                rows = []
                for _, r in pf.sort_values("papers", ascending=False).iterrows():
                    k, x = r.field, int(r.papers)
                    d = per_field.get(k)
                    kp = krp_all[krp_all.field == k].papers
                    in_pool = d is not None and who in set(d.name)
                    pool = d[d.name != who] if d is not None else None
                    if pd.notna(my_age):
                        kc = [p_ for n_, p_ in zip(krp_all[krp_all.field == k].name, kp)
                              if n_ != who and n_ in ages and abs(ages[n_] - my_age) <= win]
                        cc = pool[(pool.acad_age - my_age).abs() <= win] if pool is not None else None
                        kc_rank = f"{1 + sum(p_ > x for p_ in kc)}위 / {len(kc) + 1}명"
                        kc_pct = max(1, round(100 * (1 + sum(p_ >= x for p_ in kc)) / (len(kc) + 1)))
                        cc_rank = f"{1 + int((cc.field > x).sum())}위 / {len(cc) + 1}명" if cc is not None else "후보 없음"
                    else:
                        kc_rank = cc_rank = "나이 미상"
                        kc_pct = None
                    rows.append({
                        "field": f"{k} · {B.FIELDS[k][0]}" if k in B.FIELDS else k, "papers": x,
                        "cand_rank": (f"{1 + int((pool.field > x).sum())}위 / {len(pool) + 1}명"
                                      if pool is not None else "후보 없음"),
                        "in_pool": "포함" if in_pool else "아님",
                        "kr_rank": f"{1 + int((kp > x).sum())}위 / {len(kp)}명" if len(kp) else "",
                        "kr_pct": max(1, round(100 * (kp >= x).mean())) if len(kp) else None,
                        "cc_rank": cc_rank, "kc_rank": kc_rank, "kc_pct": kc_pct,
                    })
                st.markdown(f"**{who}** — {y0}–{y1} 분야별 순위 ({'확장 학회 포함' if nt else 'top 학회만'})"
                            + (f" · 학문적 나이 {int(my_age)}년 (추정) → 동년차 = {int(my_age) - win}~{int(my_age) + win}년"
                               if pd.notna(my_age) else " · 학문적 나이 미상 (동년차 비교 불가)"))
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
                    "field": "분야", "papers": st.column_config.NumberColumn("논문", format="%d편"),
                    "cand_rank": st.column_config.TextColumn(
                        "초빙 후보 중 순위", help="지금 조건(유형·학문적 나이·위치·최소 논문)의 후보들 사이에 넣었을 때"),
                    "in_pool": st.column_config.TextColumn(
                        "후보 목록에 있음", help="'아님' = 국내 교수이거나, 나이·유형·위치·최소 논문 조건 밖"),
                    "kr_rank": st.column_config.TextColumn("국내 교수 중 순위", help="같은 분야·기간 국내 교수 논문 수 기준"),
                    "kr_pct": st.column_config.NumberColumn("국내 교수 대비", format="상위 %d%%"),
                    "cc_rank": st.column_config.TextColumn(f"동년차(±{win}) 후보 중",
                                                           help="학문적 나이가 비슷한 초빙 후보 사이 순위"),
                    "kc_rank": st.column_config.TextColumn(f"동년차(±{win}) 국내 교수 중",
                                                           help="학문적 나이가 비슷한 국내 교수 사이 순위 (그 분야 논문 1편 이상인 교수)"),
                    "kc_pct": st.column_config.NumberColumn(f"동년차 국내 교수 대비", format="상위 %d%%")})
                if pd.notna(my_age):
                    st.markdown(f"**동년차(학문적 나이 {int(my_age) - win}~{int(my_age) + win}년) 중 {who}보다 잘하는 사람**")
                    flds = list(pf.sort_values("papers", ascending=False).field)
                    fk = st.selectbox("분야", flds, key="rc_q_fld",
                                      format_func=lambda k: f"{k} · {B.FIELDS[k][0]}" if k in B.FIELDS else k)
                    x = int(pf.set_index("field").papers[fk])
                    aff = dict(c.execute("SELECT name, any_value(affiliation) FROM cs_faculty GROUP BY 1").fetchall())
                    kk = krp_all[(krp_all.field == fk) & (krp_all.name != who)]
                    kk = kk[kk.name.map(lambda n: n in ages and abs(ages[n] - my_age) <= win)]
                    kr_rows = pd.DataFrame({"name": kk.name, "group": "국내 교수", "institution": kk.name.map(aff),
                                            "acad_age": kk.name.map(ages), "field": kk.papers})
                    d = per_field.get(fk)
                    if d is not None:
                        d = d[(d.name != who) & ((d.acad_age - my_age).abs() <= win)]
                        cd_rows = d[["name", "type", "institution", "acad_age", "field"]].rename(columns={"type": "group"})
                    else:
                        cd_rows = kr_rows.iloc[0:0]
                    me = pd.DataFrame([{"name": who, "group": "◀ 검색한 사람", "institution":
                                        m.set_index("name").affiliation.get(who), "acad_age": my_age, "field": x}])
                    allc = pd.concat([kr_rows, cd_rows, me], ignore_index=True)
                    # citations of these field papers in the window (OpenAlex, current counts)
                    fw, fprm = B.BF(start=y0, end=y1, next_tier=nt, fields=[fk]).where("p", "v")
                    cit = c.execute(
                        f"""SELECT a.name, sum(pc.cited_by)::BIGINT AS cites, max(pc.cited_by) AS best
                            FROM dblp_authorship a JOIN dblp_paper p USING (key) JOIN venue v ON v.venue = p.venue
                            LEFT JOIN paper_cites pc ON pc.key = p.key
                            WHERE a.name IN (SELECT unnest(?)) AND {fw} GROUP BY 1""",
                        [list(allc.name), *fprm]).df().set_index("name")
                    allc["cites"] = allc.name.map(cit.cites).fillna(0).astype(int)
                    allc["per_paper"] = (allc.cites / allc.field).round(1)
                    allc["best"] = allc.name.map(cit.best).astype("Int64")
                    CRIT = {"field": "논문 수", "cites": "피인용 합", "per_paper": "편당 피인용"}
                    crit = st.radio("비교 기준", list(CRIT), format_func=CRIT.get, horizontal=True, key="rc_q_crit")
                    mine = allc[allc.group == "◀ 검색한 사람"].iloc[0]
                    others = allc[allc.group != "◀ 검색한 사람"]
                    better = others[others[crit] > mine[crit]]
                    same = others[others[crit] == mine[crit]]
                    show = pd.concat([better, allc[allc.group == "◀ 검색한 사람"]], ignore_index=True).sort_values(
                        [crit, "name"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
                    show["acad_age"] = show.acad_age.round().astype("Int64")
                    show.insert(0, "no", range(1, len(show) + 1))
                    st.caption(f"{fk} {CRIT[crit]} 기준으로 {who}({mine[crit]:,})보다 앞선 동년차: 국내 교수 {int((better.group == '국내 교수').sum())}명 · "
                               f"초빙 후보 {int((better.group != '국내 교수').sum())}명 (같은 값 {len(same)}명). "
                               "초빙 후보는 위 조건(유형·위치·최소 논문)에 맞는 사람만. 이름을 누르면 상세가 열립니다.")
                    ui.people_table(
                        ui.add_age(show, after="acad_age"), "rc_q_better",
                        B.BF(start=y0, end=y1, next_tier=nt, fields=[fk]),
                        height=min(520, 36 * len(show) + 40),
                        column_config={
                            "no": st.column_config.NumberColumn("#", width="small"), "group": "구분",
                            "institution": "소속", "acad_age": "학문적 나이 (추정)",
                            "field": st.column_config.ProgressColumn(
                                f"{fk} 논문 {y0}–{y1}", format="%d", min_value=0,
                                max_value=int(show.field.max() or 1)),
                            "cites": st.column_config.NumberColumn(
                                "피인용", format="localized",
                                help=f"이 {fk} 논문들의 현재 피인용 합 (OpenAlex; DOI·제목으로 못 찾은 논문은 빠짐)"),
                            "per_paper": st.column_config.NumberColumn("편당 피인용", format="%.1f"),
                            "best": st.column_config.NumberColumn("최다 피인용 논문", format="localized")})
                with st.expander(f"{who} 상세 (논문 목록·피인용·나이 근거)"):
                    ui.person_card(who, B.BF(start=y0, end=y1, next_tier=nt), key="rc_q_card")
    elif q:
        st.caption("3글자 이상 입력하세요.")
for t, (k, d) in zip(tabs[2:], per_field.items()):
    with t:
        st.markdown(f"**{k} · {B.FIELDS[k][0]}**")
        q1, q2 = st.columns([3, 1.4])
        tt = q1.multiselect("유형 좁히기", types, default=types, key=f"rc_t_{k}")
        only_main = q2.toggle(f"주 분야가 {k}인 사람만", value=True, key=f"rc_main_{k}",
                              help=f"끄면 주 분야는 다르지만 {k} 논문도 {min_p}편 이상인 사람까지 보입니다")
        d = d[d.type.isin(tt)]
        if only_main:
            d = d[d["main"] == k]
        st.caption(f"후보 {len(d)}명")
        show = d[["name", "type", "verified", "pos_url", "institution", "main", "acad_age", "field", "recent", "per_year",
                  "first", "cites", "kr_equiv", "kr_pct", "cohort_pctl"]].copy()
        show.insert(0, "no", range(1, len(show) + 1))
        mx = int(show.field.max() or 1)
        ui.people_table(
            ui.add_age(show, after="acad_age"), f"rc_tab_{k}", B.BF(start=y0, end=y1, next_tier=nt, fields=[k]),
            height=min(620, 36 * len(show) + 40),
            column_config={
                "no": st.column_config.NumberColumn("#", width="small"), "type": "유형",
                "verified": st.column_config.TextColumn("현재 직위 (웹 확인)", width="medium",
                                                        help="웹 검색으로 확인한 현재 직위. 비어 있으면 확인 못 해 추정"),
                "pos_url": st.column_config.LinkColumn("근거", display_text="↗", width="small"),
                "institution": "현재 소속", "main": "주 분야", "acad_age": "학문적 나이",
                "field": st.column_config.ProgressColumn(f"{k} 논문 {y0}–{y1}", format="%d", min_value=0,
                                                         max_value=mx),
                "recent": st.column_config.NumberColumn("최근 2년", help=f"{max(y0, y1 - 1)}–{y1} {k} 논문"),
                "per_year": st.column_config.NumberColumn("연평균", format="%.2f",
                                                          help="분야 논문 / 활동 연수 (PhD 4년 전부터, 기간 안)"),
                "first": st.column_config.NumberColumn("1저자", help="기간 내 전체 top 학회 1저자 논문"),
                "cites": st.column_config.NumberColumn("피인용", format="localized", help="기간 내 논문의 OpenAlex 피인용"),
                "kr_equiv": st.column_config.TextColumn("국내 교수 환산 순위",
                                                        help=f"같은 기간 {k} 논문 수로 국내 교수들 사이에 넣었을 때 순위"),
                "kr_pct": st.column_config.NumberColumn("국내 교수 대비", format="상위 %d%%",
                                                        help="국내 교수 중 이 사람 이상으로 논문을 낸 비율 — 작을수록 상위"),
                "cohort_pctl": st.column_config.NumberColumn("동년차 국내 교수 대비", format="%d백분위",
                                                             help="학문적 나이 ±2년 국내 교수 사이 백분위 (전체 분야 논문)"),
            },
        )
        st.download_button("CSV 다운로드", show.to_csv(index=False), f"recruit_{k}_{y0}-{y1}.csv",
                           key=f"rc_csv_{k}")
st.caption("유형은 웹 검색(LLM)으로 현재 직위를 확인한 경우 그 결과를, 아니면 최근 논문 소속과 학문적 나이로 추정한 값을 "
           "씁니다. 유형·한국인 여부·나이는 공개 데이터(CSrankings·DBLP·OpenAlex·ORCID·홈페이지)와 LLM 지식으로 추정한 값이라 "
           "틀릴 수 있습니다. 초빙 판단 전 본인 이력서로 확인하세요.")
