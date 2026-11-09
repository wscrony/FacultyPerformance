"""Shared Streamlit helpers: DB cursor, palette (light/dark), chart chrome, small components."""

from __future__ import annotations

import re
from datetime import date

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fp import age, pii

THIS_YEAR = date.today().year

# Categorical palette (dataviz reference instance), fixed order; validated (light + dark) for 8 adjacent slots.
_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
GRAY = {"light": "#a3a29b", "dark": "#6f6e69"}
_SEQ = {
    "light": [[0, "#eef4fc"], [0.25, "#b7d3f6"], [0.5, "#6da7ec"], [0.75, "#256abf"], [1, "#0d366b"]],
    "dark": [[0, "#1f2a38"], [0.25, "#184f95"], [0.5, "#2a78d6"], [0.75, "#6da7ec"], [1, "#cde2fb"]],
}
# Region groups: fixed slot per entity (color follows the entity, never its rank). 'Other' folds to gray.
REGIONS = ["Korea", "United States", "China", "Europe", "Rest of Asia", "Other"]


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def series() -> list[str]:
    return _DARK if mode() == "dark" else _LIGHT


def seq_scale():
    return _SEQ[mode()]


def gray() -> str:
    return GRAY[mode()]


def region_color(g: str) -> str:
    return gray() if g == "Other" else series()[REGIONS.index(g)]


@st.cache_resource
def _root_con() -> duckdb.DuckDBPyConnection:
    return pii.connect(read_only=True)


def con() -> duckdb.DuckDBPyConnection:
    """Per-session cursor (DuckDB connections aren't shared across threads); temp views are per cursor."""
    if "cur" not in st.session_state:
        c = _root_con().cursor()
        age.create_views(c)
        st.session_state.cur = c
    return st.session_state.cur


def require_db():
    try:
        con()
    except duckdb.IOException:
        st.error("DB가 다른 작업(fp update …)에 의해 잠겨 있습니다. 갱신이 끝난 뒤 새로고침하세요.")
        st.stop()


def plot(fig: go.Figure, height: int = 420, legend: bool | None = None, key: str | None = None):
    multi = len(fig.data) > 1 if legend is None else legend
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=84 if multi else 44, b=8),
        title=dict(y=0.99, yanchor="top", x=0, xanchor="left", font=dict(size=15)),
        hoverlabel=dict(namelength=-1),
        showlegend=multi,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, traceorder="normal"),
        font=dict(family="Pretendard, -apple-system, 'Apple SD Gothic Neo', sans-serif"),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    st.plotly_chart(fig, width="stretch", key=key)


def note(text: str):
    st.caption(text)


# ── board filter bar ───────────────────────────────────────────────────────────

CREDIT = {"any": "교수 저자가 한 명이라도 있으면", "last": "마지막 저자(주로 PI)가 그 기관 교수일 때만"}


@st.cache_data
def _areas() -> list[tuple[str, str, str]]:
    from fp import metrics

    return metrics.all_areas(con())


@st.cache_data
def _venues() -> list[str]:
    return [r[0] for r in con().execute("SELECT venue FROM venue ORDER BY 1").fetchall()]


def board_filter(key: str, default_years: int = 10, credit: bool = True):
    from fp.boards import BF

    areas = _areas()
    title = {a: t for a, t, _ in areas}
    c1, c2, c3, c4 = st.columns([2, 2, 3, 2])
    yr = c1.slider("기간", 2000, THIS_YEAR, (THIS_YEAR - default_years + 1, THIS_YEAR), key=f"{key}_yr")
    from fp.boards import AREA_FIELD, FIELDS

    cats = c2.multiselect("분야", list(FIELDS), format_func=lambda k: f"{k} · {FIELDS[k][0]}", key=f"{key}_cat")
    pool = [a for a, _, _ in areas if not cats or AREA_FIELD.get(a, "기타") in cats]
    picked = c3.multiselect("세부 분야 (CSrankings)", pool, format_func=lambda a: f"{title[a]}", key=f"{key}_area")
    venues = c4.multiselect("학회 (선택 시 분야 무시)", _venues(), key=f"{key}_venue")
    o1, o2 = st.columns([3, 2])
    nt = o2.toggle("확장 학회 포함 (KDD · ICDE · PODS · AAAI · IJCAI 등)", value=False, key=f"{key}_nt",
                   help="기본은 top 학회만 셉니다. 켜면 CSrankings 확장 학회와 AAAI·IJCAI까지 셉니다. DM의 KDD도 이걸 켜야 들어갑니다.")
    cr = "any"
    if credit:
        cr = o1.radio("기관 귀속 기준", list(CREDIT), format_func=CREDIT.get, horizontal=True, key=f"{key}_credit")
    return BF(start=yr[0], end=yr[1], areas=picked or None, venues=venues or None, credit=cr, next_tier=nt,
              fields=cats or None)


