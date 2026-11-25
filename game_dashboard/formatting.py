"""Parsing and formatting helpers for rupiah values, numbers, dates, versions, and tables."""

from __future__ import annotations

import numbers
import re
from typing import Any

import pandas as pd


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
        """Convert one Google Sheets date serial or date label to a timestamp.

        Args:
            value: A serial number, supported date string, or missing value.

        Returns:
            A pandas timestamp, or NaT when the value cannot be parsed.
        """
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
