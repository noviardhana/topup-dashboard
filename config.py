"""Application configuration: constants, palette, source-sheet column names, and CSS."""

from __future__ import annotations

WORKSHEET_NAME = "Hoyoverse"
GAME_ORDER = ("Genshin", "HSR")
BOOSTING_CATEGORY = "Boosting"
RECURRING_SERVICES = ("VIP IT", "VIP Spiral", "Stygian Onslaught")
BENCHMARK_LOOKBACK_MONTHS = 24
RECOMMENDATION_LOOKBACK_MONTHS = 12
MINIMUM_ENTITY_TRANSACTIONS = 2
MINIMUM_PEER_ENTITIES = 2
RECENCY_HALF_LIFE_DAYS = 365.25
HIGH_PRIORITY_RELATIVE_THRESHOLD = 0.70
MEDIUM_PRIORITY_RELATIVE_THRESHOLD = 0.30
COLOUR_PALETTE = (
    "#56B4E9",  # sky blue
    "#E69F00",  # amber
    "#009E73",  # green
    "#CC79A7",  # pink
    "#F0E442",  # yellow
    "#D55E00",  # vermilion
    "#3D6FE0",  # blue
    "#B388FF",  # violet
    "#A6ACB5",  # grey
    "#7FD1B9",  # mint
)
CATEGORY_COLOURS = {
    "Genshin": "#56B4E9",
    "HSR": "#E69F00",
    BOOSTING_CATEGORY: "#CC79A7",
}
SAVINGS_COLOUR = "#009E73"
OVERPAY_COLOUR = "#D55E00"
LINE_COLOUR = "#9AA4B2"  # neutral colour for average / cumulative lines
MATERIAL_ESTIMATE_SHARE = 0.02  # an estimate must reach 2% of 12-month spend to rank High/Medium
RECOMMENDATIONS_SHOWN = 5  # recommendations shown before the rest move into an expander
STRONG_EVIDENCE_THRESHOLD = 0.75
MODERATE_EVIDENCE_THRESHOLD = 0.50
BUDGET_PERIOD_SCALE = {"Month": 1, "Quarter": 3, "Year": 12}  # months per period
PERIOD_OPTIONS = {
    "Monthly": "Month",
    "Quarterly": "Quarter",
    "By patch": "Patch",
    "Yearly": "Year",
}
SUMMARY_PERIOD_OPTIONS = {
    label: value for label, value in PERIOD_OPTIONS.items() if value != "Patch"
}
SOURCE_REFERENCE_COLUMN = "70% Value"
SOURCE_SHOP_COLUMN = "Toko"
SOURCE_DESCRIPTION_COLUMN = "Keterangan"
SOURCE_PROVIDER_COLUMN = "Joki"
DASHBOARD_CSS = """
<style>
    .block-container { padding-top: 4.5rem; }
    .dashboard-header {
        padding: 0.2rem 0 1rem 0;
        margin: 0 0 1.25rem 0;
        border-bottom: 1px solid rgba(140, 150, 165, 0.28);
    }
    .dashboard-eyebrow {
        margin-bottom: 0.2rem;
        color: #56B4E9;
        font-size: 0.70rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    .dashboard-title {
        font-size: 1.6rem;
        font-weight: 700;
        line-height: 1.25;
    }
    .dashboard-subtitle {
        margin-top: 0.3rem;
        font-size: 0.85rem;
        opacity: 0.70;
    }
    .kpi-card {
        min-height: 100px;
        padding: 0.8rem 1rem;
        margin-bottom: 0.6rem;
        border: 1px solid rgba(140, 150, 165, 0.22);
        border-left: 3px solid rgba(140, 150, 165, 0.55);
        border-radius: 8px;
        background: rgba(140, 150, 165, 0.07);
    }
    .kpi-card.good { border-left-color: #009E73; }
    .kpi-card.bad { border-left-color: #D55E00; }
    .kpi-label {
        margin-bottom: 0.3rem;
        font-size: 0.70rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        opacity: 0.72;
        text-transform: uppercase;
    }
    .kpi-value {
        font-size: 1.3rem;
        font-weight: 700;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }
    .kpi-caption {
        min-height: 1rem;
        margin-top: 0.2rem;
        font-size: 0.73rem;
        opacity: 0.68;
    }
    div[data-testid="stPlotlyChart"] {
        border: 1px solid rgba(140, 150, 165, 0.18);
        border-radius: 8px;
        padding: 0.25rem;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(140, 150, 165, 0.18);
        border-radius: 8px;
    }
</style>
"""
