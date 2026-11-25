"""Rolling windows, spending statistics, peer benchmarks, confidence, and avoidable-spend estimates."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .config import (
    MINIMUM_ENTITY_TRANSACTIONS,
    MINIMUM_PEER_ENTITIES,
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
