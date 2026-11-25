"""Google Sheets access and cleaning of game and boosting transaction tables."""

from __future__ import annotations

import re

import gspread
import pandas as pd
import streamlit as st

from .config import (
    BOOSTING_CATEGORY,
    GAME_ORDER,
    SOURCE_DESCRIPTION_COLUMN,
    SOURCE_PROVIDER_COLUMN,
    SOURCE_REFERENCE_COLUMN,
    SOURCE_SHOP_COLUMN,
    WORKSHEET_NAME,
)
from .formatting import to_date, to_number


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


def _is_row_number(text: str) -> bool:
    """Return True when a cell holds a row number such as ``12`` or ``12.0``."""
    try:
        return float(text).is_integer()
    except ValueError:
        return False


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

        headers = [str(value).strip() for value in row]
        while headers and not headers[-1]:
            headers.pop()

        body = []
        for candidate in rows[row_index + 1 :]:
            first_cell = str(candidate[0]).strip() if candidate else ""
            if not _is_row_number(first_cell):
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
