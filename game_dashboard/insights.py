"""Conclusion text and combined insights for game and boosting pages."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .benchmarks import build_trend_summary, calculate_game_stats, select_window
from .config import BOOSTING_CATEGORY, MINIMUM_ENTITY_TRANSACTIONS
from .formatting import format_rupiah
from .recommendations import generate_candidates


def build_overpay_trend_summary(frame: pd.DataFrame, as_of: pd.Timestamp) -> str | None:
    """Compare the overpayment rate of the last 12 months with the 12 before.

    Args:
        frame: Game transactions containing ``Amount`` and ``Overpay``.
        as_of: Date used as the end of the comparison windows.

    Returns:
        A Markdown-ready sentence, or None when either window has too few
        transactions or no spend. The rate (overpay / spend) is used so the
        comparison stays fair when the earlier window is only partly covered.
    """
    recent = select_window(frame, as_of, 12)
    previous = select_window(frame, as_of, 24, 12)
    if len(recent) < MINIMUM_ENTITY_TRANSACTIONS or len(previous) < MINIMUM_ENTITY_TRANSACTIONS:
        return None
    recent_spend = recent["Amount"].sum()
    previous_spend = previous["Amount"].sum()
    if recent_spend <= 0 or previous_spend <= 0:
        return None

    recent_overpay = recent["Overpay"].sum()
    previous_overpay = previous["Overpay"].sum()
    recent_share = recent_overpay / recent_spend
    previous_share = previous_overpay / previous_spend
    difference = recent_share - previous_share
    if abs(difference) < 0.005:
        relation, verdict = "level with", "stable"
    elif difference < 0:
        relation, verdict = "down from", "improving"
    else:
        relation, verdict = "up from", "getting worse"
    return (
        f"Overpayment rate over the last 12 months was **{recent_share:.1%}** of spend, {relation} "
        f"{previous_share:.1%} in the preceding 12 months "
        f"({format_rupiah(recent_overpay)} vs {format_rupiah(previous_overpay)}); "
        f"purchasing habits are {verdict}."
    )


def build_game_insights(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Generate game-spending conclusions and ranked saving opportunities.

    Conclusions are ordered from the most to the least informative on the
    overview: total, net position, spending trend, largest recent overpayment,
    inactive shops with overpayment history, overpayment-rate trend, and hit
    rate last (it is already shown on the KPI cards).

    Args:
        frame: Cleaned game transactions.
        as_of: Date used for rolling 12-month comparisons.

    Returns:
        Conclusions, ranked recommendation dictionaries, and the number of
        transactions that could be compared with a target. Recommendations
        estimate avoidable spend in comparable historical transactions, not
        guaranteed future savings.
    """
    conclusions: list[str] = []
    stats = calculate_game_stats(frame, as_of)
    net = stats["overpay"] - stats["savings"]
    overpay_share = stats["overpay"] / stats["total"] if stats["total"] else 0

    conclusions.append(
        f"Total spending was **{format_rupiah(stats['total'])}** across {stats['count']} transactions. "
        f"Overpayment against the 70% reference was **{format_rupiah(stats['overpay'])}** "
        f"({overpay_share:.1%} of total spending)."
    )
    if net > 0:
        conclusions.append(
            f"The net position is **{format_rupiah(net)} in overpayments**; savings "
            f"({format_rupiah(stats['savings'])}) have not offset the excess spend."
        )
    else:
        conclusions.append(
            f"The net position is **{format_rupiah(-net)} in savings**; savings "
            f"({format_rupiah(stats['savings'])}) exceed overpayments."
        )

    trend = build_trend_summary(frame, as_of)
    if trend:
        conclusions.append(trend)

    cutoff = as_of - pd.DateOffset(months=12)
    recent_transactions = select_window(frame, as_of, 12)
    if not recent_transactions.empty and recent_transactions["Overpay"].max() > 0:
        largest_overpay = recent_transactions.loc[recent_transactions["Overpay"].idxmax()]
        conclusions.append(
            f"Largest overpayment in the last 12 months: **{largest_overpay['Item']}** at "
            f"{largest_overpay['Shop']} on {largest_overpay['Date']:%d %b %Y} "
            f"({format_rupiah(largest_overpay['Overpay'])} above the reference)."
        )

    shop_summary = frame.groupby("Shop").agg(
        transactions=("Amount", "size"),
        overpay=("Overpay", "sum"),
        last_used=("Date", "max"),
    ) if not frame.empty else pd.DataFrame()
    if not shop_summary.empty:
        inactive_shops = shop_summary[
            (shop_summary["overpay"] > 0) & (shop_summary["last_used"] <= cutoff)
        ]
        if not inactive_shops.empty:
            shop_names = ", ".join(f"**{shop}**" for shop in inactive_shops.index)
            conclusions.append(
                "Shops with a history of overpayment but no purchases in the last 12 months: "
                f"{shop_names}. Review their prices before using them again."
            )

    overpay_trend = build_overpay_trend_summary(frame, as_of)
    if overpay_trend:
        conclusions.append(overpay_trend)

    if pd.notna(stats["hit_rate"]):
        hit_rate_text = f"{stats['hit_rate']:.0%} of transactions with a reference price were at or below that price."
    else:
        hit_rate_text = "A hit rate is unavailable because reference prices are missing."
    if stats["recent_count"]:
        if pd.notna(stats["recent_hit_rate"]):
            hit_rate_text += (
                f" Over the last 12 months: {stats['recent_hit_rate']:.0%} "
                f"across {stats['recent_count']} transactions."
            )
        else:
            hit_rate_text += f" There were {stats['recent_count']} transactions in the last 12 months."
    conclusions.append(hit_rate_text)

    recommendations, evaluated = generate_candidates(frame, as_of, category="game")
    return conclusions, recommendations, evaluated


