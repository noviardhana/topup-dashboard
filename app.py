"""Game Spending Dashboard

A Streamlit dashboard for analysing spending across Genshin, Honkai: Star Rail,
 and game-boosting services. Transaction data is read from the ``Hoyoverse``
Google Sheets worksheet, cleaned into consistent data frames, and presented
through summary metrics, spending trends, shop and provider comparisons, and
practical cost-saving insights.

Configuration
-------------
The application expects ``sheet_id`` and ``gcp_service_account`` to be defined
in Streamlit secrets. The source worksheet must retain its existing column
headers, including ``70% Value``, ``Toko``, and ``Keterangan`` where applicable.

Run with::

    streamlit run app.py
"""

from __future__ import annotations

import html
import numbers
import re
from typing import Any

import gspread
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ---------------------------------------------------------------------------
# Application configuration
# ---------------------------------------------------------------------------

WORKSHEET_NAME = "Hoyoverse"
GAME_ORDER = ("Genshin", "HSR")
BOOSTING_CATEGORY = "Boosting"
RECURRING_SERVICES = ("VIP IT", "VIP Spiral", "Stygian Onslaught")

COLOUR_PALETTE = (
    "#7C83FD",
    "#42A5A5",
    "#E6B566",
    "#E07A5F",
    "#5B9BD5",
    "#C084B8",
    "#6FC2B0",
    "#8795A1",
    "#A79BDB",
    "#8FC7A2",
)
CATEGORY_COLOURS = {
    "Genshin": "#7C83FD",
    "HSR": "#5B9BD5",
    BOOSTING_CATEGORY: "#E07A5F",
}
SAVINGS_COLOUR = "#3FA67A"
OVERPAY_COLOUR = "#D97770"
ACCENT_COLOUR = "#7C83FD"

PERIOD_OPTIONS = {
    "Monthly": "Month",
    "Quarterly": "Quarter",
    "By patch": "Patch",
    "Yearly": "Year",
}
SUMMARY_PERIOD_OPTIONS = {
    label: value for label, value in PERIOD_OPTIONS.items() if value != "Patch"
}

# These names belong to the existing Google Sheets schema and must not be
# translated unless the worksheet headers are changed as well.
SOURCE_REFERENCE_COLUMN = "70% Value"
SOURCE_SHOP_COLUMN = "Toko"
SOURCE_DESCRIPTION_COLUMN = "Keterangan"
SOURCE_PROVIDER_COLUMN = "Joki"

DASHBOARD_CSS = """
<style>
    .dashboard-header {
        padding: 1.1rem 1.35rem;
        margin: 0 0 1.25rem 0;
        border: 1px solid rgba(140, 150, 165, 0.24);
        border-radius: 12px;
        background: var(--secondary-background-color);
    }
    .dashboard-eyebrow {
        margin-bottom: 0.25rem;
        color: var(--primary-color);
        font-size: 0.70rem;
        font-weight: 700;
        letter-spacing: 0.11em;
        text-transform: uppercase;
    }
    .dashboard-title {
        color: var(--text-color);
        font-size: 1.65rem;
        font-weight: 700;
        line-height: 1.25;
    }
    .dashboard-subtitle {
        margin-top: 0.35rem;
        color: var(--text-color);
        font-size: 0.86rem;
        opacity: 0.70;
    }
    .kpi-card {
        min-height: 105px;
        padding: 0.85rem 1rem;
        margin-bottom: 0.6rem;
        border: 1px solid rgba(140, 150, 165, 0.24);
        border-radius: 10px;
        background: var(--secondary-background-color);
    }
    .kpi-label {
        margin-bottom: 0.35rem;
        color: var(--text-color);
        font-size: 0.70rem;
        font-weight: 600;
        letter-spacing: 0.045em;
        opacity: 0.72;
        text-transform: uppercase;
    }
    .kpi-value {
        color: var(--text-color);
        font-size: 1.35rem;
        font-weight: 700;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }
    .kpi-card.good .kpi-value { color: #55B889; }
    .kpi-card.bad .kpi-value { color: #E1847C; }
    .kpi-caption {
        min-height: 1.05rem;
        margin-top: 0.2rem;
        color: var(--text-color);
        font-size: 0.73rem;
        opacity: 0.68;
    }
    div[data-testid="stPlotlyChart"] {
        border: 1px solid rgba(140, 150, 165, 0.15);
        border-radius: 10px;
        padding: 0.15rem;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(140, 150, 165, 0.18);
        border-radius: 8px;
    }
</style>
"""


