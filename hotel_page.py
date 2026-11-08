"""Parse Booking.com hotel pages and review lists.

Reviews are collected in this order, falling back to the next source only
when the previous one did not give enough:

1. Booking's own ReviewList GraphQL endpoint, replayed from inside the page.
2. Reviews embedded in the hotel page (JSON in <script> tags, then HTML).
3. The paginated "Read all reviews" panel.
"""

import datetime
import json
import re

from bs4 import BeautifulSoup


# ISO 3166-1 alpha-2 codes Booking puts on each review. The visible country
# name from the flag is preferred when the page includes it.
COUNTRY_NAMES = {
    "ad": "Andorra", "ae": "United Arab Emirates", "af": "Afghanistan",
    "ag": "Antigua and Barbuda", "al": "Albania", "am": "Armenia",
    "ao": "Angola", "ar": "Argentina", "at": "Austria", "au": "Australia",
    "az": "Azerbaijan", "ba": "Bosnia and Herzegovina", "bb": "Barbados",
    "bd": "Bangladesh", "be": "Belgium", "bf": "Burkina Faso", "bg": "Bulgaria",
    "bh": "Bahrain", "bi": "Burundi", "bj": "Benin", "bn": "Brunei",
    "bo": "Bolivia", "br": "Brazil", "bs": "Bahamas", "bt": "Bhutan",
    "bw": "Botswana", "by": "Belarus", "bz": "Belize", "ca": "Canada",
    "cd": "DR Congo", "cf": "Central African Republic", "cg": "Congo",
    "ch": "Switzerland", "ci": "Côte d'Ivoire", "cl": "Chile", "cm": "Cameroon",
    "cn": "China", "co": "Colombia", "cr": "Costa Rica", "cu": "Cuba",
    "cv": "Cabo Verde", "cy": "Cyprus", "cz": "Czechia", "de": "Germany",
    "dj": "Djibouti", "dk": "Denmark", "dm": "Dominica", "do": "Dominican Republic",
    "dz": "Algeria", "ec": "Ecuador", "ee": "Estonia", "eg": "Egypt",
    "er": "Eritrea", "es": "Spain", "et": "Ethiopia", "fi": "Finland",
    "fj": "Fiji", "fr": "France", "ga": "Gabon", "gb": "United Kingdom",
    "ge": "Georgia", "gh": "Ghana", "gm": "Gambia", "gn": "Guinea",
    "gq": "Equatorial Guinea", "gr": "Greece", "gt": "Guatemala", "gw": "Guinea-Bissau",
    "gy": "Guyana", "hk": "Hong Kong", "hn": "Honduras", "hr": "Croatia",
    "ht": "Haiti", "hu": "Hungary", "id": "Indonesia", "ie": "Ireland",
    "il": "Israel", "in": "India", "iq": "Iraq", "ir": "Iran", "is": "Iceland",
    "it": "Italy", "jm": "Jamaica", "jo": "Jordan", "jp": "Japan", "ke": "Kenya",
    "kg": "Kyrgyzstan", "kh": "Cambodia", "km": "Comoros", "kn": "Saint Kitts and Nevis",
    "kr": "South Korea", "kw": "Kuwait", "kz": "Kazakhstan", "la": "Laos",
    "lb": "Lebanon", "lc": "Saint Lucia", "li": "Liechtenstein", "lk": "Sri Lanka",
    "lr": "Liberia", "ls": "Lesotho", "lt": "Lithuania", "lu": "Luxembourg",
    "lv": "Latvia", "ly": "Libya", "ma": "Morocco", "mc": "Monaco",
    "md": "Moldova", "me": "Montenegro", "mg": "Madagascar", "mk": "North Macedonia",
    "ml": "Mali", "mm": "Myanmar", "mn": "Mongolia", "mr": "Mauritania",
    "mt": "Malta", "mu": "Mauritius", "mv": "Maldives", "mw": "Malawi",
    "mx": "Mexico", "my": "Malaysia", "mz": "Mozambique", "na": "Namibia",
    "ne": "Niger", "ng": "Nigeria", "ni": "Nicaragua", "nl": "Netherlands",
    "no": "Norway", "np": "Nepal", "nz": "New Zealand", "om": "Oman",
    "pa": "Panama", "pe": "Peru", "pg": "Papua New Guinea", "ph": "Philippines",
    "pk": "Pakistan", "pl": "Poland", "pr": "Puerto Rico", "ps": "Palestine",
    "pt": "Portugal", "py": "Paraguay", "qa": "Qatar", "ro": "Romania",
    "rs": "Serbia", "ru": "Russia", "rw": "Rwanda", "sa": "Saudi Arabia",
    "sd": "Sudan", "se": "Sweden", "sg": "Singapore", "si": "Slovenia",
    "sk": "Slovakia", "sl": "Sierra Leone", "sn": "Senegal", "so": "Somalia",
    "sr": "Suriname", "sv": "El Salvador", "sy": "Syria", "sz": "Eswatini",
    "td": "Chad", "tg": "Togo", "th": "Thailand", "tn": "Tunisia",
    "tr": "Turkey", "tt": "Trinidad and Tobago", "tw": "Taiwan", "tz": "Tanzania",
    "ua": "Ukraine", "ug": "Uganda", "us": "United States", "uy": "Uruguay",
    "uz": "Uzbekistan", "ve": "Venezuela", "vn": "Vietnam", "ye": "Yemen",
    "za": "South Africa", "zm": "Zambia", "zw": "Zimbabwe",
}

