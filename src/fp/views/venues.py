"""Settings: which venues count as top (one shared choice for every page)."""

import streamlit as st

from fp import ui

ui.require_db()
st.title("Top 학회 설정")
st.caption("여기서 고른 학회만 'top 학회'로 셉니다. 국내 기관·한국인 연구자·검색 모든 페이지의 논문 수·순위·피인용·"
           "학문적 나이 추정에 적용됩니다.")
ui.top_venue_editor(expanded=True)
