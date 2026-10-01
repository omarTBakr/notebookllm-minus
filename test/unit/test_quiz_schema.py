"""The quiz contract: the model names the right answer, code finds its position.

A real run stored 8 of 13 questions with answer_index 0 and most of the
"correct" options plainly wrong. The model now writes the correct answer as
text, apart from three wrong ones, and `as_item` shuffles them and works out
the index -- so the one thing these tests pin is that the index always points
at the answer the model called correct.
"""

import pytest
from pydantic import ValidationError

from data.models import QuizQuestion, QuizSet


def _question(**overrides):
    fields = {
        "question": "What does retrieval-augmented generation add to a language model?",
        "correct_answer": "Documents retrieved from an outside source",
        "wrong_answers": [
            "A larger set of trainable weights",
            "A second model that checks its grammar",
            "A cache of its own earlier answers",
        ],
        "chunk_order": 3,
    }
    fields.update(overrides)
    return QuizQuestion(**fields)


def test_the_index_points_at_the_correct_answer():
    item = _question().as_item()

    assert item["options"][item["answer_index"]] == "Documents retrieved from an outside source"
    assert sorted(item["options"]) == sorted(_question().options)
    assert item["chunk_order"] == 3


def test_the_correct_answer_does_not_always_land_first():
    """Correct-first is the unshuffled order; stored as-is it would be the
    same tell the old answer_index-0 bias was."""
    positions = {
        _question(question=f"Question number {n} about retrieval?").as_item()["answer_index"] for n in range(40)
    }

    assert len(positions) > 1


def test_the_same_question_always_shuffles_the_same_way():
    assert _question().as_item() == _question().as_item()


def test_a_wrong_answer_repeating_the_correct_one_is_rejected():
    with pytest.raises(ValidationError, match="repeats the correct answer"):
        _question(wrong_answers=["documents retrieved from an outside source.", "B", "C"])


def test_exactly_three_wrong_answers():
    with pytest.raises(ValidationError):
        _question(wrong_answers=["B", "C"])

    with pytest.raises(ValidationError):
        _question(wrong_answers=["B", "C", "D", "E"])


def test_duplicate_wrong_answers_are_rejected():
    with pytest.raises(ValidationError, match="all be different"):
        _question(wrong_answers=["B", "b", "C"])


def test_an_answer_that_is_a_question_is_rejected():
    with pytest.raises(ValidationError, match="is a question"):
        _question(correct_answer="Which of the following?")


def test_the_example_shows_no_answer_position():
    """The worked example is what a weak model copies. It used to carry
    answer_index 0, which is what the model then answered."""
    example = QuizSet.model_config["json_schema_extra"]["example"]["questions"][0]

    assert "answer_index" not in example
    assert set(example) == {"question", "correct_answer", "wrong_answers", "chunk_order"}
