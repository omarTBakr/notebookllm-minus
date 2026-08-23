"""Model ids carry the host they live on: "local/llama3.1:8b"."""

import pytest

from utils import CLOUD, LOCAL, qualify, split_source


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
        # A prefix with nothing after it is not a qualified id.
        ("cloud/", (LOCAL, "cloud/")),
        # Neither is a look-alike prefix.
        ("remote/x:1b", (LOCAL, "remote/x:1b")),
    ],
)
def test_split_source(model_id, expected):
    assert split_source(model_id) == expected


@pytest.mark.parametrize("source", [LOCAL, CLOUD])
def test_qualify_round_trips(source):
    assert split_source(qualify(source, "llama3.1:8b")) == (source, "llama3.1:8b")


def test_qualify_survives_a_slash_in_the_tag():
    qualified = qualify(CLOUD, "dimavz/whisper-tiny:latest")
    assert split_source(qualified) == (CLOUD, "dimavz/whisper-tiny:latest")