def kpis(items: list[tuple[str, str, str | None]]):
    cols = st.columns(len(items))
    for c, (label, value, help_) in zip(cols, items):
        c.metric(label, value, help=help_, border=True)


_SHORT = {
    "Massachusetts Institute of Technology": "MIT", "Carnegie Mellon University": "CMU",
    "Univ. of Illinois at Urbana-Champaign": "UIUC", "University of California - Berkeley": "UC Berkeley",
    "University of California - San Diego": "UC San Diego", "University of California - Los Angeles": "UCLA",
    "University of California - Irvine": "UC Irvine", "University of California - Santa Barbara": "UCSB",
    "University of California - Davis": "UC Davis", "University of California - Riverside": "UC Riverside",
    "University of California - Santa Cruz": "UC Santa Cruz", "Georgia Institute of Technology": "Georgia Tech",
    "University of Washington": "U Washington", "University of Michigan": "U Michigan",
    "University of Wisconsin - Madison": "UW–Madison", "University of Texas at Austin": "UT Austin",
    "University of Maryland - College Park": "UMD", "University of Southern California": "USC",
    "University of Pennsylvania": "UPenn", "New York University": "NYU", "Stanford University": "Stanford",
    "Cornell University": "Cornell", "Princeton University": "Princeton", "Harvard University": "Harvard",
    "Purdue University": "Purdue", "Northeastern University": "Northeastern", "Columbia University": "Columbia",
    "Johns Hopkins University": "Johns Hopkins", "Tsinghua University": "Tsinghua", "Peking University": "Peking U",
    "Zhejiang University": "Zhejiang U", "Shanghai Jiao Tong University": "SJTU", "Nanjing University": "Nanjing U",
    "Fudan University": "Fudan", "Chinese Academy of Sciences": "CAS", "Nanyang Technological University": "NTU",
    "National University of Singapore": "NUS", "University of Hong Kong": "HKU", "Seoul National University": "SNU",
    "Korea University": "Korea U", "Yonsei University": "Yonsei", "Sungkyunkwan University": "SKKU",
    "Hanyang University": "Hanyang", "ETH Zurich": "ETH Zurich", "University of Oxford": "Oxford",
    "University of Cambridge": "Cambridge", "Imperial College London": "Imperial", "University of Toronto": "Toronto",
    "Beihang University": "Beihang", "University of Tokyo": "U Tokyo", "Harbin Institute of Technology": "HIT",
    "Wuhan University": "Wuhan U", "Sun Yat-sen University": "SYSU", "Huazhong University of Science and Technology": "HUST",
    "University of Science and Technology of China": "USTC", "Hong Kong Polytechnic University": "PolyU",
    "Chinese University of Hong Kong": "CUHK", "Tianjin University": "Tianjin U", "Southeast University": "Southeast U",
    "Xidian University": "Xidian", "Sichuan University": "Sichuan U", "Shandong University": "Shandong U",
}


_SHORT.update({
    "Massachusetts Inst. of Technology": "MIT", "Univ. of Maryland - College Park": "UMD",
    "Univ. of Southern California": "USC", "Univ. of Texas at Austin": "UT Austin",
})


def short(name: str) -> str:
    """Compact institution label for dense charts (full name stays in hover)."""
    if name in _SHORT:
        return _SHORT[name]
    m = re.match(r"Univ(?:\.|ersity) of California\s*-\s*(.+)", name)
    if m:
        return "UC " + m.group(1)
    s = re.sub(r"^Univ(?:\.|ersity) of ", "U ", name)
    s = s.replace(" University", " U").replace("Institute of Technology", "IT").replace("Inst. of Technology", "IT")
    return s if len(s) <= 22 else s[:21] + "…"


def ink(fill: str) -> str:
    """Readable text color on a filled tile (WCAG relative luminance)."""
    h = fill.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#1a1a19" if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else "#ffffff"


# ───────────── people tables: real age next to academic age, click a name → person card ─────────────

def _as_filter(f):
    """BF (boards) or metrics.Filter → metrics.Filter for the paper list."""
    from fp import boards as B
    from fp import metrics

    if isinstance(f, metrics.Filter):
        return f
    venues = f.venues or (B.field_venues(con(), f.fields, f.next_tier) if f.fields and not f.areas else None)
    return metrics.Filter(start=f.start, end=f.end, areas=f.areas, venues=venues, next_tier=f.next_tier)