# ---------------------------------------------------------------------------
# Formatting and presentation helpers
# ---------------------------------------------------------------------------
def format_rupiah(value: Any) -> str:
    """Format a numeric value as whole Indonesian rupiah.

    Args:
        value: The numeric value to format.

    Returns:
        A currency string using dots as thousands separators, or an em dash
        when the value is missing.
    """
    if pd.isna(value):
        return "—"
    return "Rp " + f"{float(value):,.0f}".replace(",", ".")


def _parse_numeric_value(value: Any) -> float:
    """Parse one numeric value, including Indonesian and international formats.

    Args:
        value: A number or formatted numeric string, optionally with a currency
            symbol, thousands separators, decimal separators, or parentheses.

    Returns:
        The parsed number, or NaN when the value is empty or invalid.
    """
    if pd.isna(value):
        return float("nan")
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return float(value)

    text = str(value).strip().replace("−", "-")
    if not text:
        return float("nan")

    is_parenthesised_negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^0-9,.+\-]", "", text)
    if not text or text in {"+", "-", ".", ","}:
        return float("nan")

    explicit_negative = text.startswith("-") or is_parenthesised_negative
    text = text.lstrip("+-")
    if not text:
        return float("nan")

    has_dot = "." in text
    has_comma = "," in text

    if has_dot and has_comma:
        # The right-most separator is the decimal separator; the other is
        # treated as a thousands separator (e.g. 1.234,56 or 1,234.56).
        decimal_separator = "." if text.rfind(".") > text.rfind(",") else ","
        thousands_separator = "," if decimal_separator == "." else "."
        text = text.replace(thousands_separator, "")
        text = text.replace(decimal_separator, ".")
    elif has_dot or has_comma:
        separator = "." if has_dot else ","
        groups = text.split(separator)
        if len(groups) > 2:
            if all(len(group) == 3 for group in groups[1:]):
                text = "".join(groups)
            else:
                text = groups[0] + "." + "".join(groups[1:])
        elif len(groups) == 2:
            # A single three-digit suffix is a thousands group; other suffix
            # lengths are interpreted as decimal places.
            if len(groups[1]) == 3 and len(groups[0]) <= 3:
                text = "".join(groups)
            else:
                text = groups[0] + "." + groups[1]

    try:
        parsed = float(text)
    except ValueError:
        return float("nan")
    return -abs(parsed) if explicit_negative else parsed


def to_number(values: pd.Series) -> pd.Series:
    """Convert raw or formatted monetary values into numeric values.

    Args:
        values: A pandas Series containing numbers or strings such as
            ``1.234.567``, ``1.234.567,00``, or ``1,234,567.00``.

    Returns:
        A numeric Series. Values that cannot be interpreted become NaN.
    """
    return pd.to_numeric(values.map(_parse_numeric_value), errors="coerce")


def to_date(values: pd.Series) -> pd.Series:
    """Convert Google Sheets date serials or date strings into datetimes.

    Args:
        values: A pandas Series containing Google Sheets serial dates, date
            strings, or missing values.

    Returns:
        A datetime Series; unrecognised values are represented by NaT.
    """
    def parse_date_value(value: Any) -> Any:
        if pd.isna(value):
            return pd.NaT
        if isinstance(value, numbers.Real) and not isinstance(value, bool):
            return pd.to_datetime(
                value,
                unit="D",
                origin="1899-12-30",
                errors="coerce",
            )

        text = str(value).strip()
        parsed = pd.to_datetime(text, format="%B %d, %Y", errors="coerce")
        return parsed if not pd.isna(parsed) else pd.to_datetime(text, errors="coerce")

    return pd.to_datetime(values.map(parse_date_value), errors="coerce")


def version_sort_key(version: Any) -> tuple[int, ...]:
    """Create a sortable numeric key from a game version label.

    Args:
        version: A version label such as ``5.3`` or ``4.7.1``.

    Returns:
        A tuple of numeric components. Labels without numbers sort last.
    """
    components = re.findall(r"\d+", str(version))
    return tuple(int(part) for part in components) if components else (10**9,)


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


def render_bullets(items: list[str]) -> None:
    """Render a list of insight statements as Markdown bullets.

    Args:
        items: Insight strings to display.

    Returns:
        None. The bullet list is rendered in the Streamlit interface.
    """
    content = "\n".join(f"- {item}" for item in items) if items else "_No notes are available._"
    st.markdown(content)


