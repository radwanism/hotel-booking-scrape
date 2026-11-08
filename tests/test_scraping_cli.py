import contextlib
import io
import os
import tempfile
import unittest

import scraping


def parse(argv):
    return scraping.build_parser().parse_args(argv)


def destinations(argv):
    return scraping.destinations_from_args(parse(argv))


class DestinationArgumentTests(unittest.TestCase):
    def test_country_then_towns(self):
        self.assertEqual(
            destinations(["-c", "Egypt", "-t", "Cairo, Luxor", "-c", "Morocco", "-t", "Fes"]),
            [("Egypt", "Cairo"), ("Egypt", "Luxor"), ("Morocco", "Fes")],
        )

    def test_destination_flag_splits_on_the_last_comma(self):
        self.assertEqual(
            destinations(["-d", "Sharm El Sheikh, South Sinai, Egypt"]),
            [("Egypt", "Sharm El Sheikh, South Sinai")],
        )

    def test_duplicates_are_dropped(self):
        with contextlib.redirect_stdout(io.StringIO()):
            found = destinations(["-c", "Egypt", "-t", "Cairo", "-d", " cairo , egypt "])
        self.assertEqual(found, [("Egypt", "Cairo")])

    def test_bad_combinations_exit(self):
        bad_inputs = [
            ["-t", "Cairo"],
            ["-c", "Egypt", "-c", "Morocco"],
            ["-d", "Cairo"],
            ["-c", " "],
        ]
        for argv in bad_inputs:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse(argv)

    def test_country_without_town_exits(self):
        with self.assertRaises(SystemExit):
            destinations(["-c", "Egypt"])


class PlacesFileTests(unittest.TestCase):
    def write_places(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        with handle:
            handle.write(text)
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def test_reads_lines_and_skips_comments(self):
        path = self.write_places("# my list\n\nCairo, Egypt\nMarrakech, Morocco\n")
        self.assertEqual(
            scraping.read_places_file(path),
            [("Egypt", "Cairo"), ("Morocco", "Marrakech")],
        )

    def test_reports_the_bad_line(self):
        path = self.write_places("Cairo, Egypt\nLuxor\n")
        with self.assertRaises(SystemExit) as raised:
            scraping.read_places_file(path)
        self.assertIn(":2:", str(raised.exception))


class OtherOptionTests(unittest.TestCase):
    def test_defaults(self):
        args = parse(["-d", "Cairo, Egypt"])
        self.assertEqual(args.out_format, "json")
        self.assertEqual(args.max_hotels, 20)
        self.assertEqual(args.max_reviews, 8)
        self.assertIsNone(args.output_dir)
        self.assertFalse(args.headless)

    def test_output_dir(self):
        self.assertEqual(parse(["-d", "Cairo, Egypt", "--output-dir", "out"]).output_dir, "out")

    def test_invalid_date(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse(["-d", "Cairo, Egypt", "-s", "2025-13-01"])

    def test_numbers_must_be_positive(self):
        with self.assertRaises(SystemExit) as raised:
            scraping.main(["-d", "Cairo, Egypt", "--max-hotels", "0"])
        self.assertEqual(str(raised.exception), "--max-hotels must be at least 1.")


class SearchUrlTests(unittest.TestCase):
    def test_includes_dest_id_only_when_known(self):
        with_id = scraping.build_search_url(
            "Cairo, Egypt", "-290692", "", "2025-01-01", "2025-01-02", rooms=2, persons=3
        )
        self.assertIn("dest_id=-290692", with_id)
        self.assertIn("dest_type=city", with_id)
        self.assertIn("no_rooms=2", with_id)
        self.assertIn("group_adults=3", with_id)

        without_id = scraping.build_search_url("Cairo", "", "", "a", "b", rooms=1)
        self.assertNotIn("dest_id", without_id)

    def test_same_place(self):
        self.assertTrue(scraping._same_place("Cairo", "cairo"))
        self.assertTrue(scraping._same_place("Cairo Governorate", "Cairo"))
        self.assertFalse(scraping._same_place("New Cairo", "Cairo"))


if __name__ == "__main__":
    unittest.main()
