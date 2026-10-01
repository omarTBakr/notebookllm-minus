"""One multiple-choice question, and the set a batch of them arrives in.

The validators here are the point. Whether an answer is *right* needs a model
that can read the passage; whether a question is well formed does not, and the
things that are decidable in code are decided here so a model that breaks them
is asked again rather than the reader being shown the result.
"""

import random

from pydantic import BaseModel, Field, model_validator

# The list field on each set below is REQUIRED -- `Field(...)`, not
# `default_factory=list`. The difference is not stylistic and it cost a batch.
#
# With a default, *any* JSON object validates as an empty set. A model that
# echoes the schema back at you instead of filling it in -- which is exactly
# what llama-3.2-11b did with a ten-summary batch -- produces a clean parse of
# zero items, so `generate_structured` calls it a success and its repair loop
# never fires. The batch is dropped in silence and the deck is quietly half
# the size it should be.
#
# Required means the key has to be there, which is proof the model answered
# the question rather than restating it; a missing key is a ValidationError
# and gets retried with the error fed back. An *empty* list stays valid,
# because "these ten summaries support no good card" is a real answer the
# prompts explicitly invite.


def _normalise(text: str) -> str:
    """Casefolded, stripped of punctuation and repeated spaces.

    So that "X has experience with which of the following?" and "X has
    experience with which of the following" compare equal -- a model restating
    the question rarely restates it character for character.
    """
    kept = "".join(char if char.isalnum() or char.isspace() else " " for char in text)

    return " ".join(kept.split()).casefold()


class QuizQuestion(BaseModel):
    """One multiple-choice question, as the model is asked to write it.

    The model names the right answer by its *text*, in `correct_answer`, and
    never by position. Asking for `answer_index` was the failure: in a real run
    8 of 13 questions came back with index 0 -- the value in the worked example
    -- and most "correct" options were plainly wrong ("RAG is used for data
    compression"). A small model writing four options does not keep track of
    which slot it put the true one in, but it does know which sentence it
    wrote as the answer. Positions are decided here, in code, by `as_item`.

    Three wrong answers, fixed. A variable count would let a model return one
    option and call it a question, and calibrating difficulty across sets is
    easier when the guess rate is constant.
    """

    question: str = Field(..., min_length=1, max_length=500)
    correct_answer: str = Field(..., min_length=1, max_length=500)
    wrong_answers: list[str] = Field(..., min_length=3, max_length=3)
    chunk_order: int = Field(..., ge=0)

    @property
    def options(self) -> list[str]:
        """All four, correct first -- the unshuffled order, never shown."""
        return [self.correct_answer, *self.wrong_answers]

    @model_validator(mode="after")
    def _options_must_be_answers(self) -> "QuizQuestion":
        """Reject an option that is the question rather than an answer to it.

        Seen in a real run: "What technical skills does X have experience
        with?" offering "X has experience with which of the following?" as an
        option -- and marking it correct, while the option that actually listed
        the skills was marked wrong. An option that is itself a question cannot
        be an answer to one, and a model that pads the list this way is padding
        rather than reasoning.

        Caught structurally, on the same principle as the duplicate check: the
        answer being *wrong* needs a model that can read, but "this option is a
        restatement of the prompt" is decidable here, so the model is asked
        again instead of the reader being shown it.
        """
        asked = _normalise(self.question)

        for option in self.options:
            if not option.strip():
                raise ValueError("every answer must have text; one was empty")

            if option.rstrip().endswith("?"):
                raise ValueError(f"option {option!r} is a question, not an answer to one")

            if _normalise(option) == asked:
                raise ValueError(f"option {option!r} restates the question instead of answering it")

        return self

    @model_validator(mode="after")
    def _options_must_differ(self) -> "QuizQuestion":
        """Reject a question offering the same option twice.

        Seen in a real run: four options where two were the identical string,
        which makes the question unanswerable -- if the repeated option is the
        right one, two indexes are correct and only one is accepted; if it is
        wrong, the question is really a choice of three dressed as four.

        The wrong-answer-equals-right-answer case gets its own message, because
        it is a different mistake -- the model contradicting itself -- and the
        retry prompt is only as useful as the error it carries.
        """
        right = _normalise(self.correct_answer)

        if any(_normalise(wrong) == right for wrong in self.wrong_answers):
            raise ValueError("a wrong answer repeats the correct answer; each wrong answer must be false")

        if len({_normalise(option) for option in self.options}) != len(self.options):
            raise ValueError("the four options must all be different; two were the same")

        return self

    def as_item(self) -> dict:
        """The stored question: four shuffled options and the index of the right one.

        The index is *found*, not asked for -- the correct answer is wherever
        the shuffle put it. Seeded by the question text so a given question
        always lands the same way: a re-render, or a test, sees the same order.
        The stored shape is unchanged, so the browser marks answers as before.
        """
        options = self.options
        random.Random(self.question).shuffle(options)

        return {
            "question": self.question,
            "options": options,
            "answer_index": options.index(self.correct_answer),
            "chunk_order": self.chunk_order,
        }


class QuizSet(BaseModel):
    questions: list[QuizQuestion] = Field(...)

    model_config = {
        "json_schema_extra": {
            "example": {
                "questions": [
                    {
                        "question": "EXAMPLE ONLY - your question here",
                        "correct_answer": "EXAMPLE ONLY - the one true answer, as the summary states it",
                        "wrong_answers": [
                            "EXAMPLE ONLY - a plausible but false answer",
                            "EXAMPLE ONLY - another plausible but false answer",
                            "EXAMPLE ONLY - a third plausible but false answer",
                        ],
                        "chunk_order": 1,
                    }
                ]
            }
        }
    }
