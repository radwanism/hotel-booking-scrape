#! /usr/bin/env python3
"""Scrape Booking.com hotels and guest reviews for one or more towns.

The script opens Booking, uses the site search to resolve each destination,
confirms it is on the search-results page, then scrolls that page to load
more hotels. Each hotel page (and its review list) is opened for reviews.
Each town is written to its own file. A later town still runs if an earlier
one fails.

Examples
--------
python scraping.py --country Egypt --town Cairo
python scraping.py -c Egypt -t Cairo -t Luxor -t "Sharm El Sheikh" -o csv
python scraping.py -c Egypt -t "Cairo, Luxor, Alexandria" --max-hotels 15
python scraping.py -d "Cairo, Egypt" -d "Marrakech, Morocco" -d "Paris, France"
python scraping.py --places places.txt -o excel --output-dir results
"""

import argparse
import datetime
import os
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from hotel_page import (  # noqa: E402
    dismiss_overlays,
    extract_score,
    log,
    scrape_hotel_reviews,
)
from file_writer import FileWriter  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PROPERTY_CARD = '[data-testid="property-card"]'
DATE_FORMAT = "%Y-%m-%d"


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def valid_date(given_date):
    try:
        return datetime.datetime.strptime(given_date, DATE_FORMAT)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{given_date} is not a valid date. Use YYYY-MM-DD."
        )


def default_start_date():
    return datetime.datetime.now().strftime(DATE_FORMAT)


def default_end_date():
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    return tomorrow.strftime(DATE_FORMAT)


def as_date_string(value):
    if isinstance(value, datetime.datetime):
        return value.strftime(DATE_FORMAT)
    return str(value)


# --------------------------------------------------------------------------
# Browser
# --------------------------------------------------------------------------

def require_playwright():
    if sync_playwright is None:
        raise SystemExit(
            "Playwright is required.\n"
            "  pip install -e .\n"
            "  python -m playwright install chromium"
        )