def format_recommendations(items: list[dict[str, Any]], limit: int | None = None) -> list[str]:
    """Add estimated savings to recommendations and optionally limit the list.

    Args:
        items: Recommendation dictionaries containing ``text`` and ``est``.
        limit: Maximum number of recommendations to return, or None for all.

    Returns:
        A list of formatted recommendation strings.
    """
    selected_items = items if limit is None else items[:limit]
    output = []
    for item in selected_items:
        text = item["text"]
        if item["est"] > 0:
            text += f" _(estimated saving: {format_rupiah(item['est'])})_"
        output.append(text)
    return output


def format_table(frame: pd.DataFrame, money_columns: tuple[str, ...] | list[str] = ()) -> pd.DataFrame:
    """Prepare a copy of a data frame for display in Streamlit tables.

    Args:
        frame: The source data frame.
        money_columns: Column names that should be formatted as rupiah.

    Returns:
        A display-ready copy with formatted dates and currency values.
    """
    display = frame.copy()
    if "Date" in display:
        display["Date"] = pd.to_datetime(display["Date"], errors="coerce").dt.strftime("%d %b %Y")
    for column in money_columns:
        if column in display:
            display[column] = display[column].map(format_rupiah)
    return display


# ---------------------------------------------------------------------------
# Data cleaning and Google Sheets access
# ---------------------------------------------------------------------------
def clean_game_data(frame: pd.DataFrame) -> pd.DataFrame:
    """Clean a game-spending table and calculate its reference-price variance.

    Args:
        frame: Raw game transaction data from Google Sheets.

    Returns:
        A cleaned data frame with English analytical column names and derived
        ``Variance``, ``Overpay``, and ``Savings`` columns.
    """
    required = {"Date", "Amount", SOURCE_REFERENCE_COLUMN, "Version", "Item", SOURCE_SHOP_COLUMN}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Game table is missing required columns: {', '.join(sorted(missing))}.")

    cleaned = frame.copy()
    cleaned["Date"] = to_date(cleaned["Date"])
    cleaned["Amount"] = to_number(cleaned["Amount"])
    cleaned["Reference"] = to_number(cleaned[SOURCE_REFERENCE_COLUMN])
    cleaned["Version"] = cleaned["Version"].astype("string").fillna("").str.strip()
    cleaned["Item"] = cleaned["Item"].astype("string").fillna("").str.strip()
    cleaned["Shop"] = cleaned[SOURCE_SHOP_COLUMN].astype("string").fillna("").str.strip()
    cleaned["Variance"] = cleaned["Amount"] - cleaned["Reference"]
    cleaned["Overpay"] = cleaned["Variance"].clip(lower=0)
    cleaned["Savings"] = (-cleaned["Variance"]).clip(lower=0)
    cleaned = cleaned.dropna(subset=["Date", "Amount"])
    cleaned = cleaned.drop(columns=[SOURCE_REFERENCE_COLUMN, SOURCE_SHOP_COLUMN], errors="ignore")
    return cleaned.sort_values("Date", kind="stable").reset_index(drop=True)


def clean_boosting_data(frame: pd.DataFrame) -> pd.DataFrame:
    """Clean a boosting-services table and extract service duration in days.

    Args:
        frame: Raw boosting transaction data from Google Sheets.

    Returns:
        A cleaned data frame with ``Provider``, ``Description``, and ``Days``
        columns, sorted by transaction date.
    """
    required = {"Date", "Amount", "Version", SOURCE_PROVIDER_COLUMN, "Content"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Boosting table is missing required columns: {', '.join(sorted(missing))}.")

    cleaned = frame.copy()
    cleaned["Date"] = to_date(cleaned["Date"])
    cleaned["Amount"] = to_number(cleaned["Amount"])
    cleaned["Version"] = cleaned["Version"].astype("string").fillna("").str.strip()
    cleaned["Provider"] = cleaned[SOURCE_PROVIDER_COLUMN].astype("string").fillna("").str.strip()
    cleaned["Content"] = cleaned["Content"].astype("string").fillna("").str.strip()
    cleaned["Content"] = cleaned["Content"].replace({"Rawat Akun": "Account care"})
    if SOURCE_DESCRIPTION_COLUMN not in cleaned:
        cleaned[SOURCE_DESCRIPTION_COLUMN] = ""
    cleaned["Description"] = cleaned[SOURCE_DESCRIPTION_COLUMN].astype("string").fillna("").str.strip()
    cleaned["Days"] = pd.to_numeric(
        cleaned["Description"].str.extract(r"\((\d+)\s*(?:hari|days?)\)", flags=re.IGNORECASE)[0],
        errors="coerce",
    )
    cleaned = cleaned.dropna(subset=["Date", "Amount"])
    cleaned = cleaned.drop(columns=[SOURCE_PROVIDER_COLUMN, SOURCE_DESCRIPTION_COLUMN], errors="ignore")
    return cleaned.sort_values("Date", kind="stable").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner="Loading transaction data...")