# Booking shows the traveller type in the same spot as the country on some
# layouts. Anything in this set is never accepted as a country.
TRAVELER_TYPES = {
    "couple",
    "couples",
    "family",
    "families",
    "solo traveler",
    "solo traveller",
    "group",
    "group of friends",
    "friends",
    "business traveler",
    "business travellers",
    "business travelers",
    "family with young children",
    "family with older children",
    "with friends",
    "traveling with friends",
    "travelling with friends",
    "people with disabilities",
    "reviewer",
}

REVIEW_CARD = '[data-testid="review-card"]'

# Newest layout first, older layouts after.
REVIEW_CARD_SELECTORS = [
    REVIEW_CARD,
    "li.review_list_new_item_block",
    ".review_list_new_item_block",
    ".c-review-block",
]

# How many reviews the ReviewList endpoint returns per request.
REVIEW_PAGE_SIZE = 25


def log(message):
    """Print right away, even on consoles that can't show every character."""
    text = str(message)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def clean(text):
    """Collapse whitespace and trim. None and empty values become ''."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def make_review(title="", positive="", negative="", date="", country="", scale=""):
    """Build a review record. Every source produces this same shape."""
    return {
        "title": title,
        "positive": positive,
        "negative": negative,
        "date": date,
        "country": country,
        "scale": scale,
    }


def dismiss_overlays(page):
    """Close the cookie banner and the sign-in prompt if they are showing."""
    selectors = [
        "#onetrust-accept-btn-handler",
        'button[id*="onetrust-accept"]',
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        '[aria-label="Dismiss sign-in info."]',
        '[aria-label="Dismiss sign in information."]',
    ]
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=2000)
                page.wait_for_timeout(400)
        except Exception:
            continue


# --------------------------------------------------------------------------
# Scores and small value helpers
# --------------------------------------------------------------------------

def extract_score(text):
    """Return the guest-score number. Booking scores are on a 1-10 scale."""
    if not text:
        return ""
    cleaned = str(text).replace(",", "")

    explicit = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*(\d+)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if explicit:
        return explicit.group(1)

    scored = re.search(r"scored\s+(\d+(?:\.\d+)?)", cleaned, flags=re.IGNORECASE)
    if scored:
        return scored.group(1)

    for match in re.finditer(r"\d+(?:\.\d+)?", cleaned):
        if 1 <= float(match.group()) <= 10:
            return match.group()
    return ""


def review_rate_scale(text):
    """Turn score text into 'score/scale', e.g. 'Scored 8.0' -> '8.0/10'."""
    score = extract_score(text)
    if not score:
        return ""
    denom_match = re.search(r"(?:/|out of)\s*(\d+)", str(text), flags=re.IGNORECASE)
    denominator = denom_match.group(1) if denom_match else "10"
    return f"{score}/{denominator}"


def country_name(code):
    """Map a two-letter country code to a name. Longer values pass through."""
    cleaned = clean(code).lower()
    if not cleaned:
        return ""
    if len(cleaned) != 2:
        return clean(code)
    return COUNTRY_NAMES.get(cleaned, cleaned.upper())


def _format_score(value):
    """Format a numeric score as '8' or '8.5'. Out-of-range values give ''."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not 1 <= number <= 10:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _score_out_of_ten(value):
    shown_score = _format_score(value)
    return f"{shown_score}/10" if shown_score else ""


