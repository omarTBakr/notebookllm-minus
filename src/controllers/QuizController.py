"""Quizzes: four options, one right, traceable to the passage it came from.

Nothing here beyond the three declarations, because the hard parts live where
they are enforceable rather than merely requested. Distractor quality is argued
in the prompt (`quiz_prompt`), and the rules that can actually be *checked* --
four distinct options, no option that is itself a question -- are validators on
QuizQuestion, so a model that breaks them is asked again instead of the reader
being shown the result.
"""

from enums import ArtifactKind
from models.db_schema import QuizSet

from .ArtifactController import ArtifactController


class QuizController(ArtifactController):
    schema = QuizSet
    prompt_key = "quiz_prompt"
    field = "questions"
    kind = ArtifactKind.QUIZ
