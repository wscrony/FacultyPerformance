"""App entry: analysis boards + the original search/compare explorer. Launch with `fp app` (127.0.0.1 only)."""

import streamlit as st

st.set_page_config(page_title="Faculty Performance", page_icon="📊", layout="wide")

pg = st.navigation({
    "분석 보드": [
        # 세계 속 한국 (views/world.py) hidden by user request; file kept
        st.Page("views/schools.py", title="국내 기관", icon=":material/account_balance:", default=True),
        st.Page("views/people.py", title="한국인 연구자", icon=":material/groups:"),
    ],
    "탐색": [st.Page("app.py", title="검색 · 비교", icon=":material/search:")],
})
pg.run()