def build_boosting_insights(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Generate boosting conclusions and ranked provider-price opportunities.

    Args:
        frame: Cleaned boosting transactions.
        as_of: Date used for rolling 12-month comparisons.

    Returns:
        Conclusions, ranked recommendation dictionaries, and the number of
        transactions that could be compared with a target. Account-care rates
        are compared per day; other recurring services are compared per booking.
    """
    conclusions: list[str] = []
    total_spend = frame["Amount"].sum()
    transaction_count = len(frame)
    provider_summary = (
        frame.groupby("Provider")["Amount"]
        .agg(total="sum", transactions="size")
        .sort_values("total", ascending=False)
    )
    content_summary = (
        frame.groupby("Content")["Amount"]
        .agg(total="sum", transactions="size")
        .sort_values("total", ascending=False)
    )
    one_time_providers = int((provider_summary["transactions"] == 1).sum())
    average_spend = total_spend / transaction_count if transaction_count else 0

    conclusions.append(
        f"Total boosting spend was **{format_rupiah(total_spend)}** across {transaction_count} transactions "
        f"with {len(provider_summary)} providers; average spend was {format_rupiah(average_spend)} per transaction."
    )
    if not provider_summary.empty and total_spend > 0:
        leading_provider = provider_summary.index[0]
        leading_share = provider_summary["total"].iloc[0] / total_spend
        conclusions.append(
            f"Highest-spend provider: **{leading_provider}** ({leading_share:.0%} of total spend). "
            f"{one_time_providers} of {len(provider_summary)} providers were used only once."
        )
    if len(content_summary) >= 2 and total_spend > 0:
        first_share = content_summary["total"].iloc[0] / total_spend
        second_share = content_summary["total"].iloc[1] / total_spend
        conclusions.append(
            f"Highest-spend service types: **{content_summary.index[0]}** ({first_share:.0%}) and "
            f"**{content_summary.index[1]}** ({second_share:.0%})."
        )

    trend = build_trend_summary(frame, as_of)
    if trend:
        conclusions.append(trend)

    account_care = frame[
        (frame["Content"] == "Account care") & frame["Days"].notna() & (frame["Days"] > 0)
    ].copy()
    if not account_care.empty:
        account_care["Daily rate"] = account_care["Amount"] / account_care["Days"]
        median_rate = account_care["Daily rate"].median()
        conclusions.append(
            f"Account-care median daily rate: **{format_rupiah(median_rate)}** across "
            f"{len(account_care)} bookings. Provider comparisons require repeat bookings "
            "from at least two alternative providers."
        )

    recommendations, evaluated = generate_candidates(frame, as_of, category=BOOSTING_CATEGORY)
    return conclusions, recommendations, evaluated
