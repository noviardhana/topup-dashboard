"""Period grouping and Plotly chart construction."""

from __future__ import annotations

import plotly.graph_objects as go
import pandas as pd
import plotly.express as px

from .config import (
    BUDGET_PERIOD_SCALE,
    COLOUR_PALETTE,
    LINE_COLOUR,
    OVERPAY_COLOUR,
    SAVINGS_COLOUR,
)
from .formatting import version_sort_key


def add_period(frame: pd.DataFrame, period: str) -> tuple[pd.DataFrame, pd.Index]:
    """Assign a period label to each transaction and return its display order.

    Args:
        frame: Transactions containing a ``Date`` column and, for patches,
            a ``Version`` column.
        period: One of ``Month``, ``Quarter``, ``Year``, or ``Patch``.

    Returns:
        A copied data frame with a ``_period`` column and an ordered index.
    """
    period_frame = frame.copy()
    period_frame = period_frame.dropna(subset=["Date"])
    if period_frame.empty:
        period_frame["_period"] = pd.Series(dtype="object")
        return period_frame, pd.Index([])

    if period == "Month":
        period_frame["_period"] = period_frame["Date"].dt.to_period("M").dt.to_timestamp()
        order = pd.date_range(period_frame["_period"].min(), period_frame["_period"].max(), freq="MS")
    elif period == "Quarter":
        quarter = period_frame["Date"].dt.to_period("Q")
        period_frame["_period"] = quarter.map(lambda item: f"{item.year} Q{item.quarter}")
        order = pd.Index(
            [f"{item.year} Q{item.quarter}" for item in pd.period_range(quarter.min(), quarter.max(), freq="Q")]
        )
    elif period == "Year":
        period_frame["_period"] = period_frame["Date"].dt.year.astype(str)
        order = pd.Index(sorted(period_frame["_period"].unique()))
    else:
        period_frame["_period"] = (
            period_frame["Version"].astype(str).replace("", "Unspecified")
        )
        order = pd.Index(sorted(period_frame["_period"].unique(), key=version_sort_key))
    return period_frame, order


def style_figure(figure: go.Figure, period: str, order: pd.Index, height: int = 380) -> go.Figure:
    """Apply consistent layout, currency formatting, and period ordering.

    Args:
        figure: Plotly figure to style.
        period: Period identifier used to determine the x-axis format.
        order: Ordered period labels returned by ``add_period``.
        height: Figure height in pixels.

    Returns:
        The styled Plotly figure.
    """
    figure.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 16, "b": 8},
        legend={"orientation": "h", "y": -0.22, "x": 0, "title_text": ""},
        hovermode="x unified",
        hoverlabel={"font_size": 12},
        bargap=0.3,
        xaxis_title=None,
        yaxis_title=None,
        separators=",.",
    )
    figure.update_yaxes(tickprefix="Rp ", tickformat=",.0f", gridcolor="rgba(140, 150, 165, 0.18)")
    figure.update_xaxes(showgrid=False)
    if period == "Month":
        figure.update_xaxes(tickformat="%b %Y", hoverformat="%b %Y")
    else:
        figure.update_xaxes(type="category", categoryorder="array", categoryarray=[str(item) for item in order])
    return figure