def launch_browser(playwright, headed):
    """Use the installed Chrome if there is one, otherwise Playwright's Chromium."""
    options = {
        "headless": not headed,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        return playwright.chromium.launch(channel="chrome", **options)
    except Exception:
        return playwright.chromium.launch(**options)


def new_context(browser):
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="en-GB",
        viewport={"width": 1400, "height": 900},
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return context


# --------------------------------------------------------------------------
# Finding the search-results page for a town
# --------------------------------------------------------------------------

def build_search_url(destination, dest_id, dest_type, checkin, checkout, rooms, persons=1):
    params = {
        "ss": destination,
        "checkin": checkin,
        "checkout": checkout,
        "group_adults": str(persons),
        "no_rooms": str(rooms),
        "group_children": "0",
        "lang": "en-gb",
        "sb": "1",
        "src": "searchresults",
    }
    if dest_id:
        params["dest_id"] = dest_id
        params["dest_type"] = dest_type or "city"
    return "https://www.booking.com/searchresults.en-gb.html?" + urlencode(params)


def _input_value(page, name):
    locator = page.locator(f'input[name="{name}"]')
    try:
        if locator.count():
            return (locator.first.input_value() or "").strip()
    except Exception:
        return ""
    return ""


def _query_value(url, key):
    values = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    return (values.get(key) or "").strip()


def _loose(value):
    """Lowercase and reduce to letters, digits and single spaces, for comparing names."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _same_place(place, town):
    """True when place names the town, e.g. 'Cairo' or 'Cairo Governorate' for 'Cairo'."""
    place_key = _loose(place)
    town_key = _loose(town)
    return place_key == town_key or place_key.startswith(town_key + " ")


def click_best_suggestion(page, town, country):
    """Click the city suggestion for this town. Skip hotels and landmarks."""
    options = page.locator('[data-testid="autocomplete-result"]')
    if options.count() == 0:
        options = page.locator('[role="listbox"] [role="option"]')
    count = min(options.count(), 8)
    if count == 0:
        return False

    country_key = _loose(country)
    best_index = None
    best_score = 0
    for index in range(count):
        try:
            info = options.nth(index).evaluate(
                """(el) => {
                    const icon = el.querySelector('[data-testid^="autocomplete-icon"]');
                    return {
                        text: (el.innerText || '').trim(),
                        icon: icon ? icon.getAttribute('data-testid') : ''
                    };
                }"""
            )
        except Exception:
            continue

        lines = [line.strip() for line in info["text"].splitlines() if line.strip()]
        primary = lines[0] if lines else info["text"]
        if not _same_place(primary, town):
            continue

        icon = info["icon"] or ""
        score = 10
        if country_key and country_key in _loose(info["text"]):
            score += 3
        if icon in (
            "autocomplete-icon-default",
            "autocomplete-icon-city",
            "autocomplete-icon-region",
        ):
            score += 4
        if any(kind in icon for kind in ("hotel", "landmark", "airport", "attraction")):
            score -= 8
        if score > best_score:
            best_score = score
            best_index = index

    if best_index is None:
        return False
    try:
        options.nth(best_index).click(timeout=5000)
        return True
    except Exception:
        return False


def _try_disambiguation_link(page, town, country):
    """When Booking asks "which Cairo?", click the link that names town and country."""
    town_key = town.lower()
    country_key = country.lower()
    links = page.locator('a[href*="searchresults"]')
    for index in range(min(links.count(), 15)):
        try:
            label = links.nth(index).inner_text().lower()
        except Exception:
            continue
        if town_key in label and country_key in label:
            try:
                links.nth(index).click(timeout=5000)
            except Exception:
                continue
            page.wait_for_timeout(1500)
            return


def identify_search_page(page, town, country, headed):
    """Wait until Booking shows a search-results page for this destination."""
    # A visible browser gets longer, so there is time to solve a robot check.
    wait_seconds = 90 if headed else 30
    deadline = datetime.datetime.now() + datetime.timedelta(seconds=wait_seconds)
    tried_disambiguation = False

    while datetime.datetime.now() < deadline:
        dismiss_overlays(page)
        if page.locator(PROPERTY_CARD).count() > 0:
            log(f"Identified search page: {page.url}")
            return
        if not tried_disambiguation:
            tried_disambiguation = True
            _try_disambiguation_link(page, town, country)
        page.wait_for_timeout(1000)

    raise RuntimeError(
        f"Could not identify a Booking search page for {town}, {country}. "
        f"The browser stopped on {page.url}. If a robot check is showing, run again "
        "without --headless and complete it in the browser window."
    )


def _search_from_home_page(page, town, country, query):
    """Type the town into Booking's search box and pick the best suggestion.

    Returns (destination, dest_id, dest_type). If the box can't be used, the
    plain query is returned and the search URL is built from that.
    """
    destination = query
    dest_id = ""
    dest_type = "city"
    box = page.locator('input[name="ss"]')
    try:
        box.first.wait_for(state="visible", timeout=15000)
        box.first.click()
        box.first.fill("")
        box.first.press_sequentially(query, delay=30)
        page.wait_for_timeout(1200)
        if click_best_suggestion(page, town, country):
            page.wait_for_timeout(1200)
            if "searchresults" in page.url:
                destination = _query_value(page.url, "ss") or _input_value(page, "ss") or query
                dest_id = _query_value(page.url, "dest_id") or _input_value(page, "dest_id")
                dest_type = (
                    _query_value(page.url, "dest_type")
                    or _input_value(page, "dest_type")
                    or "city"
                )
            else:
                destination = _input_value(page, "ss") or query
                dest_id = _input_value(page, "dest_id")
                dest_type = _input_value(page, "dest_type") or "city"
    except Exception as error:
        log(f"Search box was not usable ({error}). Opening the search URL directly.")
    return destination, dest_id, dest_type


def resolve_search_page(page, town, country, checkin, checkout, rooms, headed, persons=1):
    """Use Booking's own search box so the site picks the destination page."""
    query = f"{town}, {country}"
    page.goto("https://www.booking.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(800)
    dismiss_overlays(page)

    destination, dest_id, dest_type = _search_from_home_page(page, town, country, query)

    # Booking sometimes picks a different town. Fall back to a plain text search.
    if not _same_place(destination.split(",")[0], town):
        destination = query
        dest_id = ""
        dest_type = "city"

    search_url = build_search_url(
        destination, dest_id, dest_type, checkin, checkout, rooms, persons
    )
    log(f"Opening search page for {destination}")
    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    dismiss_overlays(page)
    identify_search_page(page, town, country, headed)

    resolved = _query_value(page.url, "ss") or _input_value(page, "ss") or destination
    if not _same_place(resolved.split(",")[0], town):
        resolved = destination
    if country.lower() not in resolved.lower():
        resolved = f"{resolved.strip(' ,')}, {country}"
    return resolved


# --------------------------------------------------------------------------
# Hotels on the search-results page
# --------------------------------------------------------------------------

def click_load_more(page):
    candidates = [
        page.get_by_role("button", name=re.compile(r"load more", re.IGNORECASE)),
        page.locator('button:has-text("Load more")'),
        page.locator('[data-testid="load-more"]'),
    ]
    for locator in candidates:
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=4000)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def scroll_for_more_hotels(page, max_hotels):
    """Scroll the search results until enough hotels are loaded or the list stops growing."""
    last_count = 0
    idle_rounds = 0
    while idle_rounds < 4:
        count = page.locator(PROPERTY_CARD).count()
        log(f"Search page: {count} hotels loaded")
        if count >= max_hotels:
            break
        page.evaluate(
            """() => {
                const cards = document.querySelectorAll('[data-testid="property-card"]');
                const last = cards[cards.length - 1];
                if (last) last.scrollIntoView({block: 'end'});
                window.scrollBy(0, Math.max(window.innerHeight, 800));
            }"""
        )
        click_load_more(page)
        page.wait_for_timeout(1400)
        new_count = page.locator(PROPERTY_CARD).count()
        if new_count <= last_count:
            idle_rounds += 1
        else:
            idle_rounds = 0
            last_count = new_count
    return page.locator(PROPERTY_CARD).count()


def extract_hotels(page, max_hotels):
    """Read name, address, price, score and link from each hotel card."""
    raw_cards = page.evaluate(
        """() => {
            const text = (node, selector) => {
                const el = node.querySelector(selector);
                return el ? el.innerText.replace(/\\s+/g, ' ').trim() : '';
            };
            return Array.from(document.querySelectorAll('[data-testid="property-card"]')).map((card) => {
                const link = card.querySelector('a[data-testid="title-link"]') || card.querySelector('a[href*="/hotel/"]');
                return {
                    name: text(card, '[data-testid="title"]'),
                    address: text(card, '[data-testid="address"]')
                        || text(card, '[data-testid="address-link"]'),
                    price: text(card, '[data-testid="price-and-discounted-price"]')
                        || text(card, '[data-testid="price-for-x-nights"]'),
                    score: text(card, '[data-testid="review-score"]'),
                    url: link ? link.href : ''
                };
            });
        }"""
    )
    hotels = []
    seen = set()
    for card in raw_cards:
        url = card.get("url") or ""
        # The same hotel can show up twice with different tracking parameters.
        key = urlparse(url).path or card.get("name") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        hotels.append(
            {
                "name": card.get("name") or "",
                "address": card.get("address") or "",
                "price": card.get("price") or "",
                "rating": extract_score(card.get("score") or ""),
                "url": url,
            }
        )
        if len(hotels) >= max_hotels:
            break
    return hotels


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------

def _scrape_one_destination(
    page, town, country, checkin, checkout, rooms, persons, max_hotels, max_reviews, headed
):
    """Scrape one town. Returns (rows, interrupted)."""
    rows = []
    try:
        destination = resolve_search_page(
            page, town, country, checkin, checkout, rooms, headed, persons
        )
        scroll_for_more_hotels(page, max_hotels)
        hotels = extract_hotels(page, max_hotels)
        log(f"Scraping reviews for {len(hotels)} hotels in {destination}")
        for index, hotel in enumerate(hotels, start=1):
            log(f"[{index}/{len(hotels)}] {hotel.get('name') or 'hotel'}")
            rows.extend(scrape_hotel_reviews(page, hotel, destination, max_reviews))
            page.wait_for_timeout(700)
    except KeyboardInterrupt:
        log("Stopped early. Saving what has been scraped so far.")
        return rows, True
    return rows, False


def scrape_destinations(
    destinations,
    rooms=1,
    persons=1,
    start_date=None,
    end_date=None,
    max_hotels=20,
    max_reviews=8,
    headed=True,
    out_format="json",
    output_dir=None,
):
    """Scrape each (country, town) pair and write a file as soon as it finishes."""
    require_playwright()
    checkin = as_date_string(start_date or default_start_date())
    checkout = as_date_string(end_date or default_end_date())
    saved = []
    total = len(destinations)
    log(f"Scraping {total} destination{'' if total == 1 else 's'}.")

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headed)
        context = new_context(browser)
        page = context.new_page()
        try:
            for index, (country, town) in enumerate(destinations, start=1):
                log(f"=== [{index}/{total}] {town}, {country} ===")
                try:
                    rows, interrupted = _scrape_one_destination(
                        page,
                        town,
                        country,
                        checkin,
                        checkout,
                        rooms,
                        persons,
                        max_hotels,
                        max_reviews,
                        headed,
                    )
                except Exception as error:
                    log(f"Could not scrape {town}, {country}: {error}")
                    continue

                if rows:
                    saved.append(save_data(rows, out_format, country, town, output_dir))
                else:
                    log(f"No hotels were scraped for {town}, {country}.")
                if interrupted:
                    log("Stopped early. Files already written are kept.")
                    break
        finally:
            context.close()
            browser.close()
    return saved


