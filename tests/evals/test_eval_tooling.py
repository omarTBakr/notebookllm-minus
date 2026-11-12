"""The pure parts of evals/: stream parsing, question files, thresholds, means.

The tools themselves run against a live stack and are not part of this suite.
These are the pieces that would otherwise fail silently: a stream parsed
wrongly reads as an empty answer, and a threshold on a misspelt metric would
never fire.
"""

import json
import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(EVALS))

import notebook_client  # noqa: E402
import ragas_eval  # noqa: E402


def frame(payload: dict) -> str:
    return f"data: {json.dumps(payload)}"


def test_only_data_frames_are_events_and_bytes_are_accepted():
    lines = [
        frame({"type": "meta", "grounded": True}),
        "",
        ": keep-alive comment",
        frame({"type": "delta", "text": "مرحبا"}).encode(),
        "data:",
        frame({"type": "done"}),
    ]

    events = list(notebook_client.parse_events(lines))

    assert [event["type"] for event in events] == ["meta", "delta", "done"]
    assert events[1]["text"] == "مرحبا"


def test_every_metric_says_what_it_needs():
    assert set(ragas_eval.METRICS) == {
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
        "factual_correctness",
    }
    # The two that can be scored without a reference answer.
    assert [name for name, (needs_reference, _) in ragas_eval.METRICS.items() if not needs_reference] == [
        "faithfulness",
        "answer_relevancy",
    ]


def test_a_mean_ignores_what_could_not_be_scored():
    assert ragas_eval.mean([1.0, None, 0.5]) == 0.75
    assert ragas_eval.mean([None, None]) is None
    assert ragas_eval.mean([]) is None


def test_a_threshold_on_a_metric_that_is_not_run_is_refused():
    assert ragas_eval.thresholds(["faithfulness=0.8"], ["faithfulness"]) == {"faithfulness": 0.8}

    with pytest.raises(SystemExit):
        ragas_eval.thresholds(["faithfullness=0.8"], ["faithfulness"])


def test_questions_are_read_in_order_and_limited(tmp_path):
    (tmp_path / "questions.jsonl").write_text(
        '{"question": "one?", "reference": "1"}\n\n{"question": "two?"}\n{"question": "three?"}\n'
    )

    assert [row["question"] for row in ragas_eval.read_questions(tmp_path, None)] == ["one?", "two?", "three?"]
    assert [row["question"] for row in ragas_eval.read_questions(tmp_path, 2)] == ["one?", "two?"]


def test_a_line_without_a_question_stops_the_run(tmp_path):
    (tmp_path / "questions.jsonl").write_text('{"reference": "an answer to nothing"}\n')

    with pytest.raises(SystemExit):
        ragas_eval.read_questions(tmp_path, None)


def test_the_sample_dataset_is_well_formed():
    sample = EVALS / "datasets" / "sample"
    rows = ragas_eval.read_questions(sample, None)

    assert len(rows) >= 10
    assert all(row.get("reference") for row in rows)
    assert any((sample / "documents").iterdir())
