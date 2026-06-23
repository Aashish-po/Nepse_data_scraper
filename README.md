# 📈 Nepse Daily Data Scraper

A robust Python scraper that extracts daily share price and floor‑sheet data from **ShareSansar** and **Merolagani**, saving clean CSV datasets for Nepal Stock Exchange (NEPSE) analysis.

---

## 🌟 Features

- **Dual‑source scraping** – Pulls OHLCV, VWAP, turnover from ShareSansar and transaction‑level floor sheets from Merolagani.  
- **Flexible date ranges** – Scrape a single day, a custom range, or the last N trading days.  
- **Automatic weekend & holiday skipping** – Skips weekends automatically; logs a warning and moves on when a source returns no data (e.g. public holidays).  
- **Force‑redownload mode** – Overwrite existing CSV files with fresh data.  
- **Organized output** – Data saved in `data/sharesansar/` and `data/merolagani/` with date‑based filenames.  
- **Respectful scraping** – Built‑in delays to avoid rate‑limits and 403 errors.  
- **Easy automation** – Ready‑to‑use cron / Task Scheduler snippets for daily runs.  
- **Well‑documented & extensible** – Clear code, inline comments, and contribution guide.

---

## 📦 Installation

### Prerequisites

- Python 3.9 or higher
- Google Chrome (required for ShareSansar; the matching ChromeDriver auto-installs)
- Git (optional, for cloning)
- Git Bash / PowerShell / Terminal

### Step‑by‑step

1. **Clone the repository** (or download the ZIP)

   ```bash
   git clone https://github.com/your-username/Nepse-data-scraper.git
   cd Nepse-data-scraper
   ```

2. **Create a virtual environment** (recommended)

   ```bash
   # Windows (PowerShell / CMD)
   python -m venv venv
   .\venv\Scripts\activate

   # macOS / Linux
   python -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

> 💡 **Tip:** If you encounter SSL issues on Windows, run `pip install --upgrade certifi certifi==2023.7.22`.

---

## 🚀 Usage

Run the scraper from the project root after activating the virtual environment.

| Command | Description |
|---------|-------------|
| `python scraper.py` | Scrape the last 14 trading days from **both** sources. |
| `python scraper.py --today` | Scrape **today’s** data only (all sources). |
| `python scraper.py --source sharesansar` | Scrape last 14 days **only** from ShareSansar. |
| `python scraper.py --source merolagani --today` | Scrape today **only** from Merolagani. |
| `python scraper.py --start 2025-01-01 --end 2025-03-31` | Custom date range (inclusive) for both sources. |
| `python scraper.py --source sharesansar --start 2025-06-01 --end 2025-06-20 --force` | Custom range, single source, **force** re‑download. |
| `python scraper.py --help` | Show full help menu. |

### Example output structure

```
data/
├── sharesansar/
│   ├── 2025-06-16.csv   ← Monday (trading day)
│   ├── 2025-06-17.csv
│   └── ...
└── merolagani/
    ├── 2025-06-16.csv
    └── ...
```

> 📅 **Note:** NEPSE trades **Sunday–Thursday**. The scraper automatically skips  Saturdays, Sunday, and logs a warning for public holidays when no data is returned.

---

## 🛠️ Technology Stack

| Category | Tools / Libraries |
|----------|-------------------|
| **Language** | Python 3.9+ |
| **HTTP Requests** | `requests` |
| **Browser Automation** | `selenium`, `chromedriver-autoinstaller` (ShareSansar) |
| **HTML Parsing** | `beautifulsoup4`, `lxml` |
| **Data Handling** | `pandas` |
| **Date Utilities** | `datetime` (built-in) |
| **CLI Parsing** | `argparse` (built‑in) |
| **Execution** | Standard CPython interpreter |

*All dependencies are listed in `requirements.txt`.*

---

## 🤝 Contributing

We welcome contributions! Please follow these steps:

1. **Fork** the repository on GitHub.
2. **Create a topic branch** – `git checkout -b feature/amazing-feature`.
3. **Make your changes** – keep code clean, add docstrings, and follow PEP 8.
4. **Test** – run `python scraper.py --help` to ensure the CLI still works.
5. **Commit** – `git commit -m "Add amazing feature"`.
6. **Push** – `git push origin feature/amazing-feature`.
7. **Open a Pull Request** – describe the problem and solution.

### Reporting Issues

- Use the **Issues** tab.
- Include:
  - OS and Python version.
  - Exact command that failed.
  - Full error traceback.
  - Any relevant screenshots or network logs.

### Code Style

- We follow **PEP 8**. Consider using `ruff` or `flake8` for linting.
- Docstrings follow the **NumPy** style.

---

## 📜 License

This project is licensed under the **MIT License** – see the [`LICENSE`](LICENSE) file for details.

---

## 🛠️ Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| ShareSansar returns no data / timeout | The page layout or element IDs changed, or ChromeDriver is missing. | Ensure Chrome is installed (ChromeDriver auto-installs). Open `https://www.sharesansar.com/today-share-price`, verify the `#fromdate` input and `#btn_todayshareprice_submit` button still exist, and update the XPaths in `SharesansarScraper._scrape()`. |
| Merolagani pagination stops early | ASP.NET pager markup changed. | Inspect the page source, locate the `<span id>` that holds the total page count, adjust `_parse_total_pages()`. |
| HTTP 403 / rate‑limit errors | Too many requests in a short time. | Increase `INTER_DATE_DELAY` at the top of `scraper.py`, run fewer days at a time, or run outside market hours (11:00–15:00 NPT). |
| Empty CSV files | No data returned (holiday or weekend). | The scraper logs a warning and skips the day; verify the date is a NEPSE trading day. |
| `ModuleNotFoundError` | Missing dependencies. | Re‑run `pip install -r requirements.txt` inside the activated venv. |

If your issue isn’t listed, please open an issue with the details above.

---

## 🚀 Get Involved

If you find this scraper useful:

- **Star** the repository 🌟
- **Share** it with fellow traders, analysts, or students.
- **Contribute** code, documentation, or feedback.

Let’s build better tools for the Nepalese capital market together!

---

*Made with ❤️ for the NEPSE community.*
