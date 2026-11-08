# Booking.com hotel review scraper

Collects hotels and their guest reviews from Booking.com for one or more
towns and saves them as JSON, CSV or Excel. You get one row per review.

It drives a real Chrome window with Playwright: it searches for the town the
way a person would, scrolls the results to load more hotels, then opens each
hotel and reads its reviews.

## Setup

You need Python 3.8 or newer. Dependencies are listed in `pyproject.toml`.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

If Google Chrome is installed, the scraper uses it. Otherwise it uses the
Chromium that Playwright downloaded.

Installing also adds a `booking-scraper` command, which does the same thing
as `python scraping.py`.

## Usage

```bash
# One town
python scraping.py -c Egypt -t Cairo

# Several towns in one country, saved as CSV
python scraping.py -c Egypt -t "Cairo, Luxor, Alexandria" -o csv

# Towns in different countries
python scraping.py -d "Cairo, Egypt" -d "Marrakech, Morocco"

# Read destinations from a file (see places.example.txt)
python scraping.py --places places.txt -o excel

# Save somewhere else
booking-scraper -c Egypt -t Aswan --output-dir results
```

Each town is written to its own file, for example
`hotels-in-Sharm-El-Sheikh-Egypt.csv`. Files go next to `scraping.py` unless
you pass `--output-dir`. If one town fails, the rest still run.
Press `Ctrl+C` at any time and whatever has been scraped for the current town
is saved.

### Options

| Option | Default | What it does |
| --- | --- | --- |
| `-c`, `--country` | | Country for the `-t` towns that follow it. Repeat to switch country. |
| `-t`, `--town` | | Town in the current country. Repeat it, or separate towns with commas. |
| `-d`, `--destination` | | One `"Town, Country"`. Repeat for each place. |
| `--places` | | Text file with one `Town, Country` per line. `#` starts a comment. |
| `-o`, `--out_format` | `json` | `json`, `csv` or `excel`. |
| `--output-dir` | script folder | Where to write the files. Created if it doesn't exist. |
| `-s`, `--startdate` | today | Check-in date, `YYYY-MM-DD`. |
| `-e`, `--enddate` | tomorrow | Check-out date, `YYYY-MM-DD`. |
| `-r`, `--rooms` | `1` | Rooms in the search. |
| `-p`, `--persons` | `1` | Adults in the search. |
| `--max-hotels` | `20` | Hotels to take from each town's search results. |
| `--max-reviews` | `8` | Reviews to keep for each hotel. |
| `--headless` | off | Hide the browser window. |

The dates and room/guest numbers change the prices Booking shows. They don't
change which reviews you get.

## Output

| Column | Example |
| --- | --- |
| `hotel_name` | TUT Hotel-Downtown |
| `destination` | Cairo, Egypt |
| `address` | 31 Talaat Harb, 4280160 Cairo, Egypt |
| `price_text` | EGP 31,112 |
| `numeric_rating` | 9.5 |
| `review_title` | Exceptional |
| `review_positive` | What the guest liked |
| `review_negative` | What the guest didn't like |
| `review_date` | 2026-10-21 |
| `review_country` | Germany |
| `review_rate_scale` | 10/10 |

The hotel columns repeat on every review of that hotel. A hotel with no
readable reviews still gets one row with the review columns empty.

CSV files are written as UTF-8 with a BOM, so Excel shows Arabic and accented
text correctly.

## How reviews are collected

Booking only shows the first few reviews on the hotel page, so the scraper
tries these sources in order and stops once it has enough:

1. **Review API.** When the hotel page loads its review list, the scraper
   records that request and sends it again from inside the page with a
   different offset, 25 reviews at a time.
2. **Hotel page.** Reviews embedded in the page as JSON, or the rendered
   review cards if there is no JSON.
3. **"Read all reviews" panel.** Opens the panel and clicks through its pages.

Duplicates are removed across all three.

## Code layout

| File | Contents |
| --- | --- |
| `scraping.py` | Command line, browser setup, search page and hotel list. |
| `hotel_page.py` | Everything on a single hotel page: header, reviews, parsing. |
| `file_writer.py` | Writes rows to JSON, CSV or Excel. |
| `tests/` | Offline tests. They use sample HTML and JSON and never open a browser. |

You can also call it from Python:

```python
from scraping import get_data

paths = get_data(
    destinations=[("Egypt", "Aswan"), ("Egypt", "Luxor")],  # (country, town)
    out_format="csv",
    max_hotels=5,
    output_dir="results",
)
```

## Running the tests

```bash
python -m unittest discover tests
```

The tests need no network access and nothing beyond the normal dependencies.

## Troubleshooting

- **"Could not identify a Booking search page"** usually means Booking showed
  a robot check. Run without `--headless` and solve it in the window. The
  scraper waits up to 90 seconds for you.
- **No prices or fewer reviews in headless mode.** Booking serves headless
  browsers a reduced page more often. A visible window is more reliable.
- **Results suddenly empty.** Booking changes its markup from time to time.
  The selectors live near the top of each function in `hotel_page.py` and
  `scraping.py`, so that is the place to look.

## Please use it responsibly

Check Booking.com's terms of service before you scrape, keep request volumes
low, and don't republish guest reviews or other personal data. This project is
for learning and research. You are responsible for how you use it.

The review step relies on an internal Booking.com endpoint (the `ReviewList`
GraphQL query the hotel page uses itself). It is not a public or documented
API, and Booking can change or remove it at any time. If that happens, the
scraper falls back to reading reviews from the page. That still works, but it
is slower and usually returns fewer reviews.
