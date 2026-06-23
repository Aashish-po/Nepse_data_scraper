# NEPSE Daily Data Scraper

Pulls daily share price / floor sheet data from three sources and saves clean CSV files locally. No Selenium, no Chrome driver — just `requests` + `BeautifulSoup`.

---

## Sources

| Source | Data | URL |
|---|---|---|
| **ShareSansar** | Today's Share Price (OHLCV, VWAP, turnover) | `sharesansar.com/today-share-price` |
| **Merolagani** | Floor Sheet (transaction-level: buyer, seller, qty, rate) | `merolagani.com/Floorsheet.aspx` |
| **NepseTrading** | Daily price summary | `nepsetrading.com` |

---

## Setup

```bash
# 1. Create a virtual environment (recommended)
python -m venv venv

# Windows / Git Bash
source venv/Scripts/activate

# Mac / Linux
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
```

---

## Usage

```bash
# Scrape the last 14 trading days from ALL sources
python scraper.py

# Scrape today only (all sources)
python scraper.py --today

# Scrape a specific source
python scraper.py --source sharesansar
python scraper.py --source merolagani
python scraper.py --source nepsetrading

# Custom date range
python scraper.py --start 2025-01-01 --end 2025-03-31

# One source + date range
python scraper.py --source sharesansar --start 2025-06-01 --end 2025-06-20

# Force re-download (overwrite existing CSVs)
python scraper.py --today --force
```

---

## Output Structure

```
data/
├── sharesansar/
│   ├── 2025-06-15.csv      ← Sunday (trading day)
│   ├── 2025-06-16.csv
│   └── ...
├── merolagani/
│   ├── 2025-06-15.csv
│   └── ...
└── nepsetrading/
    ├── 2025-06-15.csv
    └── ...
```

> **Note:** NEPSE trades **Sunday–Thursday**. The scraper automatically skips Fridays and Saturdays.  
> Public holidays are handled gracefully — the scraper logs a warning and moves on when a site returns no data.

---

## CSV Columns

### ShareSansar (`data/sharesansar/`)

| Column | Description |
|---|---|
| `date` | Trading date (YYYY-MM-DD) |
| `symbol` | Stock ticker |
| `conf` | Number of confirmed transactions |
| `open` | Opening price |
| `high` | Day high |
| `low` | Day low |
| `close` | Closing price |
| `vwap` | Volume-weighted average price |
| `volume` | Total shares traded |
| `prev_close` | Previous closing price |
| `turnover` | Total turnover (NPR) |
| `transactions` | Number of transactions |
| `diff` | Price change |
| `diff_pct` | Price change % |

### Merolagani (`data/merolagani/`)

| Column | Description |
|---|---|
| `date` | Trading date |
| `Transaction No` | Unique floor sheet transaction ID |
| `Symbol` | Stock ticker |
| `Buyer` | Buyer broker code |
| `Seller` | Seller broker code |
| `Quantity` | Shares traded |
| `Rate` | Trade price |
| `Amount` | Total transaction value |

---

## Troubleshooting

### "Expected JSON from ShareSansar, got text/html"

The site changed its AJAX endpoint. Open `scraper.py`, find `SharesansarScraper`, and:

1. Open `https://sharesansar.com/today-share-price` in Chrome
2. Open DevTools → Network → Filter by `XHR/Fetch`
3. Select a date → observe the request URL and payload
4. Update `PAGE_URL` and `AJAX_COLS` to match

### Merolagani pagination stops early

The ASP.NET pager structure may have changed. Check `_parse_total_pages()` — verify the `<span id>` matches the live page source.

### NepseTrading returns empty data

NepseTrading's URL structure changes more frequently. Update the `_candidates` list in `NepseTradingScraper` to match the current routing.

### General 403 / rate-limit errors

The scraper already includes per-page and per-date courtesy delays. If you still hit blocks:

- Increase `INTER_DATE_DELAY` at the top of `scraper.py`
- Run for fewer days at a time
- Run during off-peak hours (avoid during NEPSE market hours 11:00–15:00 NPT)

---

## Automation (optional)

To run daily after market close (≈15:30 NPT), add to Windows Task Scheduler or a cron job:

```bash
# cron (Linux/Mac) — runs at 10:00 UTC (≈15:45 NPT) Sun–Thu
0 10 * * 0-4 cd /path/to/nepse_scraper && source venv/bin/activate && python scraper.py --today
```


