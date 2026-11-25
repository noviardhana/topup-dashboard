"""Recommendation candidates, scoring, and ranking.

Recommendation methodology
--------------------------
Price benchmarks use up to 24 months of history. A peer comparison is only
available when at least two alternative shops or providers each have at least
two transactions. Game purchases use the lower of the worksheet's 70% Value
and the eligible peer median. Account-care bookings are compared by daily rate.
Reported estimates are historical comparisons, not guaranteed future savings.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .benchmarks import (
    _calculate_confidence,
    calculate_benchmarks,
    calculate_savings,
    select_window,
)
from .config import (
    BENCHMARK_LOOKBACK_MONTHS,
    BOOSTING_CATEGORY,
    HIGH_PRIORITY_RELATIVE_THRESHOLD,
    MATERIAL_ESTIMATE_SHARE,
    MEDIUM_PRIORITY_RELATIVE_THRESHOLD,
    MINIMUM_ENTITY_TRANSACTIONS,
    MINIMUM_PEER_ENTITIES,
    RECENCY_HALF_LIFE_DAYS,
    RECURRING_SERVICES,
)
from .formatting import format_rupiah


def score_recommendations(
    recommendations: list[dict[str, Any]],
    as_of: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Apply confidence and recency weights to each recommendation's impact.

    Args:
        recommendations: Candidate recommendations with estimated spend,
            confidence, and an optional ``last_date``.
        as_of: Date used to measure how recent the supporting evidence is.

    Returns:
        New recommendation dictionaries containing ``recency``,
        ``priority_score``, and the combined evidence-weighted score.
    """
    scored = []
    for recommendation in recommendations:
        item = dict(recommendation)
        last_date = item.get("last_date")
        if last_date is not None and not pd.isna(last_date):
            age_days = max(0, (as_of - pd.Timestamp(last_date)).days)
            recency = 2 ** (-age_days / RECENCY_HALF_LIFE_DAYS)
        else:
            recency = 0.5

        confidence = float(item.get("confidence", 0.5))
        estimated_spend = max(0.0, float(item.get("est", 0.0)))
        item["recency"] = round(recency, 3)
        item["priority_score"] = estimated_spend * confidence * recency
        scored.append(item)
    return scored


def rank_recommendations(
    recommendations: list[dict[str, Any]],
    minimum_estimate: float = 0.0,
) -> list[dict[str, Any]]:
    """Rank candidates and assign relative High, Medium, or Monitor labels.

    Args:
        recommendations: Scored recommendation dictionaries.
        minimum_estimate: Estimates below this amount are always labelled
            Monitor, so a small saving is never ranked High just because it
            is the largest candidate.

    Returns:
        Recommendations sorted by weighted impact, with a relative priority
        label. Priority labels compare candidates within the same dashboard tab.
    """
    ranked = sorted(
        recommendations,
        key=lambda item: (
            item.get("priority_score", 0),
            item.get("confidence", 0),
            item.get("est", 0),
        ),
        reverse=True,
    )
    maximum_score = max((item.get("priority_score", 0) for item in ranked), default=0)
    for item in ranked:
        score = item.get("priority_score", 0)
        relative_score = score / maximum_score if maximum_score > 0 else 0
        is_material = item.get("est", 0) >= minimum_estimate
        if score > 0 and is_material and relative_score >= HIGH_PRIORITY_RELATIVE_THRESHOLD:
            item["priority"] = "High"
        elif score > 0 and is_material and relative_score >= MEDIUM_PRIORITY_RELATIVE_THRESHOLD:
            item["priority"] = "Medium"
        else:
            item["priority"] = "Monitor"
    return ranked


def _alternative_text(opportunity: pd.Series, unit_label: str = "") -> str:
    """Name the cheapest eligible alternative when it beats the observed price.

    Args:
        opportunity: One row from :func:`calculate_savings`.
        unit_label: Optional unit appended to the price, such as `` per day``.

    Returns:
        A sentence naming the alternative and its median, or an empty string
        when no cheaper eligible alternative exists.
    """
    peer = opportunity.get("cheapest_peer")
    peer_median = opportunity.get("cheapest_peer_median")
    observed = opportunity.get("observed_median")
    if peer is None or pd.isna(peer) or pd.isna(peer_median) or pd.isna(observed):
        return ""
    if peer_median >= observed:
        return ""
    return f" Alternative: **{peer}** (median {format_rupiah(peer_median)}{unit_label})."