def add_age(df: pd.DataFrame, name_col: str = "name", after: str | None = None) -> pd.DataFrame:
    """Add 'age' (estimated real age, this year − birth year incl. estimates) and 'age_est' (True = estimate)."""
    names = [n for n in df[name_col].dropna().unique()]
    a = con().execute("SELECT person, birth_year, birth_source FROM person_age WHERE person IN (SELECT unnest(?))",
                      [names]).df().set_index("person")
    out = df.drop(columns=["age", "age_est"], errors="ignore").copy()
    by = out[name_col].map(a.birth_year)
    ages = (THIS_YEAR - by).astype("Int64")
    est = out[name_col].map(a.birth_source).fillna("est").str.startswith("est")
    pos = (list(out.columns).index(after) + 1) if after in out.columns else len(out.columns)
    out.insert(pos, "age", ages)
    out.insert(pos + 1, "age_est", est)
    return out


AGE_COLS = {
    "age": st.column_config.NumberColumn("나이 (추정)", help="올해 − 출생연도. 출생연도를 모르면 PhD−30 / 학사−23 / "
                                                           "첫 top 논문−28로 추정"),
    "age_est": st.column_config.CheckboxColumn(
        "나이 추정값", help="체크 = 출생연도를 직접 확인하지 못해 추정한 나이. 박사 연도−30 → 학사 연도−23 → "
                          "첫 top 논문 연도−28 순으로 씀 (±3년 정도 오차)"),
}


def link_color() -> str:
    return "#6da7ec" if mode() == "dark" else "#1f5fae"


def selected_row(key: str) -> int | None:
    """Row picked in a selectable dataframe (row checkbox or any cell), from the previous interaction."""
    sel = st.session_state.get(key)
    if sel is None or not hasattr(sel, "selection"):
        return None
    rows = list(sel.selection.get("rows") or []) or [c[0] for c in (sel.selection.get("cells") or [])]
    return rows[0] if rows else None


def link_style(df: pd.DataFrame, link_col: str = "name", selected: int | None = None):
    """Clickable column styled like a link; the selected row highlighted (Styler keeps selection working)."""
    c = link_color()
    sty = df.style.map(lambda _: f"color: {c}; text-decoration: underline; font-weight: 600", subset=[link_col])
    if selected is not None and 0 <= selected < len(df):
        bg = "rgba(57,135,229,0.28)" if mode() == "dark" else "rgba(42,120,214,0.16)"
        sty = sty.apply(lambda r: [f"background-color: {bg}; font-weight: 700" if r.name == df.index[selected]
                                   else "" for _ in r], axis=1)
    return sty


AGE_SRC_LABEL = {
    "manual": "수동 입력 (확인)", "orcid": "ORCID (확인)", "homepage": "홈페이지 (확인)",
    "est:llm": "LLM 지식 (추정)", "est:first_top": "첫 top 논문 (추정)",
}
AGE_SRC_HELP = (
    "학문적 나이(박사 후 연수)의 근거가 된 박사 연도의 출처.\n\n"
    "· ORCID (확인): 본인 ORCID 학력에 적힌 박사 연도\n\n"
    "· 홈페이지 (확인): 본인 홈페이지/CV에서 읽은 박사 연도\n\n"
    "· 수동 입력 (확인): 직접 입력한 값\n\n"
    "· LLM 지식 (추정): 위 출처가 없어 Claude(Opus)가 알고 있는 박사 연도로 채운 값 (확신도 0.7 이상, 논문 기록과 모순되면 버림)\n\n"
    "· 첫 top 논문 (추정): 박사 연도를 몰라 첫 top 학회 논문 연도 + 2년으로 추정 (확인된 608명 기준 오차 중앙값 2년)\n\n"
    "· 없음: top 학회 논문도 없어 추정 불가"
)
_AGE_SRC_COLS = ("age_source", "age_src", "나이 출처")


def _explain_age_source(df: pd.DataFrame, cc: dict) -> pd.DataFrame:
    """Readable labels + hover help for the academic-age source column, wherever a table shows one."""
    for col in _AGE_SRC_COLS:
        if col in df.columns:
            df = df.assign(**{col: df[col].map(lambda v: AGE_SRC_LABEL.get(v, v) if pd.notna(v) else "없음")})
            cc[col] = st.column_config.TextColumn("학문적 나이 출처", help=AGE_SRC_HELP)
    return df


def clickable_table(df: pd.DataFrame, key: str, link_col: str, column_config: dict | None = None,
                    link_label: str | None = None, link_help: str = "클릭하면 아래에 자세한 정보가 열립니다",
                    height: int | None = None, **kw) -> int | None:
    """Selectable table: link-styled column (▸ header), selected row highlighted. Returns the selected row."""
    cc = dict(column_config or {})
    df = _explain_age_source(df, cc)
    lbl = link_label or (cc.get(link_col) if isinstance(cc.get(link_col), str) else link_col)
    cc[link_col] = st.column_config.TextColumn(f"{lbl} ↗", help=link_help)
    pick = selected_row(key)
    pick = pick if pick is not None and pick < len(df) else None
    st.dataframe(link_style(df, link_col, pick), hide_index=True, width="stretch", key=key, on_select="rerun",
                 selection_mode=["single-row", "single-cell"],
                 height=height or min(460, 36 * len(df) + 40), column_config=cc, **kw)
    return pick