def _timestamp_to_date(value):
    """Convert a Unix timestamp (seconds or milliseconds) to YYYY-MM-DD."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number > 10**12:
        number = number / 1000.0
    if number < 10**9:
        return ""
    moment = datetime.datetime.fromtimestamp(number, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%d")


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _is_traveler_type(text):
    return text.lower() in TRAVELER_TYPES


# --------------------------------------------------------------------------
# Reviews from HTML
# --------------------------------------------------------------------------

def _strip_sentiment_prefix(text):
    return re.sub(
        r"^(liked|disliked|positive|negative)\s*[:\-]?\s*",
        "",
        clean(text),
        flags=re.IGNORECASE,
    )


def _selector_text(node, selectors):
    """Return the text of the first selector that matches and is not empty."""
    if node is None:
        return ""
    for selector in selectors:
        found = node.select_one(selector)
        if not found:
            continue
        text = clean(found.get_text(" ", strip=True))
        if text:
            return text
    return ""


def _review_country(card):
    country = _selector_text(
        card,
        [
            '[data-testid="review-country"]',
            '[data-testid="reviewer-country"]',
            '[data-testid="country"]',
        ],
    )
    if country and not _is_traveler_type(country):
        return country

    flag = card.select_one(
        'img[src*="flags"], img[class*="flag"], [data-testid="reviewer-country-flag"]'
    )
    if flag and clean(flag.get("alt")):
        return clean(flag.get("alt"))

    # The avatar block holds the name on the first line and the country
    # somewhere below it.
    avatar = card.select_one(
        '[data-testid="review-avatar"], .bui-avatar-block, [data-testid="reviewer"]'
    )
    if avatar:
        lines = [
            clean(line)
            for line in avatar.get_text("\n", strip=True).split("\n")
            if clean(line)
        ]
        if len(lines) >= 2:
            for line in reversed(lines):
                if not _is_traveler_type(line) and len(line) <= 40:
                    return line

    subtitle = _selector_text(
        card,
        [".bui-avatar-block__subtitle", '[data-testid="review-subtitle"]'],
    )
    if subtitle and not _is_traveler_type(subtitle) and len(subtitle) <= 40:
        return subtitle
    return ""


def _labeled_review_rows(card):
    """Return [(kind, text)] for old-layout rows. kind is 'positive', 'negative' or ''."""
    labeled = []
    for row in card.select(".c-review__row"):
        body = row.select_one(".c-review__body")
        if not body:
            continue
        text = _strip_sentiment_prefix(body.get_text(" ", strip=True))
        if not text:
            continue
        prefix = row.select_one(".c-review__prefix")
        classes = " ".join(row.get("class", []))
        if prefix:
            classes += " " + " ".join(prefix.get("class", []))
        kind = ""
        if "green" in classes or "positive" in classes:
            kind = "positive"
        elif "red" in classes or "negative" in classes or "minus" in classes:
            kind = "negative"
        labeled.append((kind, text))
    return labeled


def _positive_negative(card):
    """Return (liked, disliked) text for one review card."""
    positive = _strip_sentiment_prefix(
        _selector_text(
            card,
            [
                '[data-testid="review-positive-text"]',
                '[data-testid="positive-text"]',
                ".review_pos",
                ".c-review__prefix--color-green + .c-review__body",
            ],
        )
    )
    negative = _strip_sentiment_prefix(
        _selector_text(
            card,
            [
                '[data-testid="review-negative-text"]',
                '[data-testid="negative-text"]',
                ".review_neg",
            ],
        )
    )
    if positive and negative:
        return positive, negative
    found_by_selector = bool(positive or negative)

    # Older layout: each row has a coloured icon that says which side it is.
    # This also runs when the selectors above found only one side, because
    # the green-prefix selector matches the old layout but the red row
    # still has to be read from here.
    labeled = _labeled_review_rows(card)
    for kind, text in labeled:
        if kind == "positive" and not positive:
            positive = text
        elif kind == "negative" and not negative:
            negative = text

    # A single liked or disliked text on its own is normal, so only guess
    # from unlabeled rows when the selectors found nothing at all.
    if found_by_selector:
        return positive, negative

    # Rows without a label fill whichever side is still empty, in order.
    unknowns = [text for kind, text in labeled if not kind]
    if unknowns and not positive:
        positive = unknowns[0]
        unknowns = unknowns[1:]
    if unknowns and not negative:
        negative = unknowns[0]
    return positive, negative


def _card_scale(card):
    score_node = card.select_one(
        '[data-testid="review-score"], .bui-review-score__badge, .review-score-badge'
    )
    if not score_node:
        return ""
    for text in (score_node.get("aria-label") or "", score_node.get_text(" ", strip=True)):
        scale = review_rate_scale(text)
        if scale:
            return scale
    return ""


def parse_review_html(html):
    """Read review cards from rendered HTML."""
    soup = BeautifulSoup(html, "lxml")
    cards = []
    for selector in REVIEW_CARD_SELECTORS:
        cards = soup.select(selector)
        if cards:
            break

    reviews = []
    for card in cards:
        title = _selector_text(
            card,
            [
                '[data-testid="review-title"]',
                ".c-review-block__title",
                ".review_item_header_content",
            ],
        )
        positive, negative = _positive_negative(card)
        date_text = _selector_text(
            card,
            [
                '[data-testid="review-date"]',
                ".c-review-block__date",
                ".review_item_date",
                "time",
            ],
        )
        date_text = re.sub(r"^Reviewed:\s*", "", date_text, flags=re.IGNORECASE)
        scale = _card_scale(card)
        country = _review_country(card)
        if not any([title, positive, negative, scale, date_text, country]):
            continue
        reviews.append(make_review(title, positive, negative, date_text, country, scale))
    return reviews


# --------------------------------------------------------------------------
# Reviews from JSON embedded in the hotel page
# --------------------------------------------------------------------------

def _nested_string(obj, keys):
    """Return the first non-empty string under keys, on obj or a guest-like child."""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nest in ("guest", "reviewer", "user", "author", "customer", "traveler"):
        child = obj.get(nest)
        if not isinstance(child, dict):
            continue
        for key in keys:
            value = child.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _json_score(obj):
    for key in ("score", "reviewScore", "rating", "averageScore"):
        value = obj.get(key)
        if isinstance(value, dict):
            value = value.get("score") or value.get("value")
        if isinstance(value, (int, float, str)) and str(value).strip():
            scale = review_rate_scale(str(value))
            if scale:
                return scale
    return ""


REVIEW_TEXT_KEYS = {
    "positiveText",
    "negativeText",
    "positive_text",
    "negative_text",
    "pros",
    "cons",
}


def _walk_reviews(node, found):
    """Collect every dict in node that looks like it holds review text."""
    if isinstance(node, dict):
        if set(node) & REVIEW_TEXT_KEYS:
            found.append(node)
        for value in node.values():
            _walk_reviews(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_reviews(value, found)


def _script_payloads(html):
    """Return (soup, payloads) for the <script> JSON blobs that mention reviews."""
    soup = BeautifulSoup(html, "lxml")
    payloads = []
    for script in soup.find_all("script"):
        raw = (script.string or script.get_text() or "").strip()
        if "positiveText" not in raw and "FeaturedReview" not in raw:
            continue
        if not raw or raw[0] not in "{[":
            continue
        try:
            payloads.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return soup, payloads


def _collect_typed_reviews(payload):
    """Split the payload into FeaturedReview and PropertyFeaturedReview records.

    Both describe the same reviews with different fields, so they are keyed
    by review URL and joined later.
    """
    featured = {}
    detailed = {}

    def walk(node):
        if isinstance(node, dict):
            name = node.get("__typename")
            if name == "FeaturedReview":
                key = node.get("reviewUrl") or node.get("id")
                if key:
                    featured[str(key)] = node
            elif name == "PropertyFeaturedReview":
                key = node.get("reviewUrl") or node.get("reviewId")
                if key:
                    detailed[str(key)] = node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return featured, detailed


def _review_from_pair(featured, detailed, display_countries):
    featured = featured or {}
    detailed = detailed or {}
    details = _as_dict(detailed.get("textDetails"))
    guest = _as_dict(detailed.get("guestDetails"))

    positive = clean(featured.get("positiveText") or details.get("positiveText"))
    negative = clean(featured.get("negativeText") or details.get("negativeText"))
    title = clean(featured.get("title") or details.get("title"))

    score = featured.get("averageScore")
    if score in (None, ""):
        score = detailed.get("reviewScore")
    scale = _score_out_of_ten(score)

    date_text = _timestamp_to_date(
        featured.get("completed") or detailed.get("reviewedDate")
    )

    guest_name = clean(featured.get("guestName") or guest.get("username"))
    country = display_countries.get(guest_name, "")
    if not country:
        country = country_name(
            featured.get("guestCountryCode") or guest.get("countryCode")
        )
    if _is_traveler_type(country):
        country = ""

    if not any([title, positive, negative, scale]):
        return None
    return make_review(title, positive, negative, date_text, country, scale)


def _display_countries(soup):
    """Map guest name -> country as shown under the flag on featured reviews."""
    found = {}
    for card in soup.select('[data-testid="featuredreview"]'):
        flag = card.select_one("img[alt]")
        name = card.select_one("h4")
        if not flag or not name:
            continue
        country = clean(flag.get("alt"))
        guest = clean(name.get_text())
        if country and guest:
            found[guest] = country
    return found


def _visible_quotes(soup):
    quotes = []
    for node in soup.select('[data-testid="featuredreview-text"] blockquote'):
        text = clean(node.get_text(" ", strip=True)).strip("“”\"' ")
        if text and text not in quotes:
            quotes.append(text)
    return quotes


def _repair_clipped_text(reviews, quotes):
    """Booking sometimes drops the first letter of a review in the embedded text."""
    for review in reviews:
        positive = review.get("positive") or ""
        if len(positive) < 8:
            continue
        for quote in quotes:
            if quote == positive:
                break
            extra = len(quote) - len(positive)
            if quote.endswith(positive) and 0 < extra <= 2:
                if review.get("negative"):
                    review["positive"] = quote[:extra] + positive
                else:
                    review["positive"] = quote
                break


def _loose_reviews(payloads):
    """Older pages expose the text without the featured-review wrapper."""
    reviews = []
    for payload in payloads:
        objects = []
        _walk_reviews(payload, objects)
        for obj in objects:
            if obj.get("__typename") in ("TextDetails", "ReviewSummary"):
                continue
            positive = clean(
                _nested_string(obj, ("positiveText", "positive_text", "pros", "liked"))
            )
            negative = clean(
                _nested_string(obj, ("negativeText", "negative_text", "cons", "disliked"))
            )
            title = clean(_nested_string(obj, ("title", "reviewTitle", "headline")))
            scale = _json_score(obj)
            date_text = _timestamp_to_date(
                obj.get("completed")
                or obj.get("reviewedDate")
                or obj.get("completedAt")
            )
            country = country_name(
                _nested_string(
                    obj,
                    ("guestCountryCode", "countryCode", "countryName", "reviewerCountry"),
                )
            )
            if not any([title, positive, negative, scale]):
                continue
            reviews.append(make_review(title, positive, negative, date_text, country, scale))
    return reviews


def parse_review_json(html):
    """Read the review records embedded in the hotel page."""
    soup, payloads = _script_payloads(html)
    display_countries = _display_countries(soup)

    featured = {}
    detailed = {}
    for payload in payloads:
        found_featured, found_detailed = _collect_typed_reviews(payload)
        featured.update(found_featured)
        detailed.update(found_detailed)

    reviews = []
    keys = list(featured) + [key for key in detailed if key not in featured]
    for key in dict.fromkeys(keys):
        review = _review_from_pair(featured.get(key), detailed.get(key), display_countries)
        if review:
            reviews.append(review)

    if reviews:
        _repair_clipped_text(reviews, _visible_quotes(soup))
        return reviews
    return _loose_reviews(payloads)


def parse_reviews(html):
    """Prefer the embedded JSON. Fall back to the rendered review cards."""
    from_json = parse_review_json(html)
    if from_json:
        return from_json
    return parse_review_html(html)


def review_key(review):
    """Identity used to drop duplicate reviews across sources."""
    return (
        clean(review.get("title")).lower(),
        clean(review.get("positive")).lower(),
        clean(review.get("negative")).lower(),
        clean(review.get("date")).lower(),
        clean(review.get("scale")),
        clean(review.get("country")).lower(),
    )


def merge_reviews(existing, extra, limit):
    """Append reviews from extra that are not already in existing, up to limit."""
    merged = list(existing)
    seen = {review_key(item) for item in merged}
    for item in extra:
        key = review_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged[:limit]


# --------------------------------------------------------------------------
# Browser helpers for the hotel page
# --------------------------------------------------------------------------

def _click_first(page, locator):
    try:
        if locator.count() and locator.first.is_visible():
            locator.first.click(timeout=4000)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        return False
    return False


def _scroll_and_click(locator, timeout):
    """Scroll to the element and click it, forcing the click if it is covered."""
    try:
        locator.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        locator.click(timeout=timeout)
        return True
    except Exception:
        pass
    try:
        locator.click(force=True, timeout=2000)
        return True
    except Exception:
        return False


def scroll_reviews(page, rounds=6):
    """Scroll the review area and press "load more" until it stops growing."""
    last = -1
    idle = 0
    for _ in range(rounds):
        count = 0
        for selector in (REVIEW_CARD, ".review_list_new_item_block", ".c-review-block"):
            count = page.locator(selector).count()
            if count:
                break
        if count == last:
            idle += 1
        else:
            idle = 0
            last = count
        if idle >= 2:
            break
        page.evaluate(
            """() => {
                const selectors = [
                    '[data-testid="review-card"]',
                    '.review_list_new_item_block',
                    '.c-review-block'
                ];
                let nodes = [];
                for (const selector of selectors) {
                    nodes = document.querySelectorAll(selector);
                    if (nodes.length) break;
                }
                const last = nodes[nodes.length - 1];
                if (last) last.scrollIntoView({block: 'end'});
                window.scrollBy(0, Math.max(window.innerHeight, 600));
            }"""
        )
        load_more = page.get_by_role(
            "button", name=re.compile(r"load more|show more", re.IGNORECASE)
        )
        _click_first(page, load_more)
        page.wait_for_timeout(900)


def clean_address(text):
    """Drop the extra text Booking puts around the address in the header."""
    address = clean(text)
    address = re.split(r"After booking\b", address, maxsplit=1, flags=re.IGNORECASE)[0]
    address = re.sub(r"Great location.*", "", address, flags=re.IGNORECASE)
    address = re.sub(r"\bShow on map\b", "", address, flags=re.IGNORECASE)
    address = re.sub(r"\s+[–-]\s*", " ", address)
    return clean(address).strip(" -–")


def read_hotel_header(page):
    """Read name, address and rating from the top of a hotel page."""
    try:
        data = page.evaluate(
            """() => {
                const text = (selector) => {
                    const el = document.querySelector(selector);
                    return el ? el.innerText.replace(/\\s+/g, ' ').trim() : '';
                };
                const addressNode = document.querySelector(
                    '[data-testid="PropertyHeaderAddressDesktop-wrapper"] button, [data-testid="address"], .hp_address_subtitle'
                );
                let address = '';
                if (addressNode) {
                    const copy = addressNode.cloneNode(true);
                    copy.querySelectorAll('[aria-hidden="true"]').forEach((node) => node.remove());
                    address = copy.innerText.replace(/\\s+/g, ' ').trim();
                }
                return {
                    name: text('[data-testid="property-name"]')
                        || text('h2.pp-header__title')
                        || text('#hp_hotel_name')
                        || text('h1'),
                    address: address,
                    rating: text('[data-testid="review-score-right-component"]')
                        || text('[data-testid="review-score-component"]')
                        || text('[data-testid="review-score"]')
                        || text('.review-score-badge')
                };
            }"""
        )
    except Exception:
        return {"name": "", "address": "", "rating": ""}
    return {
        "name": clean(data.get("name")),
        "address": clean_address(data.get("address")),
        "rating": extract_score(data.get("rating")),
    }


def collect_visible_reviews(page, limit):
    """Reviews that are on the hotel page without opening the review panel."""
    scroll_reviews(page)
    html = page.content()
    return merge_reviews([], parse_reviews(html), limit)


# --------------------------------------------------------------------------
# Paginated "Read all reviews" panel
# --------------------------------------------------------------------------

def _open_full_review_list(page):
    """Open the full review list. The hotel page itself only shows the first screen."""
    selectors = (
        '[data-testid="review-score-read-all-actionable"]',
        '[data-testid="fr-read-all-reviews"]',
        '[data-testid="review-score-read-all"]',
        '[data-testid="Property-Header-Nav-Tab-Trigger-reviews"]',
    )
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        if not _scroll_and_click(locator.first, timeout=4000):
            continue
        for _ in range(10):
            if page.locator(REVIEW_CARD).count():
                return True
            page.wait_for_timeout(400)
    return page.locator(REVIEW_CARD).count() > 0


def _review_page_marker(page):
    """Text of the first review card, used to tell when the page has changed."""
    try:
        return page.locator(REVIEW_CARD).first.inner_text()[:180]
    except Exception:
        return ""


def _click_next_review_page(page):
    pattern = re.compile(r"next page", re.IGNORECASE)
    for role in ("button", "link"):
        buttons = page.get_by_role(role, name=pattern)
        # The pager is usually the last match, so search from the end.
        for index in range(buttons.count() - 1, -1, -1):
            button = buttons.nth(index)
            try:
                if not button.is_visible():
                    continue
                if button.get_attribute("disabled") is not None:
                    continue
                if (button.get_attribute("aria-disabled") or "").lower() == "true":
                    continue
                button.click(timeout=4000)
                return True
            except Exception:
                continue
    return False


def _wait_for_new_review_page(page, old_marker):
    for _ in range(10):
        if _review_page_marker(page) != old_marker:
            return True
        page.wait_for_timeout(400)
    return False


def collect_review_pages(page, limit):
    """Page through the full review list until limit reviews are collected."""
    if not _open_full_review_list(page):
        return []
    collected = []
    seen_markers = set()
    while len(collected) < limit:
        marker = _review_page_marker(page)
        if marker and marker in seen_markers:
            break
        if marker:
            seen_markers.add(marker)

        container = page.locator('[data-testid="review-list-container"]')
        if container.count():
            html = container.first.inner_html()
        else:
            html = page.content()
        collected = merge_reviews(collected, parse_review_html(html), limit)
        if len(collected) >= limit:
            break

        if not _click_next_review_page(page):
            break
        if not _wait_for_new_review_page(page, marker):
            break
    return collected[:limit]


# --------------------------------------------------------------------------
# Booking's ReviewList GraphQL endpoint
# --------------------------------------------------------------------------

def _review_total(value):
    """Read a review count that may be a number, a digit string or a nested dict."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, dict):
        for key in ("count", "total", "value"):
            total = _review_total(value.get(key))
            if total:
                return total
    return 0


