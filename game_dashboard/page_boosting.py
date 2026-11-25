"""Detail page for boosting services."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from .benchmarks import select_window
from .charts import create_spending_chart
from .components import (
    render_bullets,
    render_kpi,
    render_methodology,
    render_recommendations,
)
from .config import BOOSTING_CATEGORY, CATEGORY_COLOURS, COLOUR_PALETTE, PERIOD_OPTIONS
from .formatting import format_rupiah, format_table
from .insights import build_boosting_insights


def render_boosting_page(frame: pd.DataFrame, as_of: pd.Timestamp) -> None:
    """Render boosting-service metrics, provider comparisons, and insights.

    Args:
        frame: Cleaned boosting transactions.
        as_of: Date used for rolling 12-month statistics.

    Returns:
        None. The boosting analysis page is rendered in the Streamlit interface.
    """
    total_spend = frame["Amount"].sum()
    transaction_count = len(frame)
    recent_transactions = select_window(frame, as_of, 12)
    account_care = frame[
        (frame["Content"] == "Account care") & frame["Days"].notna() & (frame["Days"] > 0)
    ].copy()
    account_care["Daily rate"] = account_care["Amount"] / account_care["Days"]
    provider_count = frame["Provider"].nunique()
    one_time_count = int((frame["Provider"].value_counts() == 1).sum())
    average_spend = total_spend / transaction_count if transaction_count else 0

    columns = st.columns(5)
    render_kpi(columns[0], "Total boosting spend", format_rupiah(total_spend), f"{transaction_count} transactions")
    render_kpi(columns[1], "Unique providers", str(provider_count), f"{one_time_count} used once")
    render_kpi(
        columns[2],
        "Average per transaction",
        format_rupiah(average_spend),
        f"Median: {format_rupiah(frame['Amount'].median())}",
    )
    render_kpi(
        columns[3],
        "Last 12 months",
        format_rupiah(recent_transactions["Amount"].sum()),
        f"{len(recent_transactions)} transactions",
    )
    render_kpi(
        columns[4],
        "Account care · median daily rate",
        format_rupiah(account_care["Daily rate"].median()) if not account_care.empty else "—",
        f"{len(account_care)} bookings" if not account_care.empty else "No account-care bookings",
    )

    st.subheader("Spending trends")
    selected_label = st.radio(
        "Time period",
        list(PERIOD_OPTIONS),
        horizontal=True,
        key="boosting_period",
        label_visibility="collapsed",
    )
    selected_period = PERIOD_OPTIONS[selected_label]
    spending_column, provider_column = st.columns([3, 2])
    spending_column.markdown("**Spend by service type**")
    spending_column.plotly_chart(
        create_spending_chart(frame, selected_period, "Content"),
        key="boosting_spending",
        width="stretch",
        theme="streamlit",
    )

    provider_summary = (
        frame.groupby("Provider")["Amount"]
        .agg(Total_spend="sum", Transactions="size")
        .sort_values("Total_spend")
        .tail(10)
        .reset_index()
    )
    provider_figure = px.bar(
        provider_summary,
        x="Total_spend",
        y="Provider",
        orientation="h",
        text=provider_summary["Transactions"].map(lambda value: f"{value}x"),
        color_discrete_sequence=[CATEGORY_COLOURS[BOOSTING_CATEGORY]],
    )
    provider_figure.update_layout(
        height=380,
        margin={"l": 8, "r": 8, "t": 12, "b": 8},
        xaxis_title=None,
        yaxis_title=None,
        separators=",.",
    )
    provider_figure.update_xaxes(tickprefix="Rp ", tickformat=",.0f")
    provider_column.markdown("**10 highest-spend providers · transaction count at bar end**")
    provider_column.plotly_chart(
        provider_figure,
        key="boosting_top_providers",
        width="stretch",
        theme="streamlit",
    )

    st.subheader("Pricing by service type")
    pricing_column, daily_rate_column = st.columns([3, 2])
    content_summary = (
        frame.groupby("Content")["Amount"]
        .agg(Total_spend="sum", Transactions="size", Median="median", Average="mean", Highest="max")
        .sort_values("Total_spend", ascending=False)
        .reset_index()
    )
    pricing_column.dataframe(
        format_table(content_summary, ["Total_spend", "Median", "Average", "Highest"]),
        hide_index=True,
        width="stretch",
        column_config={"Average": st.column_config.Column("Average spend")},
    )
    if not account_care.empty:
        daily_rate_figure = px.scatter(
            account_care,
            x="Date",
            y="Daily rate",
            color="Provider",
            size="Days",
            size_max=22,
            color_discrete_sequence=COLOUR_PALETTE,
        )
        daily_rate_figure.update_layout(
            height=320,
            margin={"l": 8, "r": 8, "t": 12, "b": 8},
            xaxis_title=None,
            yaxis_title=None,
            legend={"orientation": "h", "y": -0.25, "title_text": ""},
            separators=",.",
        )
        daily_rate_figure.update_yaxes(tickprefix="Rp ", tickformat=",.0f")
        daily_rate_column.markdown("**Account-care daily rate · bubble size represents days**")
        daily_rate_column.plotly_chart(
            daily_rate_figure,
            key="account_care_daily_rate",
            width="stretch",
            theme="streamlit",
        )

    conclusions, recommendations, evaluated = build_boosting_insights(frame, as_of)
    st.subheader("Conclusions and recommendations")
    render_methodology()
    conclusion_column, recommendation_column = st.columns(2)
    conclusion_column.markdown("#### Conclusions")
    with conclusion_column:
        render_bullets(conclusions)
    recommendation_column.markdown("#### Cost-saving actions")
    with recommendation_column:
        render_recommendations(recommendations, evaluated)

    with st.expander("Transaction details"):
        provider_filter_column, content_filter_column = st.columns(2)
        selected_providers = provider_filter_column.multiselect(
            "Provider", sorted(frame["Provider"].dropna().unique()), key="boosting_provider_filter"
        )
        selected_content = content_filter_column.multiselect(
            "Service type", sorted(frame["Content"].dropna().unique()), key="boosting_content_filter"
        )
        mask = pd.Series(True, index=frame.index)
        if selected_providers:
            mask &= frame["Provider"].isin(selected_providers)
        if selected_content:
            mask &= frame["Content"].isin(selected_content)
        transactions = frame[mask].drop(columns=["No"], errors="ignore")
        st.dataframe(
            format_table(transactions, ["Amount"]),
            hide_index=True,
            width="stretch",
        )
        st.download_button(
            "Download CSV",
            data=transactions.to_csv(index=False).encode("utf-8"),
            file_name="boosting_transactions.csv",
            mime="text/csv",
            key="download_boosting",
        )
