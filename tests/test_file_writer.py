import csv
import json
import os
import tempfile
import unittest

from file_writer import COLUMN_KEYS, FileWriter


ROWS = [
    {"hotel_name": "فندق النيل", "destination": "Cairo, Egypt", "review_positive": "Café was great"},
    {"hotel_name": "Second", "not_a_column": "ignored"},
]


class FileWriterTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def write(self, out_format):
        writer = FileWriter(ROWS, out_format, country="Egypt", town="Sharm El Sheikh",
                            output_dir=self.folder.name)
        return writer.output_file()

    def test_file_name(self):
        path = self.write("json")
        self.assertEqual(os.path.basename(path), "hotels-in-Sharm-El-Sheikh-Egypt.json")

    def test_json_keeps_unicode_and_fills_missing_columns(self):
        with open(self.write("json"), encoding="utf-8") as handle:
            rows = json.load(handle)
        self.assertEqual(rows[0]["hotel_name"], "فندق النيل")
        self.assertEqual(list(rows[1]), COLUMN_KEYS)
        self.assertEqual(rows[1]["address"], "")

    def test_csv_has_bom_and_header(self):
        path = self.write("csv")
        with open(path, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"\xef\xbb\xbf"))
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["review_positive"], "Café was great")

    def test_excel_uses_readable_headings(self):
        from openpyxl import load_workbook

        sheet = load_workbook(self.write("excel")).active
        self.assertEqual(sheet.title, "Reviews")
        self.assertEqual(sheet["A1"].value, "Hotel name")
        self.assertEqual(sheet["A2"].value, "فندق النيل")
        self.assertEqual(sheet.freeze_panes, "A2")

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            self.write("xml")


if __name__ == "__main__":
    unittest.main()
