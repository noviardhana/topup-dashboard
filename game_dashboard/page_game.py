"""Detail page for one game."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from .benchmarks import calculate_game_stats
from .charts import create_balance_chart, create_spending_chart
from .components import (
    render_bullets,
    render_kpi,
    render_methodology,
    render_recommendations,
)
from .config import OVERPAY_COLOUR, PERIOD_OPTIONS, SAVINGS_COLOUR
from .formatting import format_rupiah, format_table
from .insights import build_game_insights


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
