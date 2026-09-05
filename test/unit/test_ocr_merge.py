"""Folding the per-engine runs back into one report.

Engines are benchmarked one process at a time, so the results arrive scattered
and have to be recombined. The rule that matters is supersession: an engine is
often run twice — once failing because of a bad language code or a wrong index
into a bounding box, then again after the fix — and the merged report must show
the fixed result rather than whichever landed last on disk.
"""

import json

from ocr.benchmark.merge import merge


def _write(directory, suite, payload):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{suite}.json").write_text(json.dumps(payload))


def _summary(name, ok=1, pages=1, **extra):
    return {"extractor": name, "ok": ok, "pages": pages, "usable": ok,
            "cer": None, "wer": None, "seconds": 1.0, "space_ratio": 0.17, **extra}


def test_engines_from_separate_directories_end_up_in_one_report(tmp_path):
    _write(tmp_path / "tesseract", "real", {"summary": [_summary("tesseract")], "runs": []})
    _write(tmp_path / "easyocr", "real", {"summary": [_summary("easyocr")], "runs": []})

    combined = merge(tmp_path, "real")

    assert {row["extractor"] for row in combined["summary"]} == {"tesseract", "easyocr"}


def test_a_successful_rerun_supersedes_a_failed_first_attempt(tmp_path):
    """PaddleOCR failed with the wrong language code, then worked once it was
    corrected. The report must describe the engine, not the mistake."""
    _write(tmp_path / "a-paddle-first", "real",
           {"summary": [_summary("paddleocr", ok=0)], "runs": []})
    _write(tmp_path / "z-paddle-retry", "real",
           {"summary": [_summary("paddleocr", ok=4, pages=4)], "runs": []})

    combined = merge(tmp_path, "real")

    rows = [r for r in combined["summary"] if r["extractor"] == "paddleocr"]

    assert len(rows) == 1, "the engine appeared twice"
    assert rows[0]["ok"] == 4


def test_a_failure_never_overwrites_a_success(tmp_path):
    """Whichever order the directories are read in."""
    _write(tmp_path / "a-good", "real", {"summary": [_summary("easyocr", ok=2, pages=2)], "runs": []})
    _write(tmp_path / "z-bad", "real", {"summary": [_summary("easyocr", ok=0, pages=2)], "runs": []})

    rows = merge(tmp_path, "real")["summary"]

    assert [r["ok"] for r in rows if r["extractor"] == "easyocr"] == [2]


def test_an_engine_that_ran_is_no_longer_listed_as_skipped(tmp_path):
    """Otherwise the report says both that an engine could not run and what it
    scored, which is the contradiction the reason field exists to avoid."""
    _write(tmp_path / "a-first", "real",
           {"summary": [], "skipped": {"qari": "transformers/torch are not installed"}, "runs": []})
    _write(tmp_path / "z-later", "real", {"summary": [_summary("qari")], "runs": []})

    combined = merge(tmp_path, "real")

    assert "qari" not in combined["skipped"]
    assert any(r["extractor"] == "qari" for r in combined["summary"])


def test_extractions_for_one_page_are_gathered_across_engines(tmp_path):
    """The side-by-side sample depends on this: every engine's reading of the
    same page has to end up in the same run."""
    page = {"source": "book.pdf", "page": 55, "truth": None}

    _write(tmp_path / "tesseract", "real",
           {"summary": [_summary("tesseract")],
            "runs": [{**page, "extractions": [{"extractor": "tesseract", "text": "اليسار"}]}]})
    _write(tmp_path / "easyocr", "real",
           {"summary": [_summary("easyocr")],
            "runs": [{**page, "extractions": [{"extractor": "easyocr", "text": "اليسار حينئذ"}]}]})

    combined = merge(tmp_path, "real")

    assert len(combined["runs"]) == 1
    assert {e["extractor"] for e in combined["runs"][0]["extractions"]} == {"tesseract", "easyocr"}


def test_results_are_ordered_by_word_error_rate(tmp_path):
    """WER first, because on Arabic it is the number that predicts retrieval."""
    _write(tmp_path / "worse", "synthetic", {"summary": [_summary("worse", wer=0.5)], "runs": []})
    _write(tmp_path / "better", "synthetic", {"summary": [_summary("better", wer=0.1)], "runs": []})
    _write(tmp_path / "unscored", "synthetic", {"summary": [_summary("unscored")], "runs": []})

    order = [r["extractor"] for r in merge(tmp_path, "synthetic")["summary"]]

    assert order[:2] == ["better", "worse"]
    assert order[-1] == "unscored", "engines without a score sort last, not first"


def test_a_corrupt_result_file_is_skipped_not_fatal(tmp_path):
    """One truncated JSON — a run killed by a timeout — must not lose every
    other engine's results."""
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "real.json").write_text("{ this is not json")
    _write(tmp_path / "fine", "real", {"summary": [_summary("tesseract")], "runs": []})

    combined = merge(tmp_path, "real")

    assert [r["extractor"] for r in combined["summary"]] == ["tesseract"]


def test_a_later_run_cannot_re_skip_an_engine_that_already_scored(tmp_path):
    """Running `--only tesseract` reports every other engine as unavailable in
    that process. Without this, a run that happened afterwards re-added engines
    an earlier run had scored, and the report claimed both that an engine could
    not run and what it scored."""
    _write(tmp_path / "a-scored", "real", {"summary": [_summary("gemini")], "runs": []})
    _write(tmp_path / "z-only-tesseract", "real",
           {"summary": [_summary("tesseract")],
            "skipped": {"gemini": "GOOGLE_API_KEY is not set"}, "runs": []})

    combined = merge(tmp_path, "real")

    assert "gemini" not in combined["skipped"]
    assert any(r["extractor"] == "gemini" for r in combined["summary"])