def save_data(data, out_format, country, town, output_dir=None):
    """Write one town's rows. Files go next to this script unless output_dir is given."""
    output_dir = output_dir or SCRIPT_DIR
    os.makedirs(output_dir, exist_ok=True)
    writer = FileWriter(
        data,
        out_format=out_format,
        country=country,
        town=town,
        output_dir=output_dir,
    )
    path = writer.output_file()
    log(f"Saved {len(data)} rows.")
    log(f"You can find them in {path}")
    return path


def get_data(
    rooms=1,
    persons=1,
    country="Egypt",
    town="Cairo",
    out_format="json",
    start_date=None,
    end_date=None,
    max_hotels=20,
    max_reviews=8,
    headed=True,
    destinations=None,
    output_dir=None,
):
    """Scrape and save. Returns the list of written file paths, or None."""
    if not destinations:
        destinations = [(country, town)]
    saved = scrape_destinations(
        destinations,
        rooms=rooms,
        persons=persons,
        start_date=start_date,
        end_date=end_date,
        max_hotels=max_hotels,
        max_reviews=max_reviews,
        headed=headed,
        out_format=out_format,
        output_dir=output_dir,
    )
    if not saved:
        log("No hotels were scraped, so no file was written.")
        return None
    return saved


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _split_towns(value):
    towns = [part.strip() for part in value.split(",")]
    return [town for town in towns if town]


