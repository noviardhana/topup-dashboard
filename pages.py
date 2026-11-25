"""Dashboard pages: overview, game detail, and boosting detail."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from .charts import create_balance_chart, create_spending_chart
from .components import (
    render_bullets,
    render_kpi,
    render_methodology,
    render_recommendations,
)
from .config import (
    BOOSTING_CATEGORY,
    CATEGORY_COLOURS,
    COLOUR_PALETTE,
    OVERPAY_COLOUR,
    PERIOD_OPTIONS,
    SAVINGS_COLOUR,
    SUMMARY_PERIOD_OPTIONS,
)
from .formatting import format_rupiah, format_table
from .insights import (
    build_boosting_insights,
    build_game_insights,
    calculate_game_stats,
    select_window,
)


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


def render_game_page(frame: pd.DataFrame, game_name: str, as_of: pd.Timestamp) -> None:
    """Render detailed metrics, charts, tables, and insights for one game.

    Args:
        frame: Cleaned transactions for the selected game.
        game_name: Game label used to create unique Streamlit widget keys.
        as_of: Date used for rolling 12-month statistics.

    Returns:
        None. The game analysis page is rendered in the Streamlit interface.
    """
    stats = calculate_game_stats(frame, as_of)
    net = stats["overpay"] - stats["savings"]
    patch_average = frame.groupby("Version")["Amount"].sum().mean() if not frame.empty else 0
    overpay_share = stats["overpay"] / stats["total"] if stats["total"] else 0
    hit_rate = f"{stats['hit_rate']:.0%}" if pd.notna(stats["hit_rate"]) else "—"
    recent_hit_rate = (
        f"Last 12 months: {stats['recent_hit_rate']:.0%}"
        if pd.notna(stats["recent_hit_rate"]) and stats["recent_count"]
        else "No comparable transactions"
    )

    columns = st.columns(6)
    render_kpi(columns[0], "Total spend", format_rupiah(stats["total"]), f"{stats['count']} transactions")
    render_kpi(columns[1], "Average per patch", format_rupiah(patch_average), f"{frame['Version'].nunique()} patches")
    render_kpi(columns[2], "Overpayment", format_rupiah(stats["overpay"]), f"{overpay_share:.1%} of spend", "bad")
    render_kpi(columns[3], "Savings", format_rupiah(stats["savings"]), "Below the reference price", "good")
    net_label = "Overpayment" if net > 0 else "Savings"
    render_kpi(
        columns[4],
        "Net position",
        f"{net_label}: {format_rupiah(abs(net))}",
        "Overpayment less savings",
        "bad" if net > 0 else "good",
    )
    render_kpi(columns[5], "Hit rate · at or below reference", hit_rate, recent_hit_rate)

    st.subheader("Spending trends")
    selected_label = st.radio(
        "Time period",
        list(PERIOD_OPTIONS),
        horizontal=True,
        key=f"period_{game_name}",
        label_visibility="collapsed",
    )
    selected_period = PERIOD_OPTIONS[selected_label]
    left, right = st.columns([3, 2])
    left.markdown("**Spend by item**")
    left.plotly_chart(
        create_spending_chart(frame, selected_period, "Item"),
        key=f"spending_{game_name}",
        width="stretch",
        theme="streamlit",
    )
    right.markdown("**Overpayment vs savings**")
    right.plotly_chart(
        create_balance_chart(frame, selected_period),
        key=f"balance_{game_name}",
        width="stretch",
        theme="streamlit",
    )

    st.subheader("Shops and items")
    shop_column, item_column = st.columns([2, 3])
    shop_summary = (
        frame.groupby("Shop")
        .agg(
            Transactions=("Amount", "size"),
            Overpayment=("Overpay", "sum"),
            Savings=("Savings", "sum"),
        )
        .reset_index()
    )
    shop_summary["Net"] = shop_summary["Overpayment"] - shop_summary["Savings"]
    shop_summary["Status"] = shop_summary["Net"].map(
        lambda value: "Overpayment" if value > 0 else "Savings"
    )
    shop_summary = shop_summary.sort_values("Net")
    shop_figure = px.bar(
        shop_summary,
        x="Net",
        y="Shop",
        orientation="h",
        color="Status",
        color_discrete_map={"Overpayment": OVERPAY_COLOUR, "Savings": SAVINGS_COLOUR},
        text=shop_summary["Transactions"].map(lambda value: f"{value}x"),
    )
    shop_figure.update_layout(
        height=max(300, 34 * len(shop_summary) + 80),
        margin={"l": 8, "r": 8, "t": 12, "b": 8},
        legend={"orientation": "h", "y": -0.15, "title_text": ""},
        xaxis_title="Net position (+ overpayment, − savings)",
        yaxis_title=None,
        separators=",.",
    )
    shop_figure.update_xaxes(tickprefix="Rp ", tickformat=",.0f")
    shop_column.markdown("**Net position by shop**")
    shop_column.plotly_chart(
        shop_figure,
        key=f"shops_{game_name}",
        width="stretch",
        theme="streamlit",
    )

    item_summary = (
        frame.groupby("Item")
        .agg(
            Transactions=("Amount", "size"),
            Total_spend=("Amount", "sum"),
            Average_spend=("Amount", "mean"),
            Reference=("Reference", "last"),
            Hit_rate=("Overpay", lambda values: (values.eq(0).sum() / values.notna().sum() * 100) if values.notna().any() else float("nan")),
            Overpayment=("Overpay", "sum"),
            Savings=("Savings", "sum"),
        )
        .reset_index()
        .sort_values("Total_spend", ascending=False)
    )
    item_summary = item_summary.rename(
        columns={"Total_spend": "Total spend", "Average_spend": "Average spend", "Hit_rate": "Hit rate"}
    )
    item_column.markdown("**Item summary**")
    item_column.dataframe(
        format_table(item_summary, ["Total spend", "Average spend", "Reference", "Overpayment", "Savings"]),
        hide_index=True,
        width="stretch",
        column_config={
            "Hit rate": st.column_config.ProgressColumn("Hit rate", min_value=0, max_value=100, format="%.0f%%")
        },
    )

    st.subheader("Promotions and overpayments")
    promotion_column, overpay_column = st.columns(2)
    promotions = frame[
        frame["Reference"].notna()
        & (frame["Reference"] > 0)
        & (frame["Savings"] >= 0.2 * frame["Reference"])
    ][["Date", "Item", "Shop", "Amount", "Savings"]]
    promotion_column.markdown("**Promotional purchases · at least 20% below reference**")
    promotion_column.dataframe(
        format_table(promotions.sort_values("Date", ascending=False), ["Amount", "Savings"]),
        hide_index=True,
        width="stretch",
    )
    largest_overpayments = frame[frame["Overpay"] > 0].nlargest(10, "Overpay")[
        ["Date", "Item", "Shop", "Amount", "Overpay"]
    ]
    overpay_column.markdown("**10 largest overpayments**")
    overpay_column.dataframe(
        format_table(largest_overpayments, ["Amount", "Overpay"]),
        hide_index=True,
        width="stretch",
    )

    conclusions, recommendations, evaluated = build_game_insights(frame, as_of)
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
        shop_filter_column, item_filter_column = st.columns(2)
        selected_shops = shop_filter_column.multiselect(
            "Shop", sorted(frame["Shop"].dropna().unique()), key=f"shop_filter_{game_name}"
        )
        selected_items = item_filter_column.multiselect(
            "Item", sorted(frame["Item"].dropna().unique()), key=f"item_filter_{game_name}"
        )
        mask = pd.Series(True, index=frame.index)
        if selected_shops:
            mask &= frame["Shop"].isin(selected_shops)
        if selected_items:
            mask &= frame["Item"].isin(selected_items)
        transactions = frame[mask].drop(columns=["No"], errors="ignore")
        st.dataframe(
            format_table(
                transactions,
                ["Amount", "Reference", "Variance", "Overpay", "Savings"],
            ),
            hide_index=True,
            width="stretch",
        )
        st.download_button(
            "Download CSV",
            data=transactions.to_csv(index=False).encode("utf-8"),
            file_name=f"{game_name.lower()}_transactions.csv",
            mime="text/csv",
            key=f"download_{game_name}",
        )


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