def _remember_review_list(found):
    """Keep the first ReviewList request so later pages can reuse it."""

    def on_request(request):
        if found.get("body") is not None:
            return
        if request.method != "POST" or "graphql" not in request.url:
            return
        raw = request.post_data or ""
        if "ReviewList" not in raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        bodies = payload if isinstance(payload, list) else [payload]
        for body in bodies:
            if isinstance(body, dict) and body.get("operationName") == "ReviewList":
                found["url"] = request.url
                found["body"] = body
                return

    return on_request


def _wait_for_review_request(page, found, tries=10):
    for _ in range(tries):
        if found.get("body") is not None:
            return True
        page.wait_for_timeout(400)
    return found.get("body") is not None


def _trigger_review_list(page, found):
    """Make the page send a ReviewList request if it has not sent one yet."""
    if _wait_for_review_request(page, found, tries=4):
        return True
    for selector in (
        '[data-testid="Property-Header-Nav-Tab-Trigger-reviews"]',
        '[data-testid="review-score-read-all-actionable"]',
        '[data-testid="fr-read-all-reviews"]',
    ):
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        if not _scroll_and_click(locator.first, timeout=5000):
            continue
        if _wait_for_review_request(page, found, tries=8):
            return True
    return False


def _review_from_api_card(card):
    if not isinstance(card, dict):
        return None
    details = _as_dict(card.get("textDetails"))
    guest = _as_dict(card.get("guestDetails"))

    positive = clean(details.get("positiveText"))
    negative = clean(details.get("negativeText"))
    title = clean(details.get("title"))
    scale = _score_out_of_ten(card.get("reviewScore"))

    country = clean(guest.get("countryName")) or country_name(guest.get("countryCode"))
    if _is_traveler_type(country):
        country = ""

    if not any([title, positive, negative, scale]):
        return None
    date_text = _timestamp_to_date(card.get("reviewedDate"))
    return make_review(title, positive, negative, date_text, country, scale)


