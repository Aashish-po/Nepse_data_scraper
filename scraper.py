#!/usr/bin/env python3
"""
NEPSE Daily Data Scraper
========================
Pulls daily floor sheet / today's share price from:
   • ShareSansar  (www.sharesansar.com/today-share-price)
   • Merolagani   (merolagani.com/Floorsheet.aspx)
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
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import chromedriver_autoinstaller as chromedriver  # type: ignore[import-untyped]


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


# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)


# ─── Utility ──────────────────────────────────────────────────────────────────


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
    Today's Share Price via Selenium WebDriver.
    Interacts with the webpage directly instead of using AJAX endpoint.
    """

    name = "sharesansar"
    PAGE_URL = "https://www.sharesansar.com/today-share-price"

    def _scrape(self, d: date) -> Optional[pd.DataFrame]:
        # Setup Chrome options
        options = Options()
        options.add_argument("--headless=new")

        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.5005.115 Safari/537.36"
        )

        chromedriver_path = chromedriver.install()
        driver = webdriver.Chrome(service=Service(chromedriver_path), options=options)
        driver.set_page_load_timeout(120)

        try:
            # Format date for the website (MM/DD/YYYY)
            date_str = d.strftime("%m/%d/%Y")

            # Navigate to the page
            self.log.info(f"Navigating to {self.PAGE_URL}")
            driver.get(self.PAGE_URL)

            # Wait for the date input field to be present
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='fromdate']"))
            )

            # Find the date input and enter the date
            date_input = driver.find_element(By.XPATH, "//input[@id='fromdate']")
            time.sleep(2)  # Small delay as in original code

            # Click on the date input to activate it
            date_input.click()

            # Clear any existing value and send the date
            date_input.clear()
            date_input.send_keys(date_str)

            # Click the search button
            try:
                search_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[@id='btn_todayshareprice_submit']")
                    )
                )
                search_btn.click()
            except Exception:
                # Fallback: try clicking the input itself as in original code
                date_input.click()
                time.sleep(2)  # Wait for search to initiate

            # Check if no data found
            if driver.find_elements(
                By.XPATH,
                "//*[contains(text(), 'Could not find floorsheet matching the search criteria')]",
            ):
                self.log.info("No data found for the given search.")
                return None

            # Scrape all pages
            df = pd.DataFrame()
            count = 0

            while True:
                count += 1
                self.log.info(f"Scraping page {count}")

                # Get the page table
                page_table_df = self._get_page_table(
                    driver,
                    "table table-bordered table-striped table-hover dataTable compact no-footer",
                )
                if not page_table_df.empty:
                    df = pd.concat([df, page_table_df], ignore_index=True)

                try:
                    # Try to find and click the next button
                    next_btn = driver.find_element(By.LINK_TEXT, "Next")
                    driver.execute_script("arguments[0].click();", next_btn)
                    time.sleep(1)  # Wait for page to load
                except NoSuchElementException:
                    # No more pages
                    break

            # Clean the dataframe if we have data
            if not df.empty:
                df = self._clean_df(df)
                # Insert date column at the beginning
                df.insert(0, "date", d.isoformat())

            return df if not df.empty else None

        except Exception as e:
            self.log.error(f"Error scraping ShareSansar: {e}")
            return None
        finally:
            # Always close the driver
            driver.quit()

    def _get_page_table(self, driver, table_class: str) -> pd.DataFrame:
        """Extract table data from the current page."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(driver.page_source, "lxml")
        table = soup.find("table", {"class": table_class})

        if not table:
            return pd.DataFrame()

        # Extract table data
        tab_data = [
            [
                cell.text.replace("\r", "").replace("\n", "")
                for cell in row.find_all(["th", "td"])
            ]
            for row in table.find_all("tr")
        ]
        df = pd.DataFrame(tab_data)
        return df

    def _clean_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean the dataframe by removing duplicates and setting proper headers."""
        if df.empty:
            return df

        new_df = df.drop_duplicates(keep="first")  # drop all duplicates
        new_header = new_df.iloc[0]  # grab the first row for the header
        new_df = new_df[1:]  # take the data less the header row
        new_df.columns = [str(x) for x in new_header.tolist()]  # type: ignore[assignment]

        # Drop the serial number column (might be "S.No" or "sno")
        cols_to_drop = [col for col in ["S.No", "sno"] if col in new_df.columns]
        if cols_to_drop:
            new_df.drop(cols_to_drop, axis=1, inplace=True)

        return new_df


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


# ─── Registry ─────────────────────────────────────────────────────────────────

SCRAPERS: dict[str, type[BaseScraper]] = {
    "sharesansar": SharesansarScraper,
    "merolagani": MerolaganiScraper,
}


# ─── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    today = date.today()
    p = argparse.ArgumentParser(
        description="NEPSE daily data scraper (ShareSansar / Merolagani)",
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
