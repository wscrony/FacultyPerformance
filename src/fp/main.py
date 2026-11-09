"""App entry: analysis boards + the original search/compare explorer. Launch with `fp app` (127.0.0.1 only)."""

import streamlit as st

st.set_page_config(page_title="Faculty Performance", page_icon="📊", layout="wide")


def _gate() -> None:
    """Password gate when FP_APP_PASSWORD is set (required for `fp app --host 0.0.0.0`)."""
    import hmac
    import os

    pw = os.environ.get("FP_APP_PASSWORD")
    if not pw or st.session_state.get("_authed"):
        return
    st.title("Faculty Performance")
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
    "탐색": [st.Page("app.py", title="검색 · 비교", icon=":material/search:")],
    "설정": [st.Page("views/venues.py", title="Top 학회 설정", icon=":material/tune:", url_path="venues")],
})
st.sidebar.caption("Developed by Wook-Shin Han")
pg.run()
