"""Shared Streamlit helpers: DB cursor, palette (light/dark), chart chrome, small components."""

from __future__ import annotations

import re
from datetime import date

import duckdb
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
    return [r[0] for r in con().execute("SELECT venue FROM venue WHERE NOT next_tier ORDER BY 1").fetchall()]


def board_filter(key: str, default_years: int = 5, credit: bool = True):
    from fp.boards import BF

    areas = _areas()
    title = {a: t for a, t, _ in areas}
    c1, c2, c3, c4 = st.columns([2, 2, 3, 2])
    yr = c1.slider("기간", 2000, THIS_YEAR, (THIS_YEAR - default_years, THIS_YEAR), key=f"{key}_yr")
    cats = c2.multiselect("분야 그룹", ["ai", "systems", "theory", "interdisciplinary"], key=f"{key}_cat")
    pool = [a for a, _, c in areas if not cats or c in cats]
    picked = c3.multiselect("세부 분야", pool, format_func=lambda a: f"{title[a]}", key=f"{key}_area")
    venues = c4.multiselect("학회 (선택 시 분야 무시)", _venues(), key=f"{key}_venue")
    cr = "any"
    if credit:
        cr = st.radio("기관 귀속 기준", list(CREDIT), format_func=CREDIT.get, horizontal=True, key=f"{key}_credit")
    return BF(start=yr[0], end=yr[1], areas=picked or (pool if cats else None), venues=venues or None, credit=cr)


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