def create_spending_chart(
    frame: pd.DataFrame,
    period: str,
    group_column: str,
    colour_map: dict[str, str] | None = None,
    monthly_budget: float | None = None,
) -> go.Figure:
    """Create a stacked spending chart with a three-month average when relevant.

    Args:
        frame: Transaction data containing ``Date``, ``Amount``, and the group
            column.
        period: Period identifier: ``Month``, ``Quarter``, ``Year``, or ``Patch``.
        group_column: Column used to split spending into categories.
        colour_map: Optional mapping of group labels to colours.
        monthly_budget: Optional monthly budget in rupiah. When positive, a
            dashed budget line is drawn, scaled to the selected period.

    Returns:
        A styled Plotly bar chart.
    """
    period_frame, order = add_period(frame, period)
    pivot = (
        period_frame.pivot_table(
            index="_period",
            columns=group_column,
            values="Amount",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(order, fill_value=0)
    )
    pivot.index.name = "Period"
    pivot.columns.name = None
    long_frame = pivot.reset_index().melt(id_vars="Period", var_name=group_column, value_name="Amount")
    # Zero-value rows are hidden so the tooltip lists only items with spending.
    long_frame["Amount"] = long_frame["Amount"].where(long_frame["Amount"] != 0)

    figure = px.bar(
        long_frame,
        x="Period",
        y="Amount",
        color=group_column,
        color_discrete_map=colour_map,
        color_discrete_sequence=COLOUR_PALETTE,
    )
    figure.update_traces(hovertemplate="%{fullData.name}: Rp %{y:,.0f}<extra></extra>")

    total_by_period = pivot.sum(axis=1)
    if not pivot.empty:
        # Invisible trace whose only purpose is to show the period total in the tooltip.
        figure.add_scatter(
            x=total_by_period.index,
            y=total_by_period.values,
            mode="markers",
            name="Total spending",
            showlegend=False,
            marker={"size": 1, "opacity": 0},
            hovertemplate="<b>Total spending: Rp %{y:,.0f}</b><extra></extra>",
        )

    if period == "Month" and not pivot.empty:
        figure.add_scatter(
            x=total_by_period.index,
            y=total_by_period.rolling(3, min_periods=1).mean(),
            mode="lines",
            name="Three-month average",
            line={"color": LINE_COLOUR, "width": 2, "dash": "dot"},
            hovertemplate="Three-month average: Rp %{y:,.0f}<extra></extra>",
        )
    budget_scale = BUDGET_PERIOD_SCALE.get(period)
    if monthly_budget and monthly_budget > 0 and budget_scale and not pivot.empty:
        # A trace (not add_hline) keeps the line inside the autoscaled y-range.
        budget_value = monthly_budget * budget_scale
        figure.add_scatter(
            x=total_by_period.index,
            y=[budget_value] * len(total_by_period),
            mode="lines",
            name="Budget",
            line={"color": OVERPAY_COLOUR, "width": 2, "dash": "dash"},
            hovertemplate="Budget: Rp %{y:,.0f}<extra></extra>",
        )
    return style_figure(figure, period, order)


def create_balance_chart(frame: pd.DataFrame, period: str) -> go.Figure:
    """Compare overpayments with savings and plot the cumulative net balance.

    Args:
        frame: Game transactions containing ``Overpay`` and ``Savings``.
        period: Period identifier used to group the transactions.

    Returns:
        A styled Plotly chart with positive and negative bars plus a net line.
    """
    period_frame, order = add_period(frame, period)
    grouped = (
        period_frame.groupby("_period")[["Overpay", "Savings"]]
        .sum()
        .reindex(order, fill_value=0)
    )
    cumulative_net = (grouped["Overpay"] - grouped["Savings"]).cumsum()

    figure = go.Figure()
    figure.add_bar(
        x=grouped.index,
        y=grouped["Overpay"],
        name="Overpay",
        marker_color=OVERPAY_COLOUR,
        hovertemplate="Overpay: Rp %{y:,.0f}<extra></extra>",
    )
    figure.add_bar(
        x=grouped.index,
        y=-grouped["Savings"],
        name="Savings",
        marker_color=SAVINGS_COLOUR,
        customdata=grouped["Savings"],
        hovertemplate="Savings: Rp %{customdata:,.0f}<extra></extra>",
    )
    figure.add_scatter(
        x=grouped.index,
        y=cumulative_net,
        name="Cumulative net",
        mode="lines+markers",
        yaxis="y2",
        line={"color": LINE_COLOUR, "width": 2},
        hovertemplate="Cumulative net: Rp %{y:,.0f}<extra></extra>",
    )
    bar_peak = grouped[["Overpay", "Savings"]].max().max()
    bar_peak = 0.0 if pd.isna(bar_peak) else float(bar_peak)
    net_peak = cumulative_net.abs().max()
    net_peak = 0.0 if pd.isna(net_peak) else float(net_peak)
    # Symmetric ranges keep the zero line of both axes aligned.
    bar_limit = max(bar_peak, 1.0) * 1.1
    net_limit = max(net_peak, 1.0) * 1.1
    figure.update_layout(
        barmode="relative",
        yaxis={"range": [-bar_limit, bar_limit]},
        yaxis2={
            "overlaying": "y",
            "side": "right",
            "range": [-net_limit, net_limit],
            "tickprefix": "Rp ",
            "tickformat": ",.0f",
            "showgrid": False,
        },
    )
    return style_figure(figure, period, order)
