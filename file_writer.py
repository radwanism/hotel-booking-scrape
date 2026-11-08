"""Write scraped rows to JSON, CSV or Excel.

Every format uses the same columns in the same order. There is one row per
review, and the hotel fields repeat on each of that hotel's reviews.
"""

import csv
import json
import os
import re


# (key in the scraped row, heading used in the Excel sheet)
COLUMNS = [
    ("hotel_name", "Hotel name"),
    ("destination", "Destination"),
    ("address", "Address"),
    ("price_text", "Price text"),
    ("numeric_rating", "Numeric rating"),
    ("review_title", "Review title"),
    ("review_positive", "Review positive"),
    ("review_negative", "Review negative"),
    ("review_date", "Review date"),
    ("review_country", "Review country"),
    ("review_rate_scale", "Review rate scale"),
]
COLUMN_KEYS = [key for key, _heading in COLUMNS]

EXCEL_COLUMN_WIDTH = 28


def _slug(value):
    """Make a value safe for a file name: 'Sharm El Sheikh' -> 'Sharm-El-Sheikh'."""
    cleaned = re.sub(r"[^\w\-]+", "-", (value or "").strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "results"


class FileWriter:
    def __init__(self, data, out_format=None, country="", town="", output_dir=None):
        self.data = data
        self.format = (out_format or "json").lower()
        self.country = country
        self.town = town
        self.output_dir = output_dir or os.getcwd()

    def output_file(self):
        """Write the file and return its path."""
        if self.format in ("json", ""):
            return self._write_json()
        if self.format == "excel":
            return self._write_excel()
        if self.format == "csv":
            return self._write_csv()
        raise ValueError(f"Unsupported format: {self.format}")

    def _file_path(self, extension):
        name = f"hotels-in-{_slug(self.town)}-{_slug(self.country)}.{extension}"
        return os.path.join(self.output_dir, name)

    def _rows(self):
        return [{key: item.get(key, "") for key in COLUMN_KEYS} for item in self.data]

    def _write_json(self):
        path = self._file_path("json")
        with open(path, "w", encoding="utf-8") as outfile:
            json.dump(self._rows(), outfile, indent=2, ensure_ascii=False)
        return path

    def _write_csv(self):
        path = self._file_path("csv")
        # utf-8-sig so Excel opens Arabic and accented text correctly.
        with open(path, "w", encoding="utf-8-sig", newline="") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=COLUMN_KEYS)
            writer.writeheader()
            writer.writerows(self._rows())
        return path

    def _write_excel(self):
        # Imported here so JSON and CSV output work without openpyxl installed.
        from openpyxl import Workbook

        path = self._file_path("xlsx")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Reviews"

        for column, (_key, heading) in enumerate(COLUMNS, start=1):
            sheet.cell(row=1, column=column).value = heading
        for row, item in enumerate(self._rows(), start=2):
            for column, key in enumerate(COLUMN_KEYS, start=1):
                sheet.cell(row=row, column=column).value = item.get(key, "")

        for column in range(1, len(COLUMNS) + 1):
            letter = sheet.cell(row=1, column=column).column_letter
            sheet.column_dimensions[letter].width = EXCEL_COLUMN_WIDTH
        sheet.freeze_panes = "A2"
        workbook.save(path)
        return path
