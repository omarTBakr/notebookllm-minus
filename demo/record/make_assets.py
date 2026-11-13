"""Build the small source files the walkthrough uploads.

    cd src && uv run python ../demo/record/make_assets.py

Run with the application's environment, which already has PyMuPDF, Pillow and
openpyxl. The results are committed under demo/record/assets/, so this only
needs running again when a file should change. Everything is invented and
small on purpose: a recording should take minutes, and nothing here can be
answered from a model's memory.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pymupdf
from openpyxl import Workbook
from PIL import Image, ImageDraw

ASSETS = Path(__file__).parent / "assets"

QUARTERS = [("Q1", 4.2), ("Q2", 5.1), ("Q3", 6.4), ("Q4", 9.3)]


def revenue_chart() -> bytes:
    """A bar chart as a PNG: revenue by quarter, Q4 clearly the tallest."""
    width, height = 900, 560
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    left, bottom, top = 110, 470, 70
    draw.line([(left, top), (left, bottom), (width - 60, bottom)], fill="#333333", width=3)

    peak = max(value for _, value in QUARTERS)
    slot = (width - 60 - left) / len(QUARTERS)

    for index, (label, value) in enumerate(QUARTERS):
        x0 = left + index * slot + slot * 0.2
        x1 = left + (index + 1) * slot - slot * 0.2
        y0 = bottom - (bottom - top) * value / peak
        draw.rectangle([x0, y0, x1, bottom], fill="#3b5bdb" if label != "Q4" else "#2f9e44")
        draw.text(((x0 + x1) / 2 - 12, bottom + 14), label, fill="#333333")
        draw.text(((x0 + x1) / 2 - 16, y0 - 22), f"{value}M", fill="#333333")

    draw.text((left, 24), "Revenue by quarter, in millions", fill="#111111")

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def report_pdf(chart: bytes) -> bytes:
    """Three pages: prose, the chart with its caption, and a page that cites it."""
    doc = pymupdf.open()

    def page_with(lines: list[str], start_y: float = 90) -> pymupdf.Page:
        page = doc.new_page(width=595, height=842)
        y = start_y
        for line in lines:
            size = 18 if line.startswith("# ") else 11
            page.insert_text((60, y), line.removeprefix("# "), fontsize=size)
            y += 30 if size == 18 else 18
        return page

    page_with(
        [
            "# Northwind Outfitters: Annual Report",
            "",
            "Northwind Outfitters sells outdoor equipment through 38 shops and an online store.",
            "The company was founded in 1998 in the town of Kelby by Maren Aldous.",
            "It employs 1,240 people, of whom 310 work in its two warehouses.",
            "",
            "This year the company opened six new shops and closed two.",
            "Its best-selling product was the Ridgeline tent, with 41,000 units sold.",
            "Returns fell to 3.1 percent of orders, from 4.6 percent the year before.",
        ]
    )

    figure = page_with(["# Revenue"], start_y=80)
    figure.insert_image(pymupdf.Rect(70, 110, 525, 393), stream=chart)
    y = 420
    for line in [
        "Figure 1: Revenue by quarter, in millions of dollars.",
        "",
        "Total revenue for the year was 25.0 million dollars, up 18 percent.",
        "Online orders made up 44 percent of revenue.",
    ]:
        figure.insert_text((60, y), line, fontsize=11)
        y += 18

    page_with(
        [
            "# Outlook",
            "",
            "As Figure 1 shows, the fourth quarter was the strongest of the year,",
            "driven by the winter range and the opening of the Kelby flagship shop.",
            "",
            "For next year the company plans ten new shops and a third warehouse.",
            "It expects revenue of 30 million dollars.",
        ]
    )

    # Deflated: the chart is otherwise stored raw and the file is 1.5 MB.
    return doc.tobytes(deflate=True, garbage=3)


def team_csv() -> str:
    rows = [
        ("name", "role", "shop", "salary", "joined"),
        ("Ada Okafor", "Shop manager", "Kelby", 61000, 2015),
        ("Bram Lindqvist", "Buyer", "Head office", 58000, 2018),
        ("Chidi Mensah", "Warehouse lead", "Dunmore", 54000, 2012),
        ("Dalia Haddad", "Designer", "Head office", 66000, 2020),
        ("Emil Sorensen", "Shop assistant", "Kelby", 31000, 2022),
        ("Farah Qureshi", "Finance analyst", "Head office", 63000, 2019),
        ("Goran Petrov", "Driver", "Dunmore", 36000, 2017),
        ("Hana Suzuki", "Shop manager", "Marlow", 60000, 2016),
    ]
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue()


def sales_xlsx() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Q1 Sales"
    for row in [
        ("product", "units", "revenue", "region"),
        ("Ridgeline tent", 9200, 1104000, "North"),
        ("Trailhead boots", 7400, 814000, "North"),
        ("Summit jacket", 5100, 765000, "East"),
        ("Basecamp stove", 3900, 273000, "South"),
        ("Drift sleeping bag", 6100, 549000, "West"),
        ("Cairn backpack", 4800, 528000, "East"),
    ]:
        sheet.append(row)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    chart = revenue_chart()

    (ASSETS / "revenue_chart.png").write_bytes(chart)
    (ASSETS / "northwind_report.pdf").write_bytes(report_pdf(chart))
    (ASSETS / "team.csv").write_text(team_csv())
    (ASSETS / "sales.xlsx").write_bytes(sales_xlsx())

    for path in sorted(ASSETS.iterdir()):
        print(f"{path.name:24} {path.stat().st_size:>8} bytes")


if __name__ == "__main__":
    main()