def people_table(df: pd.DataFrame, key: str, f, column_config: dict | None = None, name_col: str = "name",
                 height: int | None = None, **kw) -> None:
    """Person table: click a name → person card (papers + citations + age evidence) below."""
    cc = {**AGE_COLS, **(column_config or {})}
    pick = clickable_table(df, key, name_col, cc, link_label=cc.get(name_col) if isinstance(cc.get(name_col), str)
                           else "이름", link_help="클릭하면 논문 목록·피인용·나이 근거가 아래에 열립니다",
                           height=height, **kw)
    if pick is not None:
        nm = df.iloc[pick][name_col]
        st.markdown(f"▼ **{nm}** 선택됨 — 자세한 정보 (다른 이름을 클릭하면 바뀝니다)")
        person_card(nm, f, key=f"{key}_card")
    else:
        st.caption("파란 이름을 클릭하면 그 사람의 논문 목록·피인용·나이 근거가 아래에 열립니다.")


def person_card(name: str, f, key: str) -> None:
    from fp import links

    mf = _as_filter(f)
    # own cursor: a click reruns the script while the previous run may still be querying the session cursor;
    # sharing one DuckDB cursor across those threads returned empty results (Juho Kim 0001 → 0 papers)
    c = _root_con().cursor()
    age.create_views(c)
    pa = c.execute("""SELECT a.affiliation, a.phd_year, a.phd_institution, a.age_source, a.academic_age,
                             a.birth_year, a.birth_source, a.first_top_year, r.role, s.stage
                      FROM person_age a LEFT JOIN person_role r USING (person)
                      LEFT JOIN person_stage s USING (person) WHERE a.person = ?""", [name]).df()
    pp = age.person_papers(c, name, mf)
    with st.container(border=True):
        p = pa.iloc[0] if len(pa) else None
        role = ""
        if p is not None:
            role = {"KR faculty": "국내 교수", "faculty abroad": "해외 교수"}.get(p.role) or (p.stage or "")
        st.markdown(f"#### {name}")
        lk = links.person_links(c, name)
        if lk:
            st.markdown(" · ".join(f"[{k}{' (검색)' if kind == 'search' else ''}]({u})" for k, (u, kind) in lk.items()))
        tot = int(pp.cites.fillna(0).sum())
        if p is not None:
            est_b = str(p.birth_source or "est").startswith("est")
            est_a = str(p.age_source or "est").startswith("est")
            kp = [
                ("소속", p.affiliation or "-", role or None),
                ("나이" + (" (추정)" if est_b else ""), f"{THIS_YEAR - int(p.birth_year)}세" if pd.notna(p.birth_year)
                 else "-", f"출생 {int(p.birth_year)} · 출처 {p.birth_source}" if pd.notna(p.birth_year) else None),
                ("학문적 나이" + (" (추정)" if est_a else ""), f"{int(p.academic_age)}년" if pd.notna(p.academic_age)
                 else "-", (f"PhD {int(p.phd_year)}" + (f" {p.phd_institution}" if p.phd_institution else "")
                            if pd.notna(p.phd_year) else f"첫 top 논문 {p.first_top_year}") + f" · 출처 {p.age_source}"),
                (f"논문 {mf.start}–{mf.end}", f"{len(pp)}편", f"피인용 {tot:,}"),
            ]
            kpis(kp)
            st.caption(" · ".join(f"{l}: {h}" for l, _, h in kp[1:3] if h))
        show = pp.assign(cites=[f"{int(x):,}" if pd.notna(x) else "" for x in pp.cites], link=pp.link.fillna(""))
        st.dataframe(
            show, hide_index=True, width="stretch", height=min(480, 36 * len(pp) + 40), key=key,
            column_config={
                "year": st.column_config.NumberColumn("연도", format="%d", width="small"),
                "venue": "학회", "field": "분야", "title": st.column_config.TextColumn("제목", width="large"),
                "position": "저자 위치",
                "cites": st.column_config.TextColumn("피인용", help="OpenAlex — DOI, 없으면(NeurIPS 등) 제목 일치"),
                "link": st.column_config.LinkColumn("링크", display_text="열기", help="DOI, 없으면 dblp"),
            },
        )
        st.caption("논문 = 현재 화면의 기간·분야 조건. 피인용 = OpenAlex (DOI 없는 논문은 제목 일치, 빈칸 = 매칭 전/실패). "
                   "나이·학위 근거는 로컬 암호화 DB.")