def _evaluated_total(opportunities: pd.DataFrame) -> int:
    """Return the number of transactions that were compared with a target."""
    if opportunities.empty:
        return 0
    return int(pd.to_numeric(opportunities["evaluated_transactions"], errors="coerce").fillna(0).sum())


def generate_candidates(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    category: str,
) -> tuple[list[dict[str, Any]], int]:
    """Generate comparable-price opportunities for game or boosting spend.

    Game purchases are assessed against the lower of the source reference price
    and the median price at eligible alternative shops. Boosting services are
    compared with repeat providers offering the same service; account care is
    normalised to a daily rate before comparison.

    Args:
        frame: Cleaned transactions for one game or the Boosting category.
        as_of: Reference date for look-back and recency calculations.
        category: Game name or the ``Boosting`` category label.

    Returns:
        A tuple of ranked recommendation dictionaries (with estimated avoidable
        spend, confidence, recency, priority score, and supporting evidence) and
        the number of recent transactions that could be compared with a target.
    """
    candidates: list[dict[str, Any]] = []
    evaluated = 0

    if frame.empty:
        return candidates, evaluated

    if category != BOOSTING_CATEGORY:
        benchmarks = calculate_benchmarks(
            frame, "Item", "Shop", "Amount", as_of,
            lookback_months=BENCHMARK_LOOKBACK_MONTHS,
            minimum_entity_transactions=MINIMUM_ENTITY_TRANSACTIONS,
            minimum_peer_entities=MINIMUM_PEER_ENTITIES,
        )
        opportunities = calculate_savings(
            frame,
            benchmarks,
            "Item",
            "Shop",
            "Amount",
            as_of,
            reference_column="Reference" if "Reference" in frame else None,
        )
        evaluated += _evaluated_total(opportunities)
        for _, opportunity in opportunities.iterrows():
            estimate = float(opportunity["estimated_avoidable_spend"])
            if estimate <= 0:
                continue
            confidence = _calculate_confidence(
                int(opportunity["evaluated_transactions"]),
                int(opportunity["peer_entities"]),
                int(opportunity["peer_transactions"]),
                opportunity["peer_dispersion"],
            )
            item_name = str(opportunity["Item"])
            shop_name = str(opportunity["Shop"])
            candidates.append(
                {
                    "text": (
                        f"Set a price ceiling of **{format_rupiah(opportunity['target_price'])}** "
                        f"(`{opportunity['target_basis']}`) for **{item_name}** at **{shop_name}**. "
                        f"{int(opportunity['affected_transactions'])} of "
                        f"{int(opportunity['evaluated_transactions'])} comparable purchases in the last "
                        "12 months were above this target."
                        f"{_alternative_text(opportunity)}"
                    ),
                    "est": estimate,
                    "confidence": confidence,
                    "last_date": opportunity["last_transaction"],
                    "evidence": {
                        "group": item_name,
                        "entity": shop_name,
                        "target_price": float(opportunity["target_price"]),
                        "target_basis": str(opportunity["target_basis"]),
                        "evaluated_transactions": int(opportunity["evaluated_transactions"]),
                    },
                }
            )

        promotional_transactions = frame[
            frame["Reference"].notna()
            & (frame["Reference"] > 0)
            & (frame["Savings"] >= 0.2 * frame["Reference"])
        ]
        if not promotional_transactions.empty:
            promotion_summary = (
                promotional_transactions.groupby("Item")
                .agg(
                    purchases=("Amount", "size"),
                    median_price=("Amount", "median"),
                    last_purchase=("Date", "max"),
                )
                .nlargest(1, "purchases")
            )
            for item_name, promotion in promotion_summary.iterrows():
                if promotion["purchases"] >= 2:
                    candidates.append(
                        {
                            "text": (
                                f"Monitor promotions for **{item_name}**: it has been purchased at least 20% "
                                f"below the 70% reference {int(promotion['purchases'])} times. Consider timing "
                                "future purchases around similar promotions when your stock allows."
                            ),
                            "est": 0.0,
                            "confidence": _calculate_confidence(int(promotion["purchases"])),
                            "last_date": promotion["last_purchase"],
                        }
                    )

    else:
        account_care = frame[
            (frame["Content"] == "Account care")
            & frame["Days"].notna()
            & (frame["Days"] > 0)
        ].copy()
        if not account_care.empty:
            account_care["Daily rate"] = account_care["Amount"] / account_care["Days"]
            daily_benchmarks = calculate_benchmarks(
                account_care,
                "Content",
                "Provider",
                "Daily rate",
                as_of,
                lookback_months=BENCHMARK_LOOKBACK_MONTHS,
                minimum_entity_transactions=MINIMUM_ENTITY_TRANSACTIONS,
                minimum_peer_entities=MINIMUM_PEER_ENTITIES,
            )
            daily_opportunities = calculate_savings(
                account_care,
                daily_benchmarks,
                "Content",
                "Provider",
                "Daily rate",
                as_of,
                multiplier_column="Days",
            )
            evaluated += _evaluated_total(daily_opportunities)
            for _, opportunity in daily_opportunities.iterrows():
                estimate = float(opportunity["estimated_avoidable_spend"])
                if estimate <= 0:
                    continue
                confidence = _calculate_confidence(
                    int(opportunity["evaluated_transactions"]),
                    int(opportunity["peer_entities"]),
                    int(opportunity["peer_transactions"]),
                    opportunity["peer_dispersion"],
                )
                provider_name = str(opportunity["Provider"])
                candidates.append(
                    {
                        "text": (
                            f"For **Account care** bookings with **{provider_name}**, compare the daily rate "
                            f"with the eligible peer-provider median of **{format_rupiah(opportunity['target_price'])}** "
                            f"(`{opportunity['target_basis']}`). "
                            f"{int(opportunity['affected_transactions'])} of "
                            f"{int(opportunity['evaluated_transactions'])} recent bookings were above that rate."
                            f"{_alternative_text(opportunity, ' per day')}"
                        ),
                        "est": estimate,
                        "confidence": confidence,
                        "last_date": opportunity["last_transaction"],
                    }
                )

        recurring = frame[frame["Content"].isin(RECURRING_SERVICES)].copy()
        if not recurring.empty:
            recurring_benchmarks = calculate_benchmarks(
                recurring,
                "Content",
                "Provider",
                "Amount",
                as_of,
                lookback_months=BENCHMARK_LOOKBACK_MONTHS,
                minimum_entity_transactions=MINIMUM_ENTITY_TRANSACTIONS,
                minimum_peer_entities=MINIMUM_PEER_ENTITIES,
            )
            recurring_opportunities = calculate_savings(
                recurring,
                recurring_benchmarks,
                "Content",
                "Provider",
                "Amount",
                as_of,
            )
            evaluated += _evaluated_total(recurring_opportunities)
            for _, opportunity in recurring_opportunities.iterrows():
                estimate = float(opportunity["estimated_avoidable_spend"])
                if estimate <= 0:
                    continue
                confidence = _calculate_confidence(
                    int(opportunity["evaluated_transactions"]),
                    int(opportunity["peer_entities"]),
                    int(opportunity["peer_transactions"]),
                    opportunity["peer_dispersion"],
                )
                service_name = str(opportunity["Content"])
                provider_name = str(opportunity["Provider"])
                candidates.append(
                    {
                        "text": (
                            f"Review **{service_name}** pricing with **{provider_name}**. The peer-provider "
                            f"median is **{format_rupiah(opportunity['target_price'])}** "
                            f"(`{opportunity['target_basis']}`) per transaction; "
                            f"{int(opportunity['affected_transactions'])} of "
                            f"{int(opportunity['evaluated_transactions'])} recent bookings were above it."
                            f"{_alternative_text(opportunity, ' per booking')}"
                        ),
                        "est": estimate,
                        "confidence": confidence,
                        "last_date": opportunity["last_transaction"],
                    }
                )

        one_time_providers = int((frame["Provider"].value_counts() == 1).sum())
        if one_time_providers >= 5:
            candidates.append(
                {
                    "text": (
                        f"Review one-off provider usage: **{one_time_providers} providers** were used only once. "
                        "Where service scope and quality are equivalent, compare repeat-provider quotations "
                        "before placing another order."
                    ),
                    "est": 0.0,
                    "confidence": 0.5,
                    "last_date": frame["Date"].max(),
                }
            )

    recent_spend = select_window(frame, as_of, 12)["Amount"].sum()
    ranked = rank_recommendations(
        score_recommendations(candidates, as_of),
        minimum_estimate=MATERIAL_ESTIMATE_SHARE * recent_spend,
    )
    return ranked, evaluated
