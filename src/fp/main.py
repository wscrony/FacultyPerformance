"""App entry: analysis boards + the original search/compare explorer. Launch with `fp app` (127.0.0.1 only)."""

import streamlit as st

st.set_page_config(page_title="Faculty Performance", page_icon="📊", layout="wide")


def _hosted_setup() -> None:
    """streamlit.app: settings come from st.secrets; the data bundle is downloaded once per container."""
    import os

    try:
        sec = dict(st.secrets)
    except Exception:  # no secrets file (local run)
        sec = {}
    for k in ("FP_APP_PASSWORD", "FP_DATA_URL", "FP_HOSTED"):
        if k in sec:  # secrets win: an edit in the Cloud settings must reach this already-running process
            os.environ[k] = str(sec[k])
    if os.environ.get("FP_DATA_URL"):
        os.environ.setdefault("FP_HOSTED", "1")
        from fp import bundle

        with st.spinner("데이터 내려받는 중 (처음 한 번, 1–2분)…"):
            fresh = bundle.ensure(os.environ["FP_DATA_URL"])
        if fresh:  # new bundle: reopen the DB and drop results computed from the old one
            from fp import ui

            ui.reset_connections()
            st.cache_data.clear()


_hosted_setup()


def _gate() -> None:
    """Password gate when FP_APP_PASSWORD is set (required for `fp app --host 0.0.0.0`)."""
    import hmac
    import os

    pw = os.environ.get("FP_APP_PASSWORD")
    if not pw or st.session_state.get("_authed"):
        return
    st.title("Faculty Performance")
    st.caption("한국 CS 교수·연구자 논문 실적 분석. 모든 데이터는 공개 데이터(CSrankings · DBLP · OpenAlex · ORCID · "
               "공개 홈페이지)로 만들었으며, 나이는 추정치로 정확하지 않습니다.")
    got = st.text_input("비밀번호", type="password")
    if got and hmac.compare_digest(got, pw):
        st.session_state["_authed"] = True
        st.rerun()
    elif got:
        st.error("비밀번호가 틀렸습니다.")
    st.stop()


_gate()

pg = st.navigation({
    "분석 보드": [
        # 세계 속 한국 (views/world.py) hidden by user request; file kept
        st.Page("views/schools.py", title="국내 기관", icon=":material/account_balance:", default=True),
        st.Page("views/people.py", title="한국인 연구자", icon=":material/groups:"),
        st.Page("views/field.py", title="분야 심층 비교", icon=":material/insights:", url_path="field"),
    ],
    "탐색": [
        st.Page("app.py", title="검색 · 비교", icon=":material/search:"),
        st.Page("views/recruit.py", title="교수 초빙 후보", icon=":material/person_search:", url_path="recruit"),
    ],
    "설정": [st.Page("views/venues.py", title="Top 학회 설정", icon=":material/tune:", url_path="venues")],
})
st.sidebar.caption("Developed by Wook-Shin Han")
st.sidebar.info(
    "**데이터 출처:** 모든 데이터는 공개(open) 데이터로 만들었습니다 — CSrankings(교수 명단·소속·홈페이지), "
    "DBLP(논문·저자), OpenAlex(피인용·소속), ORCID·공개 홈페이지(학위 연도). 학과·일부 학위 연도는 공개 정보에 "
    "대한 LLM 지식 기반 추정입니다.\n\n"
    "**나이는 추정치이며 정확하지 않습니다.** 실제 나이·학문적 나이 모두 학위 연도나 첫 논문 연도로 계산한 "
    "근사값이고 수 년의 오차가 있을 수 있습니다. 인사·채용 판단의 근거로 쓰지 마세요.",
    icon=":material/info:",
)
pg.run()