def load_sheet_data() -> dict[str, pd.DataFrame]:
    """Load and clean the game and boosting tables from Google Sheets.

    Returns:
        A dictionary of cleaned data frames, keyed by game name or
        ``Boosting``. The result is cached for five minutes.

    Raises:
        KeyError: When required Streamlit secrets are not configured.
        ValueError: When a recognised table is missing required columns.
        gspread exceptions: When the Google Sheets request fails.
    """
    credentials = dict(st.secrets["gcp_service_account"])
    client = gspread.service_account_from_dict(credentials)
    rows = client.open_by_key(st.secrets["sheet_id"]).worksheet(WORKSHEET_NAME).get_all_values(
        value_render_option="UNFORMATTED_VALUE"
    )

    game_names = iter(GAME_ORDER)
    output: dict[str, pd.DataFrame] = {}

    for row_index, row in enumerate(rows):
        is_header = (
            len(row) > 1
            and str(row[0]).strip() in {"No", "No."}
            and str(row[1]).strip() == "Date"
        )
        if not is_header:
            continue

        headers = [value.strip() for value in row]
        while headers and not headers[-1]:
            headers.pop()

        body = []
        for candidate in rows[row_index + 1 :]:
            first_cell = str(candidate[0]).strip() if candidate else ""
            if not first_cell.isdigit():
                break
            body.append((candidate + [""] * len(headers))[: len(headers)])

        raw_frame = pd.DataFrame(body, columns=headers)
        if SOURCE_PROVIDER_COLUMN in headers:
            output[BOOSTING_CATEGORY] = clean_boosting_data(raw_frame)
        elif "Item" in headers:
            game_name = next(game_names, None)
            if game_name:
                output[game_name] = clean_game_data(raw_frame)

    ordered_keys = [*GAME_ORDER, BOOSTING_CATEGORY]
    return {key: output[key] for key in ordered_keys if key in output}


# ---------------------------------------------------------------------------
# Time periods and chart construction
# ---------------------------------------------------------------------------
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
        period_frame["_period"] = period_frame["Version"].astype(str)
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
        margin={"l": 8, "r": 8, "t": 12, "b": 8},
        legend={"orientation": "h", "y": -0.22, "x": 0, "title_text": ""},
        hovermode="x unified",
        bargap=0.25,
        xaxis_title=None,
        yaxis_title=None,
        separators=",.",
    )
    figure.update_yaxes(tickprefix="Rp ", tickformat=",.0f", gridcolor="rgba(140, 150, 165, 0.18)")
    if period == "Month":
        figure.update_xaxes(tickformat="%b %Y")
    else:
        figure.update_xaxes(type="category", categoryorder="array", categoryarray=[str(item) for item in order])
    return figure


def create_spending_chart(
    frame: pd.DataFrame,
    period: str,
    group_column: str,
    colour_map: dict[str, str] | None = None,
) -> go.Figure:
    """Create a stacked spending chart with a three-month average when relevant.

    Args:
        frame: Transaction data containing ``Date``, ``Amount``, and the group
            column.
        period: Period identifier: ``Month``, ``Quarter``, ``Year``, or ``Patch``.
        group_column: Column used to split spending into categories.
        colour_map: Optional mapping of group labels to colours.

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

    figure = px.bar(
        long_frame,
        x="Period",
        y="Amount",
        color=group_column,
        color_discrete_map=colour_map,
        color_discrete_sequence=COLOUR_PALETTE,
    )
    figure.update_traces(hovertemplate="%{fullData.name}: Rp %{y:,.0f}<extra></extra>")

    if period == "Month" and not pivot.empty:
        total_by_period = pivot.sum(axis=1)
        figure.add_scatter(
            x=total_by_period.index,
            y=total_by_period.rolling(3, min_periods=1).mean(),
            mode="lines",
            name="Three-month average",
            line={"color": ACCENT_COLOUR, "width": 2, "dash": "dot"},
            hovertemplate="Three-month average: Rp %{y:,.0f}<extra></extra>",
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
        line={"color": ACCENT_COLOUR, "width": 2},
        hovertemplate="Cumulative net: Rp %{y:,.0f}<extra></extra>",
    )
    figure.update_layout(
        barmode="relative",
        yaxis2={"overlaying": "y", "side": "right", "tickprefix": "Rp ", "tickformat": ",.0f", "showgrid": False},
    )
    return style_figure(figure, period, order)


# ---------------------------------------------------------------------------
# Statistical calculations and insight generation
# ---------------------------------------------------------------------------
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
        A Markdown-ready trend sentence, or None when the earlier period has
        no positive spend to compare against.
    """
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


