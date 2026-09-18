"""One multiple-choice question, and the set a batch of them arrives in.

The validators here are the point. Whether an answer is *right* needs a model
that can read the passage; whether a question is well formed does not, and the
things that are decidable in code are decided here so a model that breaks them
is asked again rather than the reader being shown the result.
"""

from pydantic import BaseModel, Field, field_validator, model_validator

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
    """One multiple-choice question.

    Four options, fixed. A variable count would let a model return one option
    and call it a question, and calibrating difficulty across sets is easier
    when the guess rate is constant.
    """

    question: str = Field(..., min_length=1, max_length=500)
    options: list[str] = Field(..., min_length=4, max_length=4)

    # Index into `options`, not the answer text: comparing a user's choice by
    # string would break on any whitespace the model varies between runs.
    answer_index: int = Field(..., ge=0, le=3)

    chunk_order: int = Field(..., ge=0)

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
            if option.rstrip().endswith("?"):
                raise ValueError(f"option {option!r} is a question, not an answer to one")

            if _normalise(option) == asked:
                raise ValueError(f"option {option!r} restates the question instead of answering it")

        return self

    @field_validator("options")
    @classmethod
    def _options_must_differ(cls, options: list[str]) -> list[str]:
        """Reject a question offering the same option twice.

        Seen in a real run: four options where two were the identical string,
        which makes the question unanswerable -- if the repeated option is the
        right one, two indexes are correct and only one is accepted; if it is
        wrong, the question is really a choice of three dressed as four.

        Unlike the answer being *wrong*, which needs a model that can read,
        this is checkable here, so it is. A question that fails goes back to
        the model with the error attached rather than to the reader.
        """
        seen = {option.strip().casefold() for option in options}

        if len(seen) != len(options):
            raise ValueError("the four options must all be different; two were the same")

        return options


class QuizSet(BaseModel):
    questions: list[QuizQuestion] = Field(...)

    model_config = {
        "json_schema_extra": {
            "example": {
                "questions": [
                    {
                        "question": "EXAMPLE ONLY - your question here",
                        "options": [
                            "EXAMPLE ONLY - option A",
                            "EXAMPLE ONLY - option B",
                            "EXAMPLE ONLY - option C",
                            "EXAMPLE ONLY - option D",
                        ],
                        "answer_index": 0,
                        "chunk_order": 1,
                    }
                ]
            }
        }
    }
