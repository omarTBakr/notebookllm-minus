"""The locale prompt files: English and Arabic stay in step, and every prompt
that writes text for the user carries the full language rule.

Read straight from the modules, the way scripts/langfuse_seed_prompts.py does,
so this checks the text that is seeded as a version and served as the fallback.
"""

import importlib
import string

import pytest

LANGS = ("en", "ar")
GROUPS = ("rag", "chat", "studio")

# The prompts whose output a reader sees, or that feed one (the planner's
# queries decide what is found; the summaries become the cards).
ANSWERING = {
    "rag": ("system_prompt", "subanswer_prompt", "synthesis_prompt"),
    "chat": ("system_prompt",),
}
GENERATING = {
    "studio": (
        "summarise_prompt",
        "flashcards_prompt",
        "quiz_prompt",
        "mindmap_prompt",
        "mindmap_outline_prompt",
    ),
}


def module(lang: str, group: str):
    return importlib.import_module(f"application.prompts.locales.{lang}.{group}")


def public_prompts(lang: str, group: str) -> dict[str, str]:
    return {k: v for k, v in vars(module(lang, group)).items() if not k.startswith("_") and isinstance(v, str)}


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("group", GROUPS)
def test_both_languages_have_the_same_prompts(group):
    """A key missing in one language is an AttributeError the first time a
    notebook in that language reaches it."""
    assert set(public_prompts("en", group)) == set(public_prompts("ar", group))


@pytest.mark.parametrize("group", GROUPS)
def test_both_languages_take_the_same_placeholders(group):
    """The code formats both with the same variables; a placeholder renamed in
    one translation is a KeyError in that language only."""
    en, ar = public_prompts("en", group), public_prompts("ar", group)
    for key in en:
        assert placeholders(en[key]) == placeholders(ar[key]), f"{group}.{key}"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("group", GROUPS)
def test_every_prompt_formats(lang, group):
    for key, text in public_prompts(lang, group).items():
        text.format(**{name: "x" for name in placeholders(text)})


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("group", sorted(ANSWERING))
def test_every_answering_prompt_carries_the_whole_answer_language_rule(lang, group):
    """One line ("answer in the user's language") was not enough: an Arabic
    question about English sources came back in English. Every prompt that
    writes an answer carries the full rule, word for word."""
    mod = module(lang, group)
    for key in ANSWERING[group]:
        text = getattr(mod, key)
        for line in mod._ANSWER_LANGUAGE:
            assert line in text, f"{lang}/{group}.{key} is missing: {line!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_studio_prompt_carries_the_whole_output_language_rule(lang):
    """Studio writes in the notebook's language; the rule used to be worded
    four different ways across four prompts."""
    mod = module(lang, "studio")
    for key in GENERATING["studio"]:
        text = getattr(mod, key)
        for line in mod._OUTPUT_LANGUAGE:
            assert line in text, f"{lang}/studio.{key} is missing: {line!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_the_planner_keeps_the_message_language(lang):
    """The planner translated an Arabic question into English queries, and the
    Arabic passages that answered it were never searched for. The rule names
    both rewritten fields."""
    text = module(lang, "rag").plan_prompt
    assert "standalone_query" in text and "sub_query" in text
    marker = {"en": "Never translate them", "ar": "لا تترجمها أبدًا"}[lang]
    assert marker in text


@pytest.mark.parametrize("lang", LANGS)
def test_the_language_rule_is_not_seeded_as_a_prompt_of_its_own(lang):
    """The shared blocks are private: part of other prompts, not separate
    Langfuse prompts that could be edited out of step with them."""
    from application.prompts.template_parser import iter_local_prompts

    seeded = {(lg, group, key) for lg, group, key, _ in iter_local_prompts()}
    for group in GROUPS:
        for key in ("_ANSWER_LANGUAGE", "_OUTPUT_LANGUAGE", "_SOURCE_IS_DATA"):
            assert (lang, group, key) not in seeded