def _fetch_review_cards(page, url, body, skip, limit):
    """Replay the captured ReviewList request with a new skip/limit.

    The request runs inside the page so it carries the browser's cookies.
    """
    return page.evaluate(
        """async ({url, body, skip, limit}) => {
            const next = JSON.parse(JSON.stringify(body));
            if (!next.variables || !next.variables.input) {
                return {ok: false, reviews: [], total: 0};
            }
            next.variables.input.skip = skip;
            next.variables.input.limit = limit;
            const response = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                headers: {'content-type': 'application/json'},
                body: JSON.stringify(next)
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {
                return {ok: false, reviews: [], total: 0};
            }
            const node = payload && payload.data && payload.data.reviewListFrontend;
            if (!response.ok || !node) {
                return {ok: false, reviews: [], total: 0};
            }
            return {
                ok: true,
                total: node.reviewsCount || 0,
                reviews: node.reviewCard || []
            };
        }""",
        {"url": url, "body": body, "skip": skip, "limit": limit},
    )


def _collect_api_reviews(page, found, limit):
    """Page Booking's review list until limit reviews are collected.

    The hotel page loads one screen. The list itself accepts 25 reviews
    per request, so further reviews come from later skip offsets.
    Returns (reviews, exhausted). exhausted means the hotel has no more.
    """
    url = found.get("url")
    body = found.get("body")
    if not url or not isinstance(body, dict):
        return [], False

    collected = []
    seen = set()
    skip = 0
    total = 0
    exhausted = False
    while len(collected) < limit:
        try:
            result = _fetch_review_cards(page, url, body, skip, REVIEW_PAGE_SIZE)
        except Exception as error:
            log(f"  could not read the review list: {error}")
            break
        if not result or not result.get("ok"):
            break

        total = _review_total(result.get("total")) or total
        batch = result.get("reviews") or []
        if not batch:
            exhausted = True
            break

        added = 0
        for card in batch:
            key = ""
            if isinstance(card, dict):
                key = str(card.get("reviewUrl") or "")
            review = _review_from_api_card(card)
            identity = key or (review_key(review) if review else "")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            added += 1
            if review:
                collected.append(review)
            if len(collected) >= limit:
                break

        skip += len(batch)
        if added == 0 or len(batch) < REVIEW_PAGE_SIZE or (total and skip >= total):
            exhausted = True
            break

    if total:
        log(f"  review list has {total} reviews")
    return collected[:limit], exhausted


