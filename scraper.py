#!/usr/bin/env python3
"""
NEPSE Daily Data Scraper
========================
Pulls daily floor sheet / today's share price from:
  • ShareSansar  (www.sharesansar.com/today-share-price)
  • Merolagani   (merolagani.com/Floorsheet.aspx)
  • NepseTrading (nepsetrading.com)

Usage
-----
  python scraper.py                          # last 7 trading days, all sources
  python scraper.py --today                  # today only, all sources
  python scraper.py --source sharesansar     # one source, default date range
  python scraper.py --start 2025-01-01 --end 2025-03-31
  python scraper.py --source merolagani --today

Output
------
  data/sharesansar/YYYY-MM-DD.csv
  data/merolagani/YYYY-MM-DD.csv
  data/nepsetrading/YYYY-MM-DD.csv
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from abc import ABC, abstractmethod
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd
import requests
from bs4 import BeautifulSoup

# ─── Configuration ────────────────────────────────────────────────────────────

DATA_DIR = Path("data")
REQUEST_TIMEOUT = 30  # seconds per request
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0  # base seconds; doubled on each retry
INTER_PAGE_DELAY = 0.8  # polite delay between paginated requests
INTER_DATE_DELAY = 1.5  # polite delay between different trading days

# NEPSE trades Mon–Fri (weekdays).
# Python weekday(): Mon=0 … Sun=6  →  trading: 0,1,2,3,4
TRADING_WEEKDAYS: set[int] = {0, 1, 2, 3, 4}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)


# ─── Utility ─────────────────────────────────────────────────────────────────


def is_trading_day(d: date) -> bool:
    return d.weekday() in TRADING_WEEKDAYS


def trading_days_in_range(start: date, end: date) -> list[date]:
    out, cur = [], start
    while cur <= end:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _retry(fn, attempts: int = RETRY_ATTEMPTS, backoff: float = RETRY_BACKOFF):
    """Call fn() up to `attempts` times with exponential backoff."""
    for n in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if n == attempts:
                raise
            wait = backoff * (2 ** (n - 1))
            logging.getLogger("nepse").warning(
                f"Attempt {n}/{attempts} failed ({exc}). Retrying in {wait:.1f}s …"
            )
            time.sleep(wait)


# ─── Base Scraper ─────────────────────────────────────────────────────────────


class BaseScraper(ABC):
    name: str = ""

    def __init__(self) -> None:
        self.out_dir = DATA_DIR / self.name
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger(f"nepse.{self.name}")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})

    # ── public interface ──

    def run(self, dates: list[date]) -> dict[date, int]:
        """Scrape each date; return {date: row_count} for saved dates."""
        results: dict[date, int] = {}
        total = len(dates)
        for idx, d in enumerate(dates, 1):
            self.log.info(f"[{idx}/{total}] {d}")
            if self._already_saved(d):
                self.log.info(f"  ↳ already have {self._csv_path(d).name}, skipping")
                continue
            try:
                df = _retry(lambda d=d: self._scrape(d))
            except Exception as exc:
                self.log.error(f"  ↳ gave up on {d}: {exc}")
                df = None
            if df is not None and not df.empty:
                self._save(df, d)
                results[d] = len(df)
            else:
                self.log.warning(
                    f"  ↳ no data returned for {d} (holiday / no trading?)"
                )
            if idx < total:
                time.sleep(INTER_DATE_DELAY)
        return results

    # ── helpers ──

    def _csv_path(self, d: date) -> Path:
        return self.out_dir / f"{d.isoformat()}.csv"

    def _already_saved(self, d: date) -> bool:
        p = self._csv_path(d)
        return p.exists() and p.stat().st_size > 120  # non-trivial file

    def _save(self, df: pd.DataFrame, d: date) -> None:
        path = self._csv_path(d)
        df.to_csv(path, index=False)
        self.log.info(f"  ↳ saved {len(df):,} rows → {path}")

    # ── subclass contract ──

    @abstractmethod
    def _scrape(self, d: date) -> Optional[pd.DataFrame]:
        """Fetch data for one trading day; return None / empty DF on no data."""
        ...


# ─── ShareSansar ──────────────────────────────────────────────────────────────


class SharesansarScraper(BaseScraper):
    """
    Today's Share Price via the DataTables server-side AJAX endpoint.
    No Selenium required – we replicate the XHR call directly.

    If the AJAX endpoint format ever changes, set AJAX_COLS to match the
    column `data` keys visible in browser DevTools → Network → XHR.
    """

    name = "sharesansar"
    PAGE_URL = "https://www.sharesansar.com/today-share-price"
    PAGE_SIZE = 500  # large page to minimise round-trips

    # Column `data` keys as configured in the DataTable
    AJAX_COLS = [
        "sno",
        "symbol",
        "conf",
        "open",
        "high",
        "low",
        "close",
        "vwap",
        "volume",
        "prev_close",
        "turnover",
        "transactions",
        "diff",
        "range",
        "diff_pct",
    ]

    def _warm_session(self) -> None:
        """GET the page first to acquire session cookie."""
        r = self.session.get(self.PAGE_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()

    def _ajax_payload(self, d: date, draw: int, start: int) -> dict:
        payload: dict = {
            "draw": str(draw),
            "start": str(start),
            "length": str(self.PAGE_SIZE),
            "search[value]": "",
            "search[regex]": "false",
            # Date param – Sharesansar accepts YYYY-MM-DD on the AJAX call
            "fromdate": d.strftime("%Y-%m-%d"),
        }
        for i, col in enumerate(self.AJAX_COLS):
            payload[f"columns[{i}][data]"] = col
            payload[f"columns[{i}][name]"] = ""
            payload[f"columns[{i}][searchable]"] = "true"
            payload[f"columns[{i}][orderable]"] = "true"
            payload[f"columns[{i}][search][value]"] = ""
            payload[f"columns[{i}][search][regex]"] = "false"
        return payload

    def _fetch_page(self, d: date, draw: int, start: int) -> dict:
        r = self.session.post(
            self.PAGE_URL,
            data=self._ajax_payload(d, draw, start),  # type: ignore
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.PAGE_URL,
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if "json" not in ct:
            raise ValueError(
                f"Expected JSON from ShareSansar, got {ct!r}. "
                "The site layout may have changed – check browser DevTools "
                "for the live XHR request format."
            )
        return r.json()

    def _scrape(self, d: date) -> Optional[pd.DataFrame]:
        self._warm_session()
        time.sleep(0.4)

        first = self._fetch_page(d, draw=1, start=0)
        total = int(first.get("recordsTotal", 0))
        if total == 0:
            return None

        self.log.info(
            f"  ↳ ShareSansar: {total:,} records across "
            f"≈{-(-total // self.PAGE_SIZE)} page(s)"
        )

        rows = list(first.get("data", []))
        start, draw = self.PAGE_SIZE, 2
        while start < total:
            page = self._fetch_page(d, draw=draw, start=start)
            rows.extend(page.get("data", []))
            start += self.PAGE_SIZE
            draw += 1
            time.sleep(INTER_PAGE_DELAY)

        if not rows:
            return None

        df = pd.DataFrame(rows)
        keep = [c for c in self.AJAX_COLS if c in df.columns and c != "sno"]
        df = df[keep].copy()
        # Strip commas from numeric strings ("1,234.50" → "1234.50")
        for col in df.columns:
            if col != "symbol":
                df[col] = df[col].astype(str).str.replace(",", "", regex=False)
        df.insert(0, "date", d.isoformat())
        return df


# ─── Merolagani ───────────────────────────────────────────────────────────────


class MerolaganiScraper(BaseScraper):
    """
    Floor Sheet via merolagani.com/Floorsheet.aspx.
    ASP.NET WebForms: GET → extract ViewState tokens → POST with date.
    Re-extracts ViewState from each response before submitting the next page.
    """

    name = "merolagani"
    URL = "https://merolagani.com/Floorsheet.aspx"

    # ── ViewState helpers ──

    def _extract_vs(self, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")

        def _v(fid: str) -> str:
            tag = soup.find("input", {"id": fid})  # type: ignore
            return str(tag["value"]) if tag else ""

        return {
            "__VIEWSTATE": _v("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _v("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _v("__EVENTVALIDATION"),
        }

    def _build_search_payload(self, vs: dict, d: date) -> dict:
        return {
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$lbtnSearchFloorsheet",
            "__EVENTARGUMENT": "",
            **vs,
            "ctl00$Hidden1": "",
            "ctl00$ASCompany$hdnAutoSuggest": "0",
            "ctl00$ASCompany$txtAutoSuggest": "",
            "ctl00$hdnNewsList": "",
            "ctl00$AutoSuggest1$hdnAutoSuggest": "0",
            "ctl00$AutoSuggest1$txtAutoSuggest": "",
            "ctl00$txtNews": "",
            "ctl00$ContentPlaceHolder1$ASCompanyFilter$hdnAutoSuggest": "0",
            "ctl00$ContentPlaceHolder1$ASCompanyFilter$txtAutoSuggest": "",
            "ctl00$ContentPlaceHolder1$txtBuyerBrokerCodeFilter": "",
            "ctl00$ContentPlaceHolder1$txtSellerBrokerCodeFilter": "",
            "ctl00$ContentPlaceHolder1$txtFloorsheetDateFilter": d.strftime("%m/%d/%Y"),
            "ctl00$ContentPlaceHolder1$PagerControl1$hdnPCID": "PC1",
            "ctl00$ContentPlaceHolder1$PagerControl1$hdnCurrentPage": "0",
            "ctl00$ContentPlaceHolder1$PagerControl2$hdnPCID": "PC2",
            "ctl00$ContentPlaceHolder1$PagerControl2$hdnCurrentPage": "0",
        }

    def _build_page_payload(self, vs: dict, d: date, page: int) -> dict:
        """Payload for clicking the 'Next' page link."""
        return {
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$PagerControl1$lbtnNext",
            "__EVENTARGUMENT": "",
            **vs,
            "ctl00$Hidden1": "",
            "ctl00$ASCompany$hdnAutoSuggest": "0",
            "ctl00$ASCompany$txtAutoSuggest": "",
            "ctl00$hdnNewsList": "",
            "ctl00$AutoSuggest1$hdnAutoSuggest": "0",
            "ctl00$AutoSuggest1$txtAutoSuggest": "",
            "ctl00$txtNews": "",
            "ctl00$ContentPlaceHolder1$ASCompanyFilter$hdnAutoSuggest": "0",
            "ctl00$ContentPlaceHolder1$ASCompanyFilter$txtAutoSuggest": "",
            "ctl00$ContentPlaceHolder1$txtBuyerBrokerCodeFilter": "",
            "ctl00$ContentPlaceHolder1$txtSellerBrokerCodeFilter": "",
            "ctl00$ContentPlaceHolder1$txtFloorsheetDateFilter": d.strftime("%m/%d/%Y"),
            "ctl00$ContentPlaceHolder1$PagerControl1$hdnPCID": "PC1",
            "ctl00$ContentPlaceHolder1$PagerControl1$hdnCurrentPage": str(page),
            "ctl00$ContentPlaceHolder1$PagerControl2$hdnPCID": "PC2",
            "ctl00$ContentPlaceHolder1$PagerControl2$hdnCurrentPage": str(page),
        }

    # ── parsing helpers ──

    def _parse_total_pages(self, html: str) -> int:
        soup = BeautifulSoup(html, "html.parser")
        # Try: "Page 1 of 42" in the pager label
        for span_id in (
            "ctl00_ContentPlaceHolder1_PagerControl1_litRecordCount",
            "ctl00_ContentPlaceHolder1_PagerControl2_litRecordCount",
        ):
            tag = soup.find("span", {"id": span_id})  # type: ignore
            if tag:
                m = re.search(r"of\s+(\d+)", tag.get_text())
                if m:
                    return int(m.group(1))
        # Fallback: count numbered page links
        pager = soup.find("div", {"class": re.compile(r"pager", re.I)})  # type: ignore
        if pager:
            links = pager.find_all("a")
            nums = [
                int(a.get_text(strip=True))
                for a in links
                if a.get_text(strip=True).isdigit()
            ]
            if nums:
                return max(nums)
        return 1

    def _parse_table(self, html: str) -> pd.DataFrame:
        soup = BeautifulSoup(html, "html.parser")
        # Known table id; fall back to any <table> with data rows
        table = (
            soup.find(
                "table", {"id": "ctl00_ContentPlaceHolder1_StockTradeVolumeTable"}
            )
            or soup.find("table", {"id": re.compile(r"Floorsheet|floorsheet", re.I)})
            or soup.find("table", class_=lambda c: bool(c) and "table" in c)
        )
        if not table:
            return pd.DataFrame()

        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        tbody = table.find("tbody") or table
        rows = [
            [td.get_text(strip=True) for td in tr.find_all("td")]
            for tr in tbody.find_all("tr")
            if tr.find("td")
        ]
        if not rows:
            return pd.DataFrame()
        n_cols = len(rows[0])
        col_names = headers[:n_cols] if headers else [f"col{i}" for i in range(n_cols)]
        return pd.DataFrame(rows, columns=col_names)

    # ── core ──

    def _scrape(self, d: date) -> Optional[pd.DataFrame]:
        # 1. GET → ViewState
        r0 = self.session.get(self.URL, timeout=REQUEST_TIMEOUT)
        r0.raise_for_status()
        vs = self._extract_vs(r0.text)
        time.sleep(0.4)

        # 2. POST search
        r1 = self.session.post(
            self.URL,
            data=self._build_search_payload(vs, d),  # type: ignore
            headers={"Referer": self.URL},
            timeout=REQUEST_TIMEOUT,
        )
        r1.raise_for_status()

        html = r1.text
        if "No record found" in html or "no record" in html.lower():
            return None

        vs = self._extract_vs(html)
        total_pages = self._parse_total_pages(html)
        self.log.info(f"  ↳ Merolagani: {total_pages} page(s) for {d}")

        dfs: list[pd.DataFrame] = []
        page_df = self._parse_table(html)
        if not page_df.empty:
            dfs.append(page_df)

        # 3. Paginate
        for page in range(1, total_pages):
            time.sleep(INTER_PAGE_DELAY)
            rn = self.session.post(
                self.URL,
                data=self._build_page_payload(vs, d, page),  # type: ignore
                headers={"Referer": self.URL},
                timeout=REQUEST_TIMEOUT,
            )
            rn.raise_for_status()
            vs = self._extract_vs(rn.text)
            df_p = self._parse_table(rn.text)
            if not df_p.empty:
                dfs.append(df_p)

        if not dfs:
            return None

        final = pd.concat(dfs, ignore_index=True).drop_duplicates()
        final.insert(0, "date", d.isoformat())
        return final


# ─── NepseTrading ─────────────────────────────────────────────────────────────


class NepseTradingScraper(BaseScraper):
    """
    Daily price data from nepsetrading.com.

    Tries two strategies in order:
      1. JSON REST endpoint (if the site exposes one)
      2. HTML table parsing (universal fallback)

    Adjust CANDIDATES if the URL structure changes.
    """

    name = "nepsetrading"

    # Ordered list of (url, params_factory) to try
    @property
    def _candidates(self):
        return [
            (
                "https://nepsetrading.com/trading/daily-price",
                lambda d: {"date": d.strftime("%Y-%m-%d")},
            ),
            (
                "https://nepsetrading.com/daily-price",
                lambda d: {"date": d.strftime("%Y-%m-%d")},
            ),
            ("https://nepsetrading.com", lambda d: {}),
        ]

    def _try_json(self, d: date) -> Optional[pd.DataFrame]:
        """Attempt to discover a REST/JSON endpoint."""
        json_urls = [
            "https://nepsetrading.com/api/v1/trading/daily-price",
            "https://nepsetrading.com/api/daily-price",
        ]
        date_str = d.strftime("%Y-%m-%d")
        for url in json_urls:
            try:
                r = self.session.get(
                    url,
                    params={"date": date_str, "page": 1, "per_page": 2000},  # type: ignore
                    headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                )
                if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                    data = r.json()
                    records = (
                        data
                        if isinstance(data, list)
                        else data.get("data") or data.get("records") or []
                    )
                    if records:
                        df = pd.DataFrame(records)
                        df.insert(0, "date", d.isoformat())
                        return df
            except Exception:
                pass
        return None

    def _try_html(self, d: date) -> Optional[pd.DataFrame]:
        for url, params_fn in self._candidates:
            try:
                r = self.session.get(
                    url,
                    params=params_fn(d),  # type: ignore
                    headers={"Referer": "https://nepsetrading.com/"},
                    timeout=REQUEST_TIMEOUT,
                )
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                table = soup.find("table")
                if not table:
                    continue
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
                tbody = table.find("tbody") or table
                rows = [
                    [td.get_text(strip=True) for td in tr.find_all("td")]
                    for tr in tbody.find_all("tr")
                    if tr.find("td")
                ]
                if not rows:
                    continue
                n = len(rows[0])
                col_names = headers[:n] if headers else [f"col{i}" for i in range(n)]
                df = pd.DataFrame(rows, columns=col_names)
                df.insert(0, "date", d.isoformat())
                self.log.info(f"  ↳ NepseTrading HTML parse succeeded at {url}")
                return df
            except Exception as exc:
                self.log.debug(f"  HTML candidate {url} failed: {exc}")
        return None

    def _scrape(self, d: date) -> Optional[pd.DataFrame]:
        df = self._try_json(d)
        if df is not None:
            return df
        return self._try_html(d)


# ─── Registry ─────────────────────────────────────────────────────────────────

SCRAPERS: dict[str, type[BaseScraper]] = {
    "sharesansar": SharesansarScraper,
    "merolagani": MerolaganiScraper,
    "nepsetrading": NepseTradingScraper,
}


# ─── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    today = date.today()
    p = argparse.ArgumentParser(
        description="NEPSE daily data scraper (ShareSansar / Merolagani / NepseTrading)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--source",
        default="all",
        choices=[*SCRAPERS, "all"],
        help="Which source to pull from (default: all)",
    )
    p.add_argument(
        "--start",
        type=date.fromisoformat,
        default=today - timedelta(days=14),
        metavar="YYYY-MM-DD",
        help="Start of date range (default: 14 days ago)",
    )
    p.add_argument(
        "--end",
        type=date.fromisoformat,
        default=today,
        metavar="YYYY-MM-DD",
        help="End of date range, inclusive (default: today)",
    )
    p.add_argument(
        "--today",
        action="store_true",
        help="Shorthand: scrape today only",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the CSV already exists",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    log = logging.getLogger("nepse")

    dates = (
        [date.today()] if args.today else trading_days_in_range(args.start, args.end)
    )
    if not dates:
        log.error("No trading days in the specified range.")
        sys.exit(1)

    sources = list(SCRAPERS) if args.source == "all" else [args.source]
    log.info(
        f"Scraping {len(sources)} source(s) × {len(dates)} date(s). "
        f"Output → {DATA_DIR.resolve()}"
    )

    all_results: dict[str, dict[date, int]] = {}
    for src in sources:
        scraper = SCRAPERS[src]()
        # --force: delete existing CSVs so _already_saved() returns False
        if args.force:
            for d in dates:
                p = scraper._csv_path(d)
                if p.exists():
                    p.unlink()
        all_results[src] = scraper.run(dates)

    # ── Summary ──
    print("\n" + "═" * 60)
    print("  SUMMARY")
    print("═" * 60)
    grand_total = 0
    for src, results in all_results.items():
        total_rows = sum(results.values())
        grand_total += total_rows
        print(f"\n  [{src}]  {total_rows:,} rows across {len(results)} date(s)")
        for d, n in sorted(results.items()):
            print(f"    {d}  →  {n:,} rows  →  data/{src}/{d}.csv")
    print(f"\n  TOTAL: {grand_total:,} rows saved")
    print("═" * 60)


if __name__ == "__main__":
    main()
