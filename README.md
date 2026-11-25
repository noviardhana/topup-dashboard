# Game Spending Dashboard

A Streamlit dashboard for analysing personal spending on Genshin Impact, Honkai: Star Rail and game-boosting services. Transactions are read from a Google Sheets worksheet, cleaned, and presented as summary metrics, spending trends, shop and provider comparisons, and ranked cost-saving recommendations.

## Features

- **Overview tab** with year-to-date and rolling 12-month spend, top-up overpayment, boosting share, and estimated avoidable spend (shown both gross and confidence-weighted).
- **One tab per game** with overpayment versus savings against the `70% Value` reference, hit rate, net position by shop, an item summary, promotional purchases, and the largest overpayments.
- **Boosting tab** with provider and service-type breakdowns and account-care daily rates.
- **Recommendations** ranked by estimated impact, evidence strength and recency. Each one states its basis (`70% Value` or `Peer median`) and, where available, the cheapest eligible alternative shop or provider.
- **Sidebar controls**: date-range filter (applies to every tab), monthly budget line on the overview chart, and a data refresh button.
- **CSV download** of the filtered transactions on each detail tab.
- **Optional password gate** driven by Streamlit secrets.

## Project structure

```
.
├── app.py                      # Entry point: page setup, sidebar, filters, tabs
└── game_dashboard/
    ├── __init__.py
    ├── config.py               # Constants, palette, source column names, CSS
    ├── formatting.py           # Rupiah, number, date, version and table helpers
    ├── data.py                 # Google Sheets loading and cleaning
    ├── charts.py               # Period grouping and Plotly charts
    ├── benchmarks.py           # Windows, statistics, peer benchmarks, savings estimates
    ├── recommendations.py      # Candidate generation, scoring and ranking
    ├── insights.py             # Conclusion text for game and boosting pages
    ├── components.py           # KPI cards, bullets, recommendations, methodology popover
    ├── auth.py                 # Optional password gate
    ├── page_overview.py        # Overview tab
    ├── page_game.py            # Game detail tab
    └── page_boosting.py        # Boosting detail tab
```

`app.py` must sit next to the `game_dashboard/` folder, and `game_dashboard/__init__.py` must be present.

## Requirements

The application imports the following third-party packages:

- `streamlit`
- `gspread`
- `pandas`
- `plotly`

List them in a `requirements.txt` file if you deploy to Streamlit Community Cloud.

## Configuration

Define the following in `.streamlit/secrets.toml` (locally) or in the app's secrets settings (Streamlit Cloud):

```toml
sheet_id = "your-google-sheet-id"

# Optional: require a password before any data is loaded.
app_password = "choose-a-strong-password"

[gcp_service_account]
# Paste the fields of your Google service-account JSON key here.
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "..."
client_email = "..."
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

Share the spreadsheet with the service account's `client_email` so that it can read the worksheet.

If `app_password` is not defined, the dashboard is **not** password-protected. Because this is personal financial data, either set the password or make the deployed app private.

Never commit `secrets.toml` to version control.

## Worksheet format

The data is read from a worksheet named `Hoyoverse`. Each table must start with a header row that has `No` (or `No.`) in column A and `Date` in column B. Rows continue until the first cell in column A is no longer a number.

| Table type | Required columns | Optional columns |
|---|---|---|
| Game top-ups | `Date`, `Amount`, `70% Value`, `Version`, `Item`, `Toko` | any others |
| Boosting | `Date`, `Amount`, `Version`, `Joki`, `Content` | `Keterangan` |

Notes:

- Column names follow the existing Indonesian worksheet schema and must not be renamed unless the constants in `game_dashboard/config.py` are changed too.
- A table containing `Joki` is treated as boosting; a table containing `Item` is treated as a game.
- Game tables are assigned to Genshin and then HSR in the order in which they appear in the worksheet.
- In boosting tables, a duration such as `(14 hari)` or `(14 days)` in `Keterangan` is read as the number of days. The `Rawat Akun` content label is shown as `Account care`.
- Amounts and dates in Indonesian or international formats are both accepted.

## Running locally

```bash
pip install streamlit gspread pandas plotly
streamlit run app.py
```

Fetched data is cached for five minutes. Use **Refresh data** in the sidebar to reload it immediately.

## Deploying to Streamlit Community Cloud

1. Push `app.py`, the whole `game_dashboard/` folder (including `__init__.py`) and `requirements.txt` to your repository.
2. Create the app from that repository, with `app.py` as the main file.
3. Add the secrets described above under the app's settings.
4. After any change to the folder structure, use **Reboot app** from **Manage app**.

## How the recommendations work

- Benchmarks use up to 24 months of history.
- A peer comparison needs at least two alternative shops or providers, each with at least two transactions. Each peer carries equal weight, regardless of how often it was used.
- Game purchases are compared with the lower of the worksheet's `70% Value` and the eligible peer median. Account care is compared by daily rate; other recurring services (`VIP IT`, `VIP Spiral`, `Stygian Onslaught`) are compared per booking.
- Avoidable spend is the amount paid above the target over the last 12 months. It is a historical comparison, not a guaranteed future saving.
- Evidence is labelled `strong`, `moderate` or `limited`. This is a heuristic based on sample size, number of peers and price dispersion, and is not a probability.
- Priority (High, Medium, Monitor) is relative within each tab and weighted by evidence and recency. Estimates below 2% of 12-month spend are always labelled Monitor.

The same explanation is available in the app through the **Methodology** popover.

## Limitations

- Recommendations depend on the quality and completeness of the worksheet. Missing reference prices or too few comparable purchases mean savings cannot be assessed, and the app says so rather than implying that none exist.
- The date-range filter also limits the history used for benchmarks, so a short range may reduce the number of eligible peers.
- The monthly budget line appears on the overview chart only, and not on the game or boosting tabs.