def _parse_destination(value):
    """Parse 'Town, Country'. The country is the text after the last comma."""
    error = f'Destination must look like "Town, Country". Got: {value}'
    if "," not in value:
        raise argparse.ArgumentTypeError(error)
    town, country = [part.strip() for part in value.rsplit(",", 1)]
    if not town or not country:
        raise argparse.ArgumentTypeError(error)
    return country, town


def read_places_file(path):
    """Read destinations from a text file, one 'Town, Country' per line."""
    if not os.path.isfile(path):
        raise SystemExit(f"Places file not found: {path}")
    found = []
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                found.append(_parse_destination(line))
            except argparse.ArgumentTypeError as error:
                raise SystemExit(f"{path}:{line_number}: {error}")
    if not found:
        raise SystemExit(f"Places file has no destinations: {path}")
    return found


def unique_destinations(pairs):
    """Trim each (country, town), drop empty ones and drop repeats."""
    seen = set()
    unique = []
    for country, town in pairs:
        country = country.strip()
        town = town.strip()
        key = (_loose(country), _loose(town))
        if key in seen:
            log(f"Skipping duplicate destination {town}, {country}.")
            continue
        if not country or not town:
            continue
        seen.add(key)
        unique.append((country, town))
    return unique


class _PlaceAction(argparse.Action):
    """Collect -c/-t pairs and -d 'Town, Country' values in the order given."""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, "destinations", None) is None:
            namespace.destinations = []

        if option_string in ("-c", "--country"):
            self._set_country(parser, namespace, values)
        elif option_string in ("-t", "--town"):
            self._add_towns(parser, namespace, values)
        else:
            try:
                namespace.destinations.append(_parse_destination(values))
            except argparse.ArgumentTypeError as error:
                parser.error(str(error))

    @staticmethod
    def _set_country(parser, namespace, value):
        country = value.strip()
        if not country:
            parser.error("Country cannot be empty.")
        pending = getattr(namespace, "_pending_country", None)
        if pending and not getattr(namespace, "_country_has_town", False):
            parser.error(f"No town was given for {pending}. Pass -t after -c.")
        namespace._pending_country = country
        namespace._country_has_town = False

    @staticmethod
    def _add_towns(parser, namespace, value):
        country = getattr(namespace, "_pending_country", None)
        if not country:
            parser.error("Pass -c/--country before -t/--town.")
        towns = _split_towns(value)
        if not towns:
            parser.error("Town cannot be empty.")
        for town in towns:
            namespace.destinations.append((country, town))
        namespace._country_has_town = True