def build_game_insights(frame: pd.DataFrame, as_of: pd.Timestamp) -> tuple[list[str], list[dict[str, Any]]]:
    """Generate game-spending conclusions and cost-saving recommendations.

    Args:
        frame: Cleaned game transaction data.
        as_of: Date used for rolling 12-month comparisons.

    Returns:
        A pair containing conclusion strings and recommendation dictionaries.
        Each recommendation has ``text`` and estimated ``est`` savings.
    """
    conclusions: list[str] = []
    recommendations: list[dict[str, Any]] = []
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

    hit_rate_text = (
        f"{stats['hit_rate']:.0%} of comparable transactions were at or below the reference price."
        if pd.notna(stats["hit_rate"])
        else "A hit rate is unavailable because reference prices are missing."
    )
    if stats["recent_count"]:
        if pd.notna(stats["recent_hit_rate"]):
            hit_rate_text += (
                f" Over the last 12 months: {stats['recent_hit_rate']:.0%} "
                f"across {stats['recent_count']} transactions."
            )
        else:
            hit_rate_text += f" There were {stats['recent_count']} transactions in the last 12 months."
    conclusions.append(hit_rate_text)

    trend = build_trend_summary(frame, as_of)
    if trend:
        conclusions.append(trend)

    cutoff = as_of - pd.DateOffset(months=12)
    shop_summary = (
        frame.groupby("Shop")
        .agg(
            transactions=("Amount", "size"),
            overpay=("Overpay", "sum"),
            savings=("Savings", "sum"),
            last_used=("Date", "max"),
            overpay_count=("Overpay", lambda values: int((values > 0).sum())),
        )
        .reset_index()
    )
    if not shop_summary.empty:
        shop_summary["net"] = shop_summary["overpay"] - shop_summary["savings"]
        eligible_shops = shop_summary[shop_summary["transactions"] >= 3]
        comparison_shops = eligible_shops if not eligible_shops.empty else shop_summary
        best_shop = comparison_shops.nsmallest(1, "net").iloc[0]
        if best_shop["net"] < 0:
            conclusions.append(
                f"Best-value shop: **{best_shop['Shop']}**, with net savings of "
                f"{format_rupiah(-best_shop['net'])} across {best_shop['transactions']} transactions."
            )

        worst_shop = shop_summary.nlargest(1, "net").iloc[0]
        if worst_shop["net"] > 0:
            conclusions.append(
                f"Highest-overpay shop: **{worst_shop['Shop']}**, with net overpayments of "
                f"{format_rupiah(worst_shop['net'])} across {worst_shop['transactions']} transactions."
            )

        inactive_shops = shop_summary[(shop_summary["overpay"] > 0) & (shop_summary["last_used"] <= cutoff)]
        if not inactive_shops.empty:
            shop_names = ", ".join(f"**{shop}**" for shop in inactive_shops["Shop"])
            conclusions.append(
                "Shops with a history of overpayment but no purchases in the last 12 months: "
                f"{shop_names}. Consider maintaining this restriction."
            )

        risky_shops = shop_summary[(shop_summary["overpay"] > 0) & (shop_summary["last_used"] > cutoff)]
        risky_shops = risky_shops.nlargest(2, "overpay")
        for _, shop in risky_shops.iterrows():
            recommendations.append(
                {
                    "text": (
                        f"Use **{shop['Shop']}** only when the price is at or below the reference: "
                        f"{shop['overpay_count']} of {shop['transactions']} transactions involved overpayment "
                        f"(total {format_rupiah(shop['overpay'])}); last used {shop['last_used']:%d %b %Y}."
                    ),
                    "est": 0,
                }
            )

    recent_transactions = select_window(frame, as_of, 12)
    if not recent_transactions.empty and recent_transactions["Overpay"].max() > 0:
        largest_overpay = recent_transactions.loc[recent_transactions["Overpay"].idxmax()]
        conclusions.append(
            f"Largest overpayment in the last 12 months: **{largest_overpay['Item']}** at "
            f"{largest_overpay['Shop']} on {largest_overpay['Date']:%d %b %Y} "
            f"({format_rupiah(largest_overpay['Overpay'])} above the reference)."
        )

    top_items = (
        frame.groupby("Item")
        .agg(total=("Amount", "sum"), reference=("Reference", "last"))
        .nlargest(3, "total")
    )
    price_limits = ", ".join(
        f"{item}: {format_rupiah(row['reference'])}" for item, row in top_items.iterrows()
    )
    if stats["recent_overpay"] > 0:
        recommendations.append(
            {
                "text": (
                    f"Set a **maximum purchase price** in advance: {price_limits}. "
                    f"Overpayments in the last 12 months totalled {format_rupiah(stats['recent_overpay'])}."
                ),
                "est": stats["recent_overpay"],
            }
        )
    elif price_limits:
        recommendations.append(
            {
                "text": (
                    f"Maintain disciplined pricing (maximum reference prices: {price_limits}); "
                    "there has been no overpayment in the last 12 months."
                ),
                "est": 0,
            }
        )

    promotional_transactions = frame[
        frame["Reference"].notna() & (frame["Savings"] >= 0.2 * frame["Reference"])
    ]
    if not promotional_transactions.empty:
        promotion_summary = (
            promotional_transactions.groupby("Item")
            .agg(
                purchases=("Amount", "size"),
                median_price=("Amount", "median"),
                last_purchase=("Date", "max"),
                most_common_shop=("Shop", lambda values: values.mode().iloc[0] if not values.mode().empty else "Unknown"),
            )
            .nlargest(1, "purchases")
        )
        for item, promotion in promotion_summary.iterrows():
            if promotion["purchases"] >= 2:
                recommendations.append(
                    {
                        "text": (
                            f"**{item}** has been purchased at least 20% below its reference price "
                            f"{promotion['purchases']} times (median {format_rupiah(promotion['median_price'])}; "
                            f"most often at {promotion['most_common_shop']}; last purchased "
                            f"{promotion['last_purchase']:%d %b %Y}). Consider waiting for another promotion "
                            "when your stock allows."
                        ),
                        "est": 0,
                    }
                )
    return conclusions, recommendations


