"""Quizzes: four options, one right, traceable to the passage it came from.

Nothing here beyond the declarations, because the hard parts live where they
are enforceable rather than merely requested. Distractor quality is argued in
the prompt (`quiz_prompt`), and the rules that can actually be *checked* --
four distinct options, no option that is itself a question -- are validators on
QuizQuestion, so a model that breaks them is asked again instead of the reader
being shown the result.

The model never says which position is right. It writes the correct answer
and three wrong ones as text; `QuizQuestion.as_item` shuffles them and finds
the index. See QuizQuestion for the run that made this necessary.
"""

from data.models import QuizSet
from shared.enums import ArtifactKind

from .ArtifactService import ArtifactService


class QuizService(ArtifactService):
    schema = QuizSet
    prompt_key = "quiz_prompt"
    field = "questions"
    kind = ArtifactKind.QUIZ

    def as_item(self, item) -> dict:
        return item.as_item()