def destinations_from_args(args):
    found = list(args.destinations or [])
    pending = getattr(args, "_pending_country", None)
    if pending and not getattr(args, "_country_has_town", False):
        raise SystemExit(f"No town was given for {pending}. Pass -t after -c.")
    if args.places:
        found.extend(read_places_file(args.places))
    found = unique_destinations(found)
    if not found:
        raise SystemExit(
            'Give at least one destination: -c Egypt -t Cairo, '
            '-d "Cairo, Egypt", or --places places.txt.'
        )
    return found


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Scrape Booking.com hotels and reviews for one or more towns. "
            "Repeat -c and -t to change country, or pass -d for each town."
        )
    )
    parser.set_defaults(
        destinations=[],
        _pending_country=None,
        _country_has_town=False,
    )
    parser.add_argument(
        "-c",
        "--country",
        action=_PlaceAction,
        default=argparse.SUPPRESS,
        help=(
            "Country for the towns that follow. Repeat to switch country, "
            "for example -c Egypt -t Cairo -t Luxor -c Morocco -t Marrakech."
        ),
    )
    parser.add_argument(
        "-t",
        "--town",
        action=_PlaceAction,
        default=argparse.SUPPRESS,
        help=(
            "Town or city in the current country. Repeat the flag, or separate "
            'towns with commas: -t "Cairo, Luxor".'
        ),
    )
    parser.add_argument(
        "-d",
        "--destination",
        action=_PlaceAction,
        default=argparse.SUPPRESS,
        metavar="TOWN,COUNTRY",
        help=(
            'One destination as "Town, Country". Repeat for each place, '
            'including places in different countries.'
        ),
    )
    parser.add_argument(
        "--places",
        help=(
            "Text file with one destination per line, written as "
            '"Town, Country". Blank lines and lines starting with # are ignored.'
        ),
    )
    parser.add_argument(
        "-r",
        "--rooms",
        help="Number of rooms in the search.",
        default=1,
        type=int,
    )
    parser.add_argument(
        "-p",
        "--persons",
        help="Number of persons in the search. Defaults to 1.",
        default=1,
        type=int,
    )
    parser.add_argument(
        "-o",
        "--out_format",
        help="Output file format.",
        default="json",
        choices=["json", "excel", "csv"],
    )
    parser.add_argument(
        "--output-dir",
        help="Folder for the output files. Created if missing. Defaults to the script's folder.",
    )
    parser.add_argument(
        "-s",
        "--startdate",
        help="Check-in date (YYYY-MM-DD). Defaults to today.",
        default=default_start_date(),
        type=valid_date,
    )
    parser.add_argument(
        "-e",
        "--enddate",
        help="Check-out date (YYYY-MM-DD). Defaults to tomorrow.",
        default=default_end_date(),
        type=valid_date,
    )
    parser.add_argument(
        "--max-hotels",
        help="Stop scrolling the search page after this many hotels.",
        default=20,
        type=int,
    )
    parser.add_argument(
        "--max-reviews",
        help="Maximum guest reviews to keep for each hotel.",
        default=8,
        type=int,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Hide the browser window. A visible window is more reliable for prices and reviews.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    for flag, value in (
        ("--max-hotels", args.max_hotels),
        ("--max-reviews", args.max_reviews),
        ("--rooms", args.rooms),
        ("--persons", args.persons),
    ):
        if value < 1:
            raise SystemExit(f"{flag} must be at least 1.")

    get_data(
        rooms=args.rooms,
        persons=args.persons,
        out_format=args.out_format,
        start_date=args.startdate,
        end_date=args.enddate,
        max_hotels=args.max_hotels,
        max_reviews=args.max_reviews,
        headed=not args.headless,
        destinations=destinations_from_args(args),
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
