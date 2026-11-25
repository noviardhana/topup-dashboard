"""Benchmarks, avoidable-spend estimates, recommendation ranking, and insight text.

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
    SOURCE_REFERENCE_COLUMN,
)
from .formatting import format_rupiah


def select_window(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    months_from: int,
    months_to: int = 0,
) -> pd.DataFrame:
    """Select transactions within a rolling date window.

    Args:
        frame: Transactions containing a ``Date`` column.
        as_of: Inclusive end date for the selected window.
        months_from: Number of months before ``as_of`` for the start boundary.
        months_to: Number of months before ``as_of`` for the end boundary.

    Returns:
        Transactions where ``as_of - months_from < Date <= as_of - months_to``.
    """
    lower_bound = as_of - pd.DateOffset(months=months_from)
    upper_bound = as_of - pd.DateOffset(months=months_to)
    return frame[(frame["Date"] > lower_bound) & (frame["Date"] <= upper_bound)]


def build_trend_summary(frame: pd.DataFrame, as_of: pd.Timestamp) -> str | None:
    """Summarise the change in average monthly spending between two half-years.

    Args:
        frame: Transactions containing ``Date`` and ``Amount`` columns.
        as_of: Date used as the end of the comparison windows.

    Returns:
        A Markdown-ready trend sentence, or None when the data covers less than
        12 months or the earlier period has no positive spend.
    """
    first_date = frame["Date"].min()
    if pd.isna(first_date) or first_date > as_of - pd.DateOffset(months=12):
        # Without 12 months of history the earlier window is incomplete and
        # the comparison would overstate growth.
        return None
    recent_average = select_window(frame, as_of, 6)["Amount"].sum() / 6
    previous_average = select_window(frame, as_of, 12, 6)["Amount"].sum() / 6
    if previous_average <= 0:
        return None

    change = recent_average / previous_average - 1
    if change > 0:
        direction = "increased"
    elif change < 0:
        direction = "decreased"
    else:
        direction = "remained unchanged"
    return (
        f"Average monthly spending over the last six months was **{format_rupiah(recent_average)}**, "
        f"{direction} by **{abs(change):.0%}** compared with the preceding six months "
        f"({format_rupiah(previous_average)} per month)."
    )


def calculate_game_stats(frame: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, Any]:
    """Calculate lifetime and trailing 12-month metrics for a game.

    Args:
        frame: Cleaned game transaction data.
        as_of: Date used to calculate the trailing 12-month period.

    Returns:
        A dictionary containing spend, transaction, overpay, saving, and hit-rate
        statistics.
    """
    recent = select_window(frame, as_of, 12)
    comparable = frame[frame["Reference"].notna()]
    recent_comparable = recent[recent["Reference"].notna()]
    return {
        "total": frame["Amount"].sum(),
        "count": len(frame),
        "overpay": frame["Overpay"].sum(),
        "savings": frame["Savings"].sum(),
        "hit_rate": (comparable["Overpay"] == 0).mean() if len(comparable) else float("nan"),
        "recent_count": len(recent),
        "recent_total": recent["Amount"].sum(),
        "recent_overpay": recent["Overpay"].sum(),
        "recent_hit_rate": (recent_comparable["Overpay"] == 0).mean() if len(recent_comparable) else float("nan"),
    }


def calculate_benchmarks(
    frame: pd.DataFrame,
    group_column: str,
    entity_column: str,
    price_column: str,
    as_of: pd.Timestamp,
    lookback_months: int = 24,
    minimum_entity_transactions: int = MINIMUM_ENTITY_TRANSACTIONS,
    minimum_peer_entities: int = MINIMUM_PEER_ENTITIES,
) -> pd.DataFrame:
    """Build leave-one-entity-out price benchmarks for comparable transactions.

    Benchmarks are calculated within each group (for example, the same game
    item or service type). The comparison median excludes the entity being
    assessed and includes only peer entities with enough observations.

    Args:
        frame: Transactions containing date, group, entity, and price columns.
        group_column: Column defining comparable products or service types.
        entity_column: Shop or provider column being evaluated.
        price_column: Numeric price column used for comparison.
        as_of: Reference date for the benchmark look-back window.
        lookback_months: Number of historical months used to build benchmarks.
        minimum_entity_transactions: Minimum observations required per entity.
        minimum_peer_entities: Number of eligible alternative entities required.

    Returns:
        A data frame with each group/entity median and its eligible peer median,
        peer counts, latest transaction date, peer-price dispersion, and the
        cheapest eligible peer with its median price.
    """
    result_columns = [
        group_column,
        entity_column,
        "entity_median",
        "entity_transactions",
        "peer_median",
        "peer_entities",
        "peer_transactions",
        "peer_dispersion",
        "cheapest_peer",
        "cheapest_peer_median",
        "last_transaction",
    ]
    required = {"Date", group_column, entity_column, price_column}
    if not required.issubset(frame.columns) or frame.empty:
        return pd.DataFrame(columns=result_columns)

    lower_bound = as_of - pd.DateOffset(months=lookback_months)
    history = frame.loc[
        (frame["Date"] > lower_bound) & (frame["Date"] <= as_of),
        ["Date", group_column, entity_column, price_column],
    ].copy()
    history[price_column] = pd.to_numeric(history[price_column], errors="coerce")
    history = history.dropna(subset=[group_column, entity_column, price_column, "Date"])
    history = history[(history[price_column] > 0)]
    if history.empty:
        return pd.DataFrame(columns=result_columns)

    records: list[dict[str, Any]] = []
    for (group_value, entity_value), entity_frame in history.groupby(
        [group_column, entity_column], dropna=False
    ):
        peer_frame = history[
            (history[group_column] == group_value)
            & (history[entity_column] != entity_value)
        ]
        peer_counts = peer_frame.groupby(entity_column)[price_column].size()
        eligible_peers = peer_counts[peer_counts >= minimum_entity_transactions].index
        eligible_peer_frame = peer_frame[peer_frame[entity_column].isin(eligible_peers)]
        peer_entity_medians = eligible_peer_frame.groupby(entity_column)[price_column].median()
        peer_entity_count = int(peer_entity_medians.size)

        peer_median = float("nan")
        peer_dispersion = float("nan")
        cheapest_peer = None
        cheapest_peer_median = float("nan")
        if peer_entity_count >= minimum_peer_entities:
            # Give each eligible shop/provider equal weight, regardless of purchase frequency.
            peer_median = float(peer_entity_medians.median())
            median_absolute_deviation = float(
                (peer_entity_medians - peer_median).abs().median()
            )
            peer_dispersion = (
                median_absolute_deviation / abs(peer_median) if peer_median else 0.0
            )
            # Cheapest eligible alternative, named in recommendations.
            cheapest_peer = peer_entity_medians.idxmin()
            cheapest_peer_median = float(peer_entity_medians.min())

        records.append(
            {
                group_column: group_value,
                entity_column: entity_value,
                "entity_median": float(entity_frame[price_column].median()),
                "entity_transactions": int(len(entity_frame)),
                "peer_median": peer_median,
                "peer_entities": peer_entity_count,
                "peer_transactions": int(len(eligible_peer_frame)),
                "peer_dispersion": peer_dispersion,
                "cheapest_peer": cheapest_peer,
                "cheapest_peer_median": cheapest_peer_median,
                "last_transaction": entity_frame["Date"].max(),
            }
        )

    return pd.DataFrame.from_records(records, columns=result_columns)


def _calculate_confidence(
    sample_size: int,
    peer_entities: int = 0,
    peer_transactions: int = 0,
    peer_dispersion: float | None = None,
) -> float:
    """Estimate evidence confidence from sample size and benchmark stability.

    Args:
        sample_size: Number of recent comparable transactions being assessed.
        peer_entities: Number of independent entities supporting the benchmark.
        peer_transactions: Number of peer transactions supporting the benchmark.
        peer_dispersion: Median absolute deviation divided by the peer median.

    Returns:
        A confidence score between 0 and 1. This is a heuristic, not a
        statistical probability.
    """
    if sample_size <= 0:
        return 0.0

    sample_factor = min(1.0, (sample_size / 5) ** 0.5)
    if peer_entities > 0:
        entity_factor = min(1.0, (peer_entities / 3) ** 0.5)
        peer_sample_factor = min(1.0, (peer_transactions / 6) ** 0.5)
    else:
        # Source-provided reference prices do not require peer entities.
        entity_factor = 1.0
        peer_sample_factor = 1.0

    dispersion = 0.0 if peer_dispersion is None or pd.isna(peer_dispersion) else float(peer_dispersion)
    stability_factor = max(0.55, 1.0 - min(0.45, dispersion))
    return round(min(1.0, sample_factor * entity_factor * peer_sample_factor * stability_factor), 3)


def calculate_savings(
    frame: pd.DataFrame,
    benchmarks: pd.DataFrame,
    group_column: str,
    entity_column: str,
    price_column: str,
    as_of: pd.Timestamp,
    reference_column: str | None = None,
    multiplier_column: str | None = None,
    months: int = 12,
) -> pd.DataFrame:
    """Estimate historical avoidable spend against comparable price targets.

    A row's target is the lower of its peer benchmark and an optional source
    reference price. If only one is available, that value is used. For unit-rate
    services, ``multiplier_column`` scales the per-unit difference by duration.

    Args:
        frame: Transactions containing date, group, entity, and price columns.
        benchmarks: Output from :func:`calculate_benchmarks`.
        group_column: Comparable product or service column.
        entity_column: Shop or provider column being assessed.
        price_column: Actual transaction price or unit rate.
        as_of: Reference date for the recent transaction window.
        reference_column: Optional source-defined price ceiling, such as 70% Value.
        multiplier_column: Optional quantity/duration column, such as service days.
        months: Number of recent months included in the estimate.

    Returns:
        Aggregated opportunities by group and entity, including estimated
        avoidable spend, evidence counts, confidence inputs, the basis of the
        target price, the cheapest eligible peer, and the latest date.
    """
    output_columns = [
        group_column,
        entity_column,
        "estimated_avoidable_spend",
        "evaluated_transactions",
        "affected_transactions",
        "target_price",
        "observed_median",
        "peer_entities",
        "peer_transactions",
        "peer_dispersion",
        "target_basis",
        "cheapest_peer",
        "cheapest_peer_median",
        "last_transaction",
    ]
    if frame.empty:
        return pd.DataFrame(columns=output_columns)

    recent = select_window(frame, as_of, months).copy()
    if recent.empty:
        return pd.DataFrame(columns=output_columns)

    benchmark_columns = [
        group_column,
        entity_column,
        "peer_median",
        "peer_entities",
        "peer_transactions",
        "peer_dispersion",
        "cheapest_peer",
        "cheapest_peer_median",
    ]
    if benchmarks.empty:
        recent["peer_median"] = float("nan")
        recent["peer_entities"] = 0
        recent["peer_transactions"] = 0
        recent["peer_dispersion"] = float("nan")
        recent["cheapest_peer"] = None
        recent["cheapest_peer_median"] = float("nan")
    else:
        recent = recent.merge(
            benchmarks[benchmark_columns],
            on=[group_column, entity_column],
            how="left",
        )

    recent["peer_median"] = pd.to_numeric(recent["peer_median"], errors="coerce")
    recent[price_column] = pd.to_numeric(recent[price_column], errors="coerce")
    if reference_column and reference_column in recent:
        reference_prices = pd.to_numeric(recent[reference_column], errors="coerce")
        reference_prices = reference_prices.where(reference_prices > 0)
        target_frame = pd.concat(
            [recent["peer_median"], reference_prices.rename("reference_price")], axis=1
        )
        recent["target_price"] = target_frame.min(axis=1, skipna=True)
        # The reference wins ties and whenever no peer median exists.
        uses_reference = reference_prices.notna() & (
            reference_prices <= recent["peer_median"].fillna(float("inf"))
        )
        recent["target_basis"] = uses_reference.map(
            {True: SOURCE_REFERENCE_COLUMN, False: "Peer median"}
        )
    else:
        recent["target_price"] = recent["peer_median"]
        recent["target_basis"] = "Peer median"

    if multiplier_column and multiplier_column in recent:
        multiplier = pd.to_numeric(recent[multiplier_column], errors="coerce")
    else:
        multiplier = pd.Series(1.0, index=recent.index)

    comparable = recent.dropna(subset=[price_column, "target_price", "Date"]).copy()
    comparable = comparable.loc[multiplier.loc[comparable.index].fillna(0) > 0]
    if comparable.empty:
        return pd.DataFrame(columns=output_columns)

    comparable["_multiplier"] = multiplier.loc[comparable.index]
    comparable["_avoidable"] = (
        (comparable[price_column] - comparable["target_price"]).clip(lower=0)
        * comparable["_multiplier"]
    )
    records: list[dict[str, Any]] = []
    for (group_value, entity_value), group_frame in comparable.groupby(
        [group_column, entity_column], dropna=False
    ):
        records.append(
            {
                group_column: group_value,
                entity_column: entity_value,
                "estimated_avoidable_spend": float(group_frame["_avoidable"].sum()),
                "evaluated_transactions": int(len(group_frame)),
                "affected_transactions": int((group_frame["_avoidable"] > 0).sum()),
                "target_price": float(group_frame["target_price"].median()),
                "observed_median": float(group_frame[price_column].median()),
                "peer_entities": int(group_frame["peer_entities"].fillna(0).max()),
                "peer_transactions": int(group_frame["peer_transactions"].fillna(0).max()),
                "peer_dispersion": float(group_frame["peer_dispersion"].dropna().median())
                if group_frame["peer_dispersion"].notna().any()
                else float("nan"),
                "target_basis": str(group_frame["target_basis"].mode().iloc[0]),
                "cheapest_peer": (
                    group_frame["cheapest_peer"].dropna().iloc[0]
                    if group_frame["cheapest_peer"].notna().any()
                    else None
                ),
                "cheapest_peer_median": float(group_frame["cheapest_peer_median"].dropna().iloc[0])
                if group_frame["cheapest_peer_median"].notna().any()
                else float("nan"),
                "last_transaction": group_frame["Date"].max(),
            }
        )

    return pd.DataFrame.from_records(records, columns=output_columns)


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
