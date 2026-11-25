"""Game Spending Dashboard

A Streamlit dashboard for analysing spending across Genshin, Honkai: Star Rail,
 and game-boosting services. Transaction data is read from the ``Hoyoverse``
Google Sheets worksheet, cleaned into consistent data frames, and presented
through summary metrics, spending trends, shop and provider comparisons, and
practical cost-saving insights. Its recommendation engine compares like-for-like
items and services, estimates avoidable spend over the last 12 months, and ranks
actions using estimated impact, evidence confidence, and data recency.

Recommendation methodology
--------------------------
Price benchmarks use up to 24 months of history. A peer comparison is only
available when at least two alternative shops or providers each have at least
two transactions. Game purchases use the lower of the worksheet's 70% Value
and the eligible peer median. Account-care bookings are compared by daily rate.
Reported estimates are historical comparisons, not guaranteed future savings.

Configuration
-------------
The application expects ``sheet_id`` and ``gcp_service_account`` to be defined
in Streamlit secrets. The source worksheet must retain its existing column
headers, including ``70% Value``, ``Toko``, and ``Keterangan`` where applicable.
Optionally, define ``app_password`` in Streamlit secrets to require a password
before any data is loaded; when it is absent the dashboard is not gated.

Run with::

    streamlit run app.py
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from game_dashboard.auth import check_password
from game_dashboard.config import BOOSTING_CATEGORY, DASHBOARD_CSS, WORKSHEET_NAME
from game_dashboard.data import load_sheet_data
from game_dashboard.pages import render_boosting_page, render_game_page, render_overview


def main() -> None:
    """Configure the Streamlit page, load data, and render dashboard tabs.

    Returns:
        None. This function runs the complete Streamlit application.
    """
    st.set_page_config(
        page_title="Game Spending Dashboard",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    if not check_password():
        st.stop()

    st.sidebar.button(
        "Refresh data",
        on_click=st.cache_data.clear,
        help="Clear the cached Google Sheets data and reload the dashboard.",
        width="stretch",
    )

    try:
        all_data = load_sheet_data()
    except Exception as error:  # Display a useful message while retaining diagnostics.
        st.error("Unable to load Google Sheets data. Check the sheet ID, service-account secrets, and worksheet access.")
        with st.expander("Technical details"):
            st.exception(error)
        st.stop()

    if not all_data:
        st.warning(
            f"No recognised transaction tables were found in the '{WORKSHEET_NAME}' worksheet. "
            "Each table must start with 'No' or 'No.' in column A and 'Date' in column B."
        )
        st.stop()

    available_dates = [frame["Date"] for frame in all_data.values() if not frame.empty]
    if not available_dates:
        st.warning("The recognised tables do not contain any valid dated transactions.")
        st.stop()
    earliest = min(dates.min() for dates in available_dates).date()
    latest = max(dates.max() for dates in available_dates).date()

    selected_range = st.sidebar.date_input(
        "Date range",
        value=(earliest, latest),
        min_value=earliest,
        max_value=latest,
        key="date_range",
        help="Applies to every tab. Rolling 12-month figures are measured back from the end date.",
    )
    if not isinstance(selected_range, (tuple, list)) or len(selected_range) != 2:
        st.info("Select both a start date and an end date in the sidebar.")
        st.stop()
    range_start = pd.Timestamp(selected_range[0])
    range_end = pd.Timestamp(selected_range[1]) + pd.Timedelta(days=1)

    monthly_budget = st.sidebar.number_input(
        "Monthly budget (Rp)",
        min_value=0,
        value=0,
        step=50_000,
        key="monthly_budget",
        help="Draws a budget line on the overview spending chart. Set to 0 to hide it.",
    )

    data = {
        category: frame[(frame["Date"] >= range_start) & (frame["Date"] < range_end)].reset_index(drop=True)
        for category, frame in all_data.items()
    }
    data = {category: frame for category, frame in data.items() if not frame.empty}
    if not data:
        st.warning("No transactions fall within the selected date range.")
        st.stop()
    as_of = max(frame["Date"].max() for frame in data.values())

    category_list = ", ".join(data)
    safe_category_list = html.escape(category_list)
    st.markdown(
        '<div class="dashboard-header">'
        '<div class="dashboard-eyebrow">Personal finance · Transaction analysis</div>'
        '<div class="dashboard-title">Game spending dashboard</div>'
        f'<div class="dashboard-subtitle">Latest transaction: {as_of:%d %B %Y} · '
        f'Data available: {safe_category_list}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    tab_labels = ["Overview", *data.keys()]
    tabs = st.tabs(tab_labels)
    with tabs[0]:
        render_overview(data, as_of, float(monthly_budget))

    for tab, (category, frame) in zip(tabs[1:], data.items()):
        with tab:
            if category == BOOSTING_CATEGORY:
                render_boosting_page(frame, as_of)
            else:
                render_game_page(frame, category, as_of)


if __name__ == "__main__":
    main()
