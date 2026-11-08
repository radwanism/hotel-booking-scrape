import json
import unittest

import hotel_page
from hotel_page import (
    clean_address,
    country_name,
    extract_score,
    merge_reviews,
    parse_review_html,
    parse_review_json,
    parse_reviews,
    review_rate_scale,
)


CURRENT_LAYOUT = """
<div data-testid="review-card">
  <h3 data-testid="review-title">Great stay</h3>
  <div data-testid="review-positive-text">Liked: clean rooms</div>
  <div data-testid="review-negative-text">noisy street</div>
  <span data-testid="review-date">Reviewed: 3 May 2025</span>
  <div data-testid="review-score" aria-label="Scored 9.0">9.0</div>
  <span data-testid="review-country">Couple</span>
  <img src="/static/flags/de.png" alt="Germany">
</div>
<div data-testid="review-card"></div>
"""

OLD_LAYOUT = """
<div class="c-review-block">
  <div class="c-review-block__title">Fine</div>
  <div class="c-review__row">
    <span class="c-review__prefix c-review__prefix--color-green"></span>
    <span class="c-review__body">Nice pool</span>
  </div>
  <div class="c-review__row">
    <span class="c-review__prefix c-review__prefix--color-red"></span>
    <span class="c-review__body">Slow check-in</span>
  </div>
  <div class="bui-review-score__badge">8.0</div>
  <div class="bui-avatar-block__subtitle">Italy</div>
</div>
"""

# Old layout where only the "disliked" row is coloured.
OLD_LAYOUT_UNLABELED = """
<div class="c-review-block">
  <div class="c-review__row"><span class="c-review__body">Nice pool</span></div>
  <div class="c-review__row">
    <span class="c-review__prefix c-review__prefix--color-red"></span>
    <span class="c-review__body">Slow check-in</span>
  </div>
</div>
"""


def page_with_json(payload, extra_html=""):
    return "<html><body>{0}<script>{1}</script></body></html>".format(
        extra_html, json.dumps(payload)
    )


class ScoreTests(unittest.TestCase):
    def test_extract_score(self):
        self.assertEqual(extract_score("Scored 8.5"), "8.5")
        self.assertEqual(extract_score("9.2/10"), "9.2")
        self.assertEqual(extract_score("Rated 11 times, score 6"), "6")
        self.assertEqual(extract_score(""), "")
        self.assertEqual(extract_score(None), "")

    def test_review_rate_scale_keeps_the_denominator(self):
        self.assertEqual(review_rate_scale("Scored 8.5"), "8.5/10")
        self.assertEqual(review_rate_scale("4 out of 5"), "4/5")
        self.assertEqual(review_rate_scale("no score"), "")


class SmallHelperTests(unittest.TestCase):
    def test_country_name(self):
        self.assertEqual(country_name("gb"), "United Kingdom")
        self.assertEqual(country_name("EG"), "Egypt")
        self.assertEqual(country_name("zz"), "ZZ")
        self.assertEqual(country_name("Egypt"), "Egypt")
        self.assertEqual(country_name(""), "")

    def test_clean_address_drops_booking_extras(self):
        raw = "12 Nile St - Cairo After booking, all of the property's details"
        self.assertEqual(clean_address(raw), "12 Nile St Cairo")
        self.assertEqual(clean_address("4 Road, Giza Show on map"), "4 Road, Giza")

    def test_merge_reviews_skips_duplicates_and_respects_limit(self):
        existing = [{"title": "A"}, {"title": "B"}]
        extra = [{"title": " a "}, {"title": "C"}, {"title": "D"}]
        merged = merge_reviews(existing, extra, limit=3)
        self.assertEqual([item["title"] for item in merged], ["A", "B", "C"])


class HtmlReviewTests(unittest.TestCase):
    def test_current_layout(self):
        reviews = parse_review_html(CURRENT_LAYOUT)
        self.assertEqual(
            reviews,
            [
                {
                    "title": "Great stay",
                    "positive": "clean rooms",
                    "negative": "noisy street",
                    "date": "3 May 2025",
                    # "Couple" is a traveller type, so the flag is used instead.
                    "country": "Germany",
                    "scale": "9.0/10",
                }
            ],
        )

    def test_old_layout(self):
        [review] = parse_review_html(OLD_LAYOUT)
        self.assertEqual(review["title"], "Fine")
        self.assertEqual(review["positive"], "Nice pool")
        self.assertEqual(review["scale"], "8.0/10")
        self.assertEqual(review["country"], "Italy")

    def test_old_layout_keeps_the_disliked_text(self):
        # The green-prefix selector fills "positive" first. The red row must
        # still be read for "negative".
        [review] = parse_review_html(OLD_LAYOUT)
        self.assertEqual(review["negative"], "Slow check-in")

    def test_unlabeled_rows_are_not_guessed_when_one_side_is_known(self):
        html = """
        <div data-testid="review-card">
          <div data-testid="review-positive-text">Great breakfast</div>
          <div class="c-review__row"><span class="c-review__body">Stayed 2 nights</span></div>
        </div>
        """
        [review] = parse_review_html(html)
        self.assertEqual(review["positive"], "Great breakfast")
        self.assertEqual(review["negative"], "")

    def test_old_layout_unlabeled_rows_fill_the_empty_side(self):
        [review] = parse_review_html(OLD_LAYOUT_UNLABELED)
        self.assertEqual(review["positive"], "Nice pool")
        self.assertEqual(review["negative"], "Slow check-in")

    def test_page_without_reviews(self):
        self.assertEqual(parse_reviews("<p>nothing here</p>"), [])


