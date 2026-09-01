"""Model ids carry where they live: "local/llama3.1:8b", "nvidia/meta/…"."""

import pytest

from utils import CLOUD, LOCAL, NVIDIA, backend_for, qualify, split_source


@pytest.mark.parametrize(
    "model_id, expected",
    [
        ("local/llama3.1:8b", (LOCAL, "llama3.1:8b")),
        ("cloud/gemma4:latest", (CLOUD, "gemma4:latest")),
        # Chats written before there were two hosts stored a bare tag.
        ("gemma4:e4b", (LOCAL, "gemma4:e4b")),
        # An Ollama tag may itself contain a slash, so only a *known* prefix
        # counts as a source.
        ("dimavz/whisper-tiny:latest", (LOCAL, "dimavz/whisper-tiny:latest")),
        ("local/dimavz/whisper-tiny:latest", (LOCAL, "dimavz/whisper-tiny:latest")),
        # A vendor prefix works the same way — and NVIDIA's own ids contain a
        # slash (the publisher), which only the *first* segment may claim.
        ("nvidia/meta/llama-3.2-11b-vision-instruct",
         (NVIDIA, "meta/llama-3.2-11b-vision-instruct")),
        ("nvidia/nvidia/nemotron-3-embed-1b", (NVIDIA, "nvidia/nemotron-3-embed-1b")),
        # A prefix with nothing after it is not a qualified id.
        ("cloud/", (LOCAL, "cloud/")),
        # Neither is a look-alike prefix.
        ("remote/x:1b", (LOCAL, "remote/x:1b")),
    ],
)
def test_split_source(model_id, expected):
    assert split_source(model_id) == expected


@pytest.mark.parametrize("source", [LOCAL, CLOUD, NVIDIA])
def test_qualify_round_trips(source):
    assert split_source(qualify(source, "llama3.1:8b")) == (source, "llama3.1:8b")


def test_qualify_survives_a_slash_in_the_tag():
    qualified = qualify(CLOUD, "dimavz/whisper-tiny:latest")
    assert split_source(qualified) == (CLOUD, "dimavz/whisper-tiny:latest")


# --- which provider a prefix selects ------------------------------------------


@pytest.mark.parametrize("source, backend", [
    # Both Ollama sources differ only in which machine answers.
    (LOCAL, "ollama"),
    (CLOUD, "ollama"),
    # A vendor prefix names the backend itself, which is what lets one chat
    # run on a local model and the next on a hosted one.
    (NVIDIA, "nvidia"),
])
def test_backend_for(source, backend):
    assert backend_for(source) == backend


# --- the .env defaults, spelled the one way ------------------------------------
#
# A vendor tag already begins with a publisher, so a raw .env value like
# "nvidia/nemotron-3-embed-1b" reads to split_source as source "nvidia" plus
# tag "nemotron-3-embed-1b" — one segment short of a model that exists. The
# picker showed the difference as "Missing"; the provider would have shown it
# as a 404.


@pytest.mark.parametrize("backend, model, expected", [
    ("ollama", "gemma4:e4b", "local/gemma4:e4b"),
    ("nvidia", "nvidia/nemotron-3-embed-1b", "nvidia/nvidia/nemotron-3-embed-1b"),
    ("nvidia", "meta/llama-3.2-11b-vision-instruct",
     "nvidia/meta/llama-3.2-11b-vision-instruct"),
])
def test_a_default_is_qualified_by_its_backend(settings, backend, model, expected):
    from utils import default_chat_model

    configured = settings.model_copy(
        update={"GENERATION_BACKEND": backend, "GENERATION_MODEL_ID": model}
    )

    assert default_chat_model(configured) == expected


def test_a_qualified_default_survives_split_source(settings):
    """The whole point: what comes back out is the tag the vendor knows."""
    from utils import default_embedding_model

    configured = settings.model_copy(
        update={
            "EMBEDDING_BACKEND": "nvidia",
            "EMBEDDING_MODEL_ID": "nvidia/nemotron-3-embed-1b",
        }
    )

    source, tag = split_source(default_embedding_model(configured))

    assert (source, tag) == (NVIDIA, "nvidia/nemotron-3-embed-1b")


def test_source_of_is_the_inverse_of_backend_for(settings):
    from utils import backend_for, source_of

    for source in (LOCAL, NVIDIA):
        assert source_of(backend_for(source)) == source
