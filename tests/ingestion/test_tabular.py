from datetime import date, datetime

from openpyxl import Workbook

from application.services.ingest.tabular import (
    TabularLoader,
    continuation_prefix,
    row_identity,
)


def load_csv(tmp_path, content, encoding="utf-8"):
    path = tmp_path / "data.csv"
    path.write_bytes(content.encode(encoding) if isinstance(content, str) else content)
    return TabularLoader(path).load()


def load_xlsx(tmp_path, sheets, hidden=()):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
        if title in hidden:
            sheet.sheet_state = "hidden"
    path = tmp_path / "book.xlsx"
    workbook.save(path)
    return TabularLoader(path).load()


def test_csv_gives_one_document_per_row_with_column_names(tmp_path):
    docs = load_csv(tmp_path, "Name,City\nAda,Cairo\nLinus,Oslo\n")

    assert [d.page_content for d in docs] == ["Name: Ada\nCity: Cairo", "Name: Linus\nCity: Oslo"]


def test_row_numbers_follow_the_spreadsheet_with_blank_rows_counted(tmp_path):
    docs = load_csv(tmp_path, "Name\nAda\n\nLinus\n")

    assert [d.metadata["row"] for d in docs] == [2, 4]


def test_byte_order_mark_does_not_stick_to_the_first_column(tmp_path):
    docs = load_csv(tmp_path, b"\xef\xbb\xbfName,City\nAda,Cairo\n")

    assert docs[0].page_content.startswith("Name: Ada")


def test_semicolon_delimiter_is_detected(tmp_path):
    docs = load_csv(tmp_path, "id;name;city\n1;Ada;Cairo\n")

    assert docs[0].page_content == "id: 1\nname: Ada\ncity: Cairo"


def test_arabic_text_survives(tmp_path):
    docs = load_csv(tmp_path, "الاسم,المدينة\nعمر,القاهرة\n")

    assert docs[0].page_content == "الاسم: عمر\nالمدينة: القاهرة"


def test_blank_and_duplicate_headers_keep_every_value(tmp_path):
    docs = load_csv(tmp_path, "a,a,\n1,2,3\n")

    assert docs[0].page_content == "a: 1\na_2: 2\ncolumn_3: 3"


def test_short_rows_omit_empty_cells_and_never_say_none(tmp_path):
    docs = load_csv(tmp_path, "a,b,c\n1\n2,,4\n")

    assert docs[0].page_content == "a: 1"
    assert docs[1].page_content == "a: 2\nc: 4"
    assert all("None" not in d.page_content for d in docs)


def test_empty_file_and_header_only_give_nothing(tmp_path):
    assert load_csv(tmp_path, "") == []
    assert load_csv(tmp_path, "a,b\n") == []


def test_xlsx_rows_carry_sheet_and_real_row_number(tmp_path):
    docs = load_xlsx(tmp_path, {"Sales": [["month", "revenue"], ["March", 17747.0]]})

    assert docs[0].page_content == "sheet: Sales\nmonth: March\nrevenue: 17747"
    assert docs[0].metadata["sheet"] == "Sales"
    assert docs[0].metadata["row"] == 2


def test_xlsx_reads_every_visible_sheet_and_skips_hidden_ones(tmp_path):
    docs = load_xlsx(
        tmp_path,
        {"A": [["x"], [1]], "Secret": [["x"], [2]], "B": [["x"], [3]]},
        hidden={"Secret"},
    )

    assert [d.metadata["sheet"] for d in docs] == ["A", "B"]


def test_xlsx_dates_numbers_and_booleans_read_as_a_person_sees_them(tmp_path):
    docs = load_xlsx(
        tmp_path,
        {"S": [["d", "t", "n", "ok"], [date(2026, 3, 1), datetime(2026, 3, 1, 9, 30), 2.5, True]]},
    )

    text = docs[0].page_content
    assert "d: 2026-03-01" in text
    assert "t: 2026-03-01 09:30:00" in text
    assert "n: 2.5" in text
    assert "ok: TRUE" in text


def test_csv_rows_have_no_sheet_line(tmp_path):
    docs = load_csv(tmp_path, "a\n1\n")

    assert "sheet:" not in docs[0].page_content
    assert docs[0].metadata["table"] is True


def test_identity_uses_short_cells_only_and_respects_the_cap():
    pairs = [("id", "7"), ("note", "x" * 200), ("name", "Ada")]
    assert row_identity(pairs) == "id: 7\nname: Ada"

    many = [(f"c{i}", "v" * 60) for i in range(20)]
    identity = row_identity(many)
    assert len(identity) <= 300
    assert all(line.startswith("c") and line.endswith("v" * 60) for line in identity.split("\n"))


def test_continuation_prefix_names_the_row_and_its_identity():
    meta = {"sheet": "Sales", "row": 12, "identity": "id: 7\nname: Ada"}

    assert continuation_prefix(meta, 300) == "Sales · row 12 (continued)\nid: 7\nname: Ada\n"
    assert continuation_prefix({"sheet": "", "row": 3, "identity": ""}, 300) == "row 3 (continued)\n"


def test_continuation_prefix_is_shortened_to_the_limit():
    meta = {"sheet": "", "row": 3, "identity": "name: " + "x" * 100}

    assert len(continuation_prefix(meta, 40)) <= 41


def test_long_cell_row_chunks_repeat_the_row_identity(tmp_path):
    from application.services import ProcessService

    service = ProcessService(chunk_size=400, chunk_overlap=50)
    body = " ".join(f"word{i}" for i in range(600))
    docs = load_csv(tmp_path, f"id,name,notes\n9,Ada,{body}\n")

    chunks = service.split_file(docs)

    later = [c for c in chunks if c.metadata.get("start_index")]
    assert later
    assert all(c.page_content.startswith("row 2 (continued)\nid: 9\nname: Ada") for c in later)
    assert not chunks[0].page_content.startswith("row 2 (continued)")


def test_xlsx_preview_text_heads_each_row_with_where_it_is(tmp_path):
    from application.services.ingest.tabular import xlsx_preview_text

    load_xlsx(tmp_path, {"S": [["a"], [1], [2]]})

    text = xlsx_preview_text((tmp_path / "book.xlsx").read_bytes())

    assert text == "S · row 2\na: 1\n\nS · row 3\na: 2"