class JsonReviewTests(unittest.TestCase):
    def test_featured_and_detailed_records_are_joined(self):
        payload = {
            "data": [
                {
                    "__typename": "FeaturedReview",
                    "reviewUrl": "r1",
                    "title": "Lovely",
                    "positiveText": "he staff was lovely",
                    "averageScore": 9,
                    "completed": 1700000000,
                    "guestName": "Sam",
                    "guestCountryCode": "gb",
                },
                {
                    "__typename": "PropertyFeaturedReview",
                    "reviewUrl": "r2",
                    "reviewScore": 6.5,
                    "reviewedDate": 1700000000000,
                    "textDetails": {"title": "Meh", "negativeText": "cold water"},
                    "guestDetails": {"username": "Lee", "countryCode": "eg"},
                },
            ]
        }
        # The visible card gives the country under the flag and the full text.
        visible = (
            '<div data-testid="featuredreview"><img alt="Scotland"><h4>Sam</h4>'
            '<div data-testid="featuredreview-text">'
            "<blockquote>“The staff was lovely”</blockquote></div></div>"
        )
        reviews = parse_review_json(page_with_json(payload, visible))

        self.assertEqual(len(reviews), 2)
        first, second = reviews
        self.assertEqual(first["positive"], "The staff was lovely")
        self.assertEqual(first["country"], "Scotland")
        self.assertEqual(first["scale"], "9/10")
        self.assertEqual(first["date"], "2023-11-14")
        self.assertEqual(second["title"], "Meh")
        self.assertEqual(second["negative"], "cold water")
        self.assertEqual(second["country"], "Egypt")
        self.assertEqual(second["scale"], "6.5/10")
        self.assertEqual(second["date"], "2023-11-14")

    def test_older_pages_without_typenames(self):
        payload = {
            "reviews": [
                {
                    "positiveText": "fine",
                    "cons": "far from the beach",
                    "reviewer": {"countryCode": "fr"},
                    "rating": {"value": "8"},
                }
            ]
        }
        [review] = parse_review_json(page_with_json(payload))
        self.assertEqual(review["positive"], "fine")
        self.assertEqual(review["negative"], "far from the beach")
        self.assertEqual(review["country"], "France")
        self.assertEqual(review["scale"], "8/10")


class FakePage:
    """Stands in for a Playwright page and answers ReviewList requests."""

    def __init__(self, total, page_size=hotel_page.REVIEW_PAGE_SIZE):
        self.total = total
        self.page_size = page_size
        self.requests = []

    def evaluate(self, _script, args):
        skip = args["skip"]
        self.requests.append(skip)
        count = max(0, min(self.page_size, self.total - skip))
        cards = [
            {
                "reviewUrl": "review-{0}".format(skip + offset),
                "reviewScore": 8,
                "textDetails": {"positiveText": "text {0}".format(skip + offset)},
            }
            for offset in range(count)
        ]
        return {"ok": True, "total": self.total, "reviews": cards}


class ReviewApiTests(unittest.TestCase):
    FOUND = {"url": "https://www.booking.com/dml/graphql", "body": {"variables": {"input": {}}}}

    def test_pages_until_the_hotel_runs_out(self):
        page = FakePage(total=30)
        reviews, exhausted = hotel_page._collect_api_reviews(page, self.FOUND, limit=100)
        self.assertEqual(len(reviews), 30)
        self.assertTrue(exhausted)
        self.assertEqual(page.requests, [0, 25])

    def test_stops_at_the_limit(self):
        page = FakePage(total=300)
        reviews, exhausted = hotel_page._collect_api_reviews(page, self.FOUND, limit=40)
        self.assertEqual(len(reviews), 40)
        self.assertFalse(exhausted)
        self.assertEqual(page.requests, [0, 25])

    def test_without_a_captured_request(self):
        self.assertEqual(hotel_page._collect_api_reviews(FakePage(10), {}, 5), ([], False))

    def test_api_card_drops_traveller_type_as_country(self):
        card = {
            "reviewScore": 10,
            "textDetails": {"title": "Top"},
            "guestDetails": {"countryName": "Family"},
        }
        review = hotel_page._review_from_api_card(card)
        self.assertEqual(review["country"], "")
        self.assertEqual(review["scale"], "10/10")


if __name__ == "__main__":
    unittest.main()