# --------------------------------------------------------------------------
# Entry point for one hotel
# --------------------------------------------------------------------------

def _collect_reviews(page, max_reviews, found):
    """Use the review API first, then fill up from what the page shows."""
    _trigger_review_list(page, found)
    api_reviews, exhausted = _collect_api_reviews(page, found, max_reviews)
    if len(api_reviews) >= max_reviews or exhausted:
        return api_reviews

    featured = collect_visible_reviews(page, max_reviews)
    paged = collect_review_pages(page, max_reviews)
    if paged and len(paged) >= len(featured):
        visible = paged
    elif paged:
        visible = merge_reviews(featured, paged, max_reviews)
    else:
        visible = featured
    return merge_reviews(api_reviews, visible, max_reviews)


def scrape_hotel_reviews(page, hotel, destination, max_reviews):
    """Open one hotel and return one output row per review."""
    name = hotel.get("name") or ""
    address = hotel.get("address") or ""
    price = hotel.get("price") or ""
    rating = hotel.get("rating") or ""
    hotel_url = hotel.get("url") or ""
    reviews = []

    if hotel_url:
        found = {}
        on_request = _remember_review_list(found)
        page.on("request", on_request)
        try:
            page.goto(hotel_url, wait_until="domcontentloaded", timeout=60000)
            dismiss_overlays(page)

            # The hotel page has better address and rating than the search card.
            header = read_hotel_header(page)
            name = name or header["name"]
            address = header["address"] or address
            rating = header["rating"] or rating

            try:
                page.wait_for_selector(
                    '[data-testid="featuredreview"], [data-testid="review-card"], .review_list_new_item_block',
                    timeout=12000,
                )
            except Exception:
                pass

            reviews = _collect_reviews(page, max_reviews, found)
            log(f"  reviews collected: {len(reviews)}")
        except Exception as error:
            log(f"  could not open hotel page: {error}")
        finally:
            try:
                page.remove_listener("request", on_request)
            except Exception:
                pass

    # Keep the hotel in the output even when no review could be read.
    if not reviews:
        reviews = [make_review()]

    return [
        {
            "hotel_name": name,
            "destination": destination,
            "address": address,
            "price_text": price,
            "numeric_rating": rating,
            "review_title": review.get("title") or "",
            "review_positive": review.get("positive") or "",
            "review_negative": review.get("negative") or "",
            "review_date": review.get("date") or "",
            "review_country": review.get("country") or "",
            "review_rate_scale": review.get("scale") or "",
        }
        for review in reviews[:max_reviews]
    ]