def build_boosting_insights(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Generate conclusions and savings recommendations for boosting services.

    Args:
        frame: Cleaned boosting transactions.
        as_of: Date used for rolling 12-month comparisons.

    Returns:
        A pair containing conclusion strings and recommendation dictionaries.
    """
    conclusions: list[str] = []
    recommendations: list[dict[str, Any]] = []
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

    cutoff = as_of - pd.DateOffset(months=12)
    account_care = frame[
        (frame["Content"] == "Account care") & frame["Days"].notna() & (frame["Days"] > 0)
    ].copy()
    if not account_care.empty:
        account_care["Daily rate"] = account_care["Amount"] / account_care["Days"]
        cheapest_rate = account_care["Daily rate"].min()
        highest_rate = account_care["Daily rate"].max()
        cheapest_provider = account_care.loc[account_care["Daily rate"].idxmin(), "Provider"]
        conclusions.append(
            f"Account care daily rates range from {format_rupiah(cheapest_rate)} to "
            f"{format_rupiah(highest_rate)}; the lowest recorded rate was from **{cheapest_provider}**."
        )
        recent_account_care = account_care[account_care["Date"] > cutoff]
        estimated_saving = (
            (recent_account_care["Daily rate"] - cheapest_rate) * recent_account_care["Days"]
        ).clip(lower=0).sum()
        if estimated_saving > 0:
            recommendations.append(
                {
                    "text": (
                        f"For **account care**, compare daily rates before booking. Applying the lowest recorded "
                        f"rate ({format_rupiah(cheapest_rate)} per day from {cheapest_provider}) to bookings "
                        "made in the last 12 months would reduce costs."
                    ),
                    "est": estimated_saving,
                }
            )

    estimated_recurring_saving = 0.0
    provider_notes = []
    for service in RECURRING_SERVICES:
        service_frame = frame[frame["Content"] == service]
        if len(service_frame) < 3:
            continue

        median_price = service_frame["Amount"].median()
        recent_service = service_frame[service_frame["Date"] > cutoff]
        estimated_recurring_saving += (recent_service["Amount"] - median_price).clip(lower=0).sum()

        provider_prices = service_frame.groupby("Provider")["Amount"].agg(median="median", transactions="size")
        repeat_providers = provider_prices[provider_prices["transactions"] >= 2]
        if len(repeat_providers) >= 2:
            lowest_provider = repeat_providers["median"].idxmin()
            lowest_median = repeat_providers["median"].min()
            provider_notes.append(f"{service}: {lowest_provider} (median {format_rupiah(lowest_median)})")

    if estimated_recurring_saving > 0 or provider_notes:
        recommendation_text = (
            "For recurring services ("
            + ", ".join(RECURRING_SERVICES)
            + "), use the median price as a benchmark and challenge quotes that are materially higher."
        )
        if provider_notes:
            recommendation_text += " Lowest-priced repeat providers: " + "; ".join(provider_notes) + "."
        recommendations.append({"text": recommendation_text, "est": estimated_recurring_saving})

    if one_time_providers >= 5:
        recommendations.append(
            {
                "text": (
                    f"Reduce one-off provider use ({one_time_providers} providers had only one transaction). "
                    "Consider keeping two or three regular providers to make prices easier to compare."
                ),
                "est": 0,
            }
        )
    return conclusions, recommendations


# ---------------------------------------------------------------------------
# Dashboard sections
# ---------------------------------------------------------------------------
def render_overview(data: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> None:
    """Render the cross-category overview, spending trends, and key insights.

    Args:
        data: Cleaned data frames keyed by game or service category.
        as_of: Latest transaction date across all available categories.

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
    average_last_12 = select_window(all_transactions, as_of, 12)["Amount"].sum() / 12
    average_previous_12 = select_window(all_transactions, as_of, 24, 12)["Amount"].sum() / 12

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
        for _, recommendations in insights.values()
        for recommendation in recommendations
    )
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

    average_tone = "bad" if average_last_12 > average_previous_12 > 0 else "good"
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
        "Estimated saving potential · 12 months",
        format_rupiah(estimated_total_saving),
        "Based on the recommendations below",
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
        create_spending_chart(all_transactions, selected_period, "Category", CATEGORY_COLOURS),
        key="overview_spending_chart",
        use_container_width=True,
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
        margin={"l": 8, "r": 8, "t": 12, "b": 8},
        legend={"orientation": "h", "y": -0.1},
        separators=",.",
    )
    right.plotly_chart(donut, key="overview_category_share", use_container_width=True, theme="streamlit")

    st.subheader("Key takeaways and recommendations")
    category_shares = " · ".join(
        f"{category}: {frame['Amount'].sum() / total_spend:.0%}"
        for category, frame in data.items()
    ) if total_spend else "No category spend recorded"
    top_lines = [f"Total spend: **{format_rupiah(total_spend)}** ({category_shares})."]
    if previous_year_to_date > 0:
        change = year_to_date / previous_year_to_date - 1
        direction = "increased" if change > 0 else "decreased" if change < 0 else "remained unchanged"
        top_lines.append(
            f"Spend in {current_year} to {as_of:%d %b}: **{format_rupiah(year_to_date)}**, "
            f"{direction} by {abs(change):.0%} against the comparable period in {current_year - 1}."
        )
    if recent_top_up_spend:
        top_lines.append(
            f"Top-up overpayments in the last 12 months: **{format_rupiah(recent_overpay)}** "
            f"({overpay_share:.1%} of top-up spend)."
        )
    render_bullets(top_lines)

    conclusion_column, recommendation_column = st.columns(2)
    conclusion_column.markdown("#### Conclusions")
    recommendation_column.markdown("#### Next cost-saving actions")
    for category, (conclusions, recommendations) in insights.items():
        with conclusion_column:
            st.markdown(f"**{category}**")
            render_bullets(conclusions[:3])
        with recommendation_column:
            st.markdown(f"**{category}**")
            render_bullets(format_recommendations(recommendations, 3))

    st.caption(
        "The top-up reference is taken from the worksheet's '70% Value' column. "
        "Estimated savings are indicative calculations based on transactions from the last 12 months, not guarantees."
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
        use_container_width=True,
        theme="streamlit",
    )
    right.markdown("**Overpayment vs savings**")
    right.plotly_chart(
        create_balance_chart(frame, selected_period),
        key=f"balance_{game_name}",
        use_container_width=True,
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
        use_container_width=True,
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
        use_container_width=True,
        column_config={
            "Hit rate": st.column_config.ProgressColumn("Hit rate", min_value=0, max_value=100, format="%.0f%%")
        },
    )

    st.subheader("Promotions and overpayments")
    promotion_column, overpay_column = st.columns(2)
    promotions = frame[
        frame["Reference"].notna() & (frame["Savings"] >= 0.2 * frame["Reference"])
    ][["Date", "Item", "Shop", "Amount", "Savings"]]
    promotion_column.markdown("**Promotional purchases · at least 20% below reference**")
    promotion_column.dataframe(
        format_table(promotions.sort_values("Date", ascending=False), ["Amount", "Savings"]),
        hide_index=True,
        use_container_width=True,
    )
    largest_overpayments = frame[frame["Overpay"] > 0].nlargest(10, "Overpay")[
        ["Date", "Item", "Shop", "Amount", "Overpay"]
    ]
    overpay_column.markdown("**10 largest overpayments**")
    overpay_column.dataframe(
        format_table(largest_overpayments, ["Amount", "Overpay"]),
        hide_index=True,
        use_container_width=True,
    )

    conclusions, recommendations = build_game_insights(frame, as_of)
    st.subheader("Conclusions and recommendations")
    conclusion_column, recommendation_column = st.columns(2)
    conclusion_column.markdown("#### Conclusions")
    with conclusion_column:
        render_bullets(conclusions)
    recommendation_column.markdown("#### Cost-saving actions")
    with recommendation_column:
        render_bullets(format_recommendations(recommendations))

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
            use_container_width=True,
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
        use_container_width=True,
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
        color_discrete_sequence=[ACCENT_COLOUR],
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
        use_container_width=True,
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
        use_container_width=True,
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
            use_container_width=True,
            theme="streamlit",
        )

    conclusions, recommendations = build_boosting_insights(frame, as_of)
    st.subheader("Conclusions and recommendations")
    conclusion_column, recommendation_column = st.columns(2)
    conclusion_column.markdown("#### Conclusions")
    with conclusion_column:
        render_bullets(conclusions)
    recommendation_column.markdown("#### Cost-saving actions")
    with recommendation_column:
        render_bullets(format_recommendations(recommendations))

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
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Configure the Streamlit page, load data, and render dashboard tabs.

    Returns:
        None. This function runs the complete Streamlit application.
    """
    st.set_page_config(
        page_title="Game Spending Dashboard",
        page_icon="🎮",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    st.sidebar.button(
        "Refresh data",
        on_click=st.cache_data.clear,
        help="Clear the cached Google Sheets data and reload the dashboard.",
        use_container_width=True,
    )

    try:
        data = load_sheet_data()
    except Exception as error:  # Display a useful message while retaining diagnostics.
        st.error("Unable to load Google Sheets data. Check the sheet ID, service-account secrets, and worksheet access.")
        with st.expander("Technical details"):
            st.exception(error)
        st.stop()

    if not data:
        st.warning(
            f"No recognised transaction tables were found in the '{WORKSHEET_NAME}' worksheet. "
            "Each table must start with 'No' or 'No.' in column A and 'Date' in column B."
        )
        st.stop()

    available_dates = [frame["Date"].max() for frame in data.values() if not frame.empty]
    if not available_dates:
        st.warning("The recognised tables do not contain any valid dated transactions.")
        st.stop()
    as_of = max(available_dates)

    category_list = ", ".join(data)
    safe_category_list = html.escape(category_list)
    st.markdown(
        '<div class="dashboard-header">'
        '<div class="dashboard-eyebrow">Personal finance · Transaction analysis</div>'
        '<div class="dashboard-title">Game spending dashboard</div>'
        f'<div class="dashboard-subtitle">Latest transaction: {as_of:%d %B %Y} · '
        f'Data available: {safe_category_list}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    tab_labels = ["Overview", *data.keys()]
    tabs = st.tabs(tab_labels)
    with tabs[0]:
        render_overview(data, as_of)

    for tab, (category, frame) in zip(tabs[1:], data.items()):
        with tab:
            if category == BOOSTING_CATEGORY:
                render_boosting_page(frame, as_of)
            else:
                render_game_page(frame, category, as_of)


if __name__ == "__main__":
    main()
