"""Reusable Streamlit UI components: KPI cards, bullets, recommendations, and methodology."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from .config import (
    BENCHMARK_LOOKBACK_MONTHS,
    MATERIAL_ESTIMATE_SHARE,
    MINIMUM_ENTITY_TRANSACTIONS,
    MINIMUM_PEER_ENTITIES,
    MODERATE_EVIDENCE_THRESHOLD,
    RECOMMENDATIONS_SHOWN,
    RECOMMENDATION_LOOKBACK_MONTHS,
    SOURCE_REFERENCE_COLUMN,
    STRONG_EVIDENCE_THRESHOLD,
)
from .formatting import format_rupiah


def render_kpi(column: Any, label: str, value: str, caption: str = "", tone: str = "") -> None:
    """Render a compact KPI card inside a Streamlit column.

    Args:
        column: The Streamlit column that will contain the card.
        label: The metric label displayed above the value.
        value: The primary metric value.
        caption: Optional supporting text below the value.
        tone: Optional status class: ``good``, ``bad``, or an empty string.

    Returns:
        None. The card is rendered directly in the Streamlit interface.
    """
    safe_label = html.escape(str(label))
    safe_value = html.escape(str(value))
    safe_caption = html.escape(str(caption))
    safe_tone = tone if tone in {"good", "bad"} else ""
    column.markdown(
        f'<div class="kpi-card {safe_tone}">'
        f'<div class="kpi-label">{safe_label}</div>'
        f'<div class="kpi-value">{safe_value}</div>'
        f'<div class="kpi-caption">{safe_caption}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def evidence_label(score: float) -> str:
    """Translate a heuristic confidence score into a plain-language label.

    Args:
        score: Evidence score between 0 and 1.

    Returns:
        ``strong``, ``moderate``, or ``limited``. A label is used instead of a
        percentage so the score is not mistaken for a probability.
    """
    if score >= STRONG_EVIDENCE_THRESHOLD:
        return "strong"
    if score >= MODERATE_EVIDENCE_THRESHOLD:
        return "moderate"
    return "limited"


def render_bullets(items: list[str], empty_message: str = "Not enough data to show notes yet.") -> None:
    """Render a list of insight statements as Markdown bullets.

    Args:
        items: Insight strings to display.
        empty_message: Message shown in italics when there are no items.

    Returns:
        None. The bullet list is rendered in the Streamlit interface.
    """
    content = "\n".join(f"- {item}" for item in items) if items else f"_{empty_message}_"
    st.markdown(content)


def empty_recommendation_message(evaluated_transactions: int) -> str:
    """Explain why no recommendations are shown.

    Args:
        evaluated_transactions: Number of recent transactions that could be
            compared with a reference or peer price.

    Returns:
        A message that separates "nothing could be assessed" from "nothing
        needs saving".
    """
    if evaluated_transactions <= 0:
        return (
            "Savings could not be assessed yet: no recent transaction has a usable "
            "reference price or enough peer data (see Methodology)."
        )
    return (
        f"No savings opportunities detected: {evaluated_transactions} recent transactions "
        "were compared and all are at or below their benchmark."
    )


def format_recommendations(items: list[dict[str, Any]], limit: int | None = None) -> list[str]:
    """Format ranked recommendations with their evidence label and estimate.

    Args:
        items: Ranked recommendation dictionaries containing ``text``, ``est``,
            ``confidence``, and ``priority`` fields.
        limit: Maximum number of recommendations to return, or None for all.

    Returns:
        Markdown-ready recommendation strings, ordered by priority score.
    """
    selected_items = items if limit is None else items[:limit]
    output = []
    for item in selected_items:
        priority = str(item.get("priority", "Monitor")).upper()
        confidence = item.get("confidence")
        evidence_text = f" · {evidence_label(confidence)} evidence" if confidence is not None else ""
        text = f"**{priority}{evidence_text}** — {item['text']}"
        if item.get("est", 0) > 0:
            text += f" _(estimated avoidable spend: {format_rupiah(item['est'])})_"
        output.append(text)
    return output


def render_recommendations(
    recommendations: list[dict[str, Any]],
    evaluated_transactions: int,
    limit: int = RECOMMENDATIONS_SHOWN,
    expand_rest: bool = True,
) -> None:
    """Render the top recommendations, optionally folding the rest into an expander.

    Args:
        recommendations: Ranked recommendation dictionaries.
        evaluated_transactions: Number of transactions that could be compared;
            used to word the empty-state message.
        limit: Number of recommendations shown directly.
        expand_rest: When True, remaining items go into a "View all" expander;
            when False they are omitted (used by the compact overview).

    Returns:
        None. The recommendations are rendered in the Streamlit interface.
    """
    if not recommendations:
        render_bullets([], empty_recommendation_message(evaluated_transactions))
        return
    formatted = format_recommendations(recommendations)
    render_bullets(formatted[:limit])
    if expand_rest and len(formatted) > limit:
        with st.expander(f"View remaining {len(formatted) - limit} recommendations"):
            render_bullets(formatted[limit:])


def render_methodology() -> None:
    """Render a popover that explains how targets, estimates, and labels work.

    Returns:
        None. The popover is rendered in the Streamlit interface.
    """
    with st.popover("Methodology"):
        st.markdown(
            f"""
**Targets.** Game purchases are compared with the lower of the worksheet's `{SOURCE_REFERENCE_COLUMN}`
and the median of eligible alternative shops. Boosting services are compared with the median of
eligible alternative providers (account care by daily rate). Each recommendation shows its basis:
`{SOURCE_REFERENCE_COLUMN}` or `Peer median`.

**Eligibility.** Benchmarks use up to {BENCHMARK_LOOKBACK_MONTHS} months of history. A peer comparison needs
at least {MINIMUM_PEER_ENTITIES} alternative shops or providers with at least
{MINIMUM_ENTITY_TRANSACTIONS} transactions each.

**Estimates.** Avoidable spend is the amount paid above the target in the last
{RECOMMENDATION_LOOKBACK_MONTHS} months. It is a historical comparison, not guaranteed future savings.
The confidence-weighted figure multiplies each estimate by its evidence score.

**Evidence.** `strong` is a score of {STRONG_EVIDENCE_THRESHOLD:.2f} or higher, `moderate` is
{MODERATE_EVIDENCE_THRESHOLD:.2f} or higher, and `limited` is below that. The score is a heuristic based on
sample size, number of peers, and price dispersion; it is not a probability.

**Priority.** High, Medium, and Monitor are relative within each tab. Estimates below
{MATERIAL_ESTIMATE_SHARE:.0%} of 12-month spend are always Monitor.
"""
        )
