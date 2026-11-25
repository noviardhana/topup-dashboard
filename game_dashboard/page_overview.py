"""Cross-category overview page."""

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
from .config import BOOSTING_CATEGORY, CATEGORY_COLOURS, SUMMARY_PERIOD_OPTIONS
from .formatting import format_rupiah
from .insights import build_boosting_insights, build_game_insights


def render_overview(
    data: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    monthly_budget: float = 0.0,
) -> None:
    """Render the cross-category overview, spending trends, and key insights.

    Args:
        data: Cleaned data frames keyed by game or service category.
        as_of: Latest transaction date across all available categories.
        monthly_budget: Optional monthly budget in rupiah; 0 hides the budget line.

    Returns:
        None. The overview is rendered directly in the Streamlit interface.
    """
    frames = [
        frame[["Date", "Amount"]].assign(Category=category)
        for category, frame in data.items()
    ]
    all_transactions = pd.concat(frames, ignore_index=True)
    total_spend = all_transactions["Amount"].sum()
    current_year = as_of.year
    year_to_date = all_transactions.loc[
        all_transactions["Date"].dt.year == current_year, "Amount"
    ].sum()
    previous_year_to_date = all_transactions.loc[
        (all_transactions["Date"].dt.year == current_year - 1)
        & (all_transactions["Date"] <= as_of - pd.DateOffset(years=1)),
        "Amount",
    ].sum()
    first_date = all_transactions["Date"].min()
    months_covered = (as_of.year - first_date.year) * 12 + as_of.month - first_date.month + 1
    recent_months = max(1, min(12, months_covered))
    previous_months = max(0, min(12, months_covered - 12))
    average_last_12 = select_window(all_transactions, as_of, 12)["Amount"].sum() / recent_months
    average_previous_12 = (
        select_window(all_transactions, as_of, 24, 12)["Amount"].sum() / previous_months
        if previous_months
        else float("nan")
    )

    insights = {
        category: (
            build_boosting_insights(frame, as_of)
            if category == BOOSTING_CATEGORY
            else build_game_insights(frame, as_of)
        )
        for category, frame in data.items()
    }
    estimated_total_saving = sum(
        recommendation["est"]
        for _, recommendations, _ in insights.values()
        for recommendation in recommendations
    )
    weighted_total_saving = sum(
        recommendation["est"] * recommendation.get("confidence", 0.0)
        for _, recommendations, _ in insights.values()
        for recommendation in recommendations
    )
    total_evaluated = sum(evaluated for _, _, evaluated in insights.values())
    recent_overpay = sum(
        select_window(frame, as_of, 12)["Overpay"].sum()
        for category, frame in data.items()
        if category != BOOSTING_CATEGORY
    )
    recent_top_up_spend = sum(
        select_window(frame, as_of, 12)["Amount"].sum()
        for category, frame in data.items()
        if category != BOOSTING_CATEGORY
    )
    boosting_total = data[BOOSTING_CATEGORY]["Amount"].sum() if BOOSTING_CATEGORY in data else 0

    columns = st.columns(3)
    date_range = f"{all_transactions['Date'].min():%b %Y} – {all_transactions['Date'].max():%b %Y} · {len(all_transactions)} transactions"
    render_kpi(columns[0], "Total spend", format_rupiah(total_spend), date_range)
    if previous_year_to_date > 0:
        year_change = year_to_date / previous_year_to_date - 1
        direction = "up" if year_change > 0 else "down" if year_change < 0 else "unchanged"
        render_kpi(
            columns[1],
            f"{current_year} year to date",
            format_rupiah(year_to_date),
            f"{direction} {abs(year_change):.0%} vs the comparable period in {current_year - 1} ({format_rupiah(previous_year_to_date)})",
            "bad" if year_change > 0 else "good",
        )
    else:
        render_kpi(columns[1], f"{current_year} year to date", format_rupiah(year_to_date))

    if pd.notna(average_previous_12) and average_previous_12 > 0:
        average_tone = "bad" if average_last_12 > average_previous_12 else "good"
    else:
        average_tone = ""
    render_kpi(
        columns[2],
        "Average monthly spend · 12 months",
        format_rupiah(average_last_12),
        f"Previous 12 months: {format_rupiah(average_previous_12)}",
        average_tone,
    )

    columns = st.columns(3)
    overpay_share = recent_overpay / recent_top_up_spend if recent_top_up_spend else 0
    render_kpi(
        columns[0],
        "Top-up overpayment · 12 months",
        format_rupiah(recent_overpay),
        f"{overpay_share:.1%} of top-up spend" if recent_top_up_spend else "No top-up transactions",
        "bad" if recent_overpay > 0 else "good",
    )
    boosting_share = boosting_total / total_spend if total_spend else 0
    render_kpi(
        columns[1],
        "Boosting share",
        f"{boosting_share:.0%}" if total_spend else "—",
        f"{format_rupiah(boosting_total)} lifetime spend",
    )
    render_kpi(
        columns[2],
        "Estimated avoidable spend · 12 months",
        format_rupiah(estimated_total_saving),
        f"Confidence-weighted: {format_rupiah(weighted_total_saving)}",
        "good" if estimated_total_saving > 0 else "",
    )

    st.subheader("Spending trends")
    left, right = st.columns([3, 1])
    selected_label = left.radio(
        "Time period",
        list(SUMMARY_PERIOD_OPTIONS),
        horizontal=True,
        key="overview_period",
        label_visibility="collapsed",
    )
    selected_period = SUMMARY_PERIOD_OPTIONS[selected_label]
    left.plotly_chart(
        create_spending_chart(
            all_transactions,
            selected_period,
            "Category",
            CATEGORY_COLOURS,
            monthly_budget=monthly_budget,
        ),
        key="overview_spending_chart",
        width="stretch",
        theme="streamlit",
    )

    category_share = all_transactions.groupby("Category")["Amount"].sum().reset_index()
    donut = px.pie(
        category_share,
        names="Category",
        values="Amount",
        hole=0.62,
        color="Category",
        color_discrete_map=CATEGORY_COLOURS,
    )
    donut.update_traces(
        textinfo="percent",
        hovertemplate="%{label}: Rp %{value:,.0f}<extra></extra>",
    )
    donut.update_layout(
        height=380,
        margin={"l": 8, "r": 8, "t": 12, "b": 40},
        legend={"orientation": "h", "y": -0.05, "x": 0.5, "xanchor": "center"},
        separators=",.",
    )
    right.plotly_chart(donut, key="overview_category_share", width="stretch", theme="streamlit")

    st.subheader("Key takeaways and recommendations")
    if total_evaluated <= 0:
        headline = "Avoidable spend could not be assessed yet: no recent transaction has a usable benchmark."
    elif estimated_total_saving > 0:
        saving_by_category = {
            category: sum(recommendation["est"] for recommendation in recommendations)
            for category, (_, recommendations, _) in insights.items()
        }
        top_category = max(saving_by_category, key=saving_by_category.get)
        top_share = saving_by_category[top_category] / estimated_total_saving
        headline = (
            f"**{top_category}** accounts for {top_share:.0%} of the estimated avoidable spend "
            f"({format_rupiah(saving_by_category[top_category])} of {format_rupiah(estimated_total_saving)})."
        )
    else:
        headline = "No avoidable spend detected: recent prices are at or below their benchmarks."
    st.markdown(headline)
    render_methodology()

    conclusion_column, recommendation_column = st.columns(2)
    conclusion_column.markdown("#### Conclusions")
    recommendation_column.markdown("#### Next cost-saving actions")
    for category, (conclusions, recommendations, evaluated) in insights.items():
        with conclusion_column:
            st.markdown(f"**{category}**")
            render_bullets(conclusions[1:4])
        with recommendation_column:
            st.markdown(f"**{category}**")
            render_recommendations(recommendations, evaluated, limit=3, expand_rest=False)

    st.caption(
        "The top-up reference is taken from the worksheet's '70% Value' column. "
        "Estimates are historical comparisons, not guaranteed future savings; "
        "open Methodology for how targets and evidence labels are determined."
    )
