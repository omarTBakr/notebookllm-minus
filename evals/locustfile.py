"""Load test: many signed-in users working in notebooks at once.

Run from the repository root, against a running stack:

    uv run --project evals locust -f evals/locustfile.py --host http://localhost:8081

or headless, for a number to compare between runs:

    uv run --project evals locust -f evals/locustfile.py --host http://localhost:8081 \\
        --headless --users 20 --spawn-rate 2 --run-time 3m --tags browse search \\
        --csv evals/reports/load --html evals/reports/load.html

Tasks are tagged so the cheap paths and the expensive ones can be loaded apart:

    browse   listings and reads: the database and the auth layer
    search   retrieval without a chat model: the embedder and the vector store
    ask      a full streamed answer: planner, retrieval, rerank, generation
    ingest   an upload: the Celery ingest pipeline

`ask` holds a connection open for a whole answer and is bounded by the model,
not by the app, so start with `--tags browse search` to find what the API and
the stores can take, then add `ask` with a few users.

Every user signs in with the account in evals/.env. With LOAD_CHAT_ID set they
all read and ask in that one notebook; without it each user makes its own and
uploads the sample document, which loads the ingest pipeline at the start.
"""

from __future__ import annotations

import os
import random
import time
import uuid
from pathlib import Path

from locust import HttpUser, between, events, tag, task
from locust.exception import StopUser
from notebook_client import parse_events

HERE = Path(__file__).parent
SAMPLE = HERE / "datasets" / "sample"


def _load_env(path: Path) -> None:
    """KEY=VALUE lines from evals/.env, without overriding the real environment."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(HERE / ".env")

QUESTIONS = [
    "Who founded the observatory?",
    "When is the observatory open to visitors?",
    "How much is a night viewing ticket?",
    "What are the rules about light after sunset?",
    "When are the domes closed?",
    "What does the summer school do?",
]

#: How long a user waits for its own upload to be indexed before giving up on it.
INDEX_TIMEOUT_S = 900


class NotebookUser(HttpUser):
    """One person with the app open: mostly reading, sometimes asking."""

    wait_time = between(1, 5)

    def on_start(self) -> None:
        email, password = os.environ.get("EVAL_EMAIL"), os.environ.get("EVAL_PASSWORD")
        if not email or not password:
            raise StopUser("Set EVAL_EMAIL and EVAL_PASSWORD in evals/.env")

        with self.client.post(
            "/auth/login", json={"email": email, "password": password}, name="POST /auth/login", catch_response=True
        ) as response:
            if response.status_code != 200:
                response.failure(f"login refused: HTTP {response.status_code}")
                raise StopUser("could not sign in")
            self.user_id = response.json()["user"]["user_id"]

        self.chat_id = os.environ.get("LOAD_CHAT_ID") or self._own_notebook()

    def _own_notebook(self) -> str:
        """A notebook of this user's own, holding the sample document."""
        created = self.client.post(
            f"/chat/users/{self.user_id}/chats",
            json={"title": f"load {uuid.uuid4().hex[:8]}", "lang": "en"},
            name="POST /chat/users/[id]/chats",
        )
        chat_id = created.json()["chat_id"]

        document = SAMPLE / "documents" / "halden_handbook.md"
        # A line of its own makes the bytes unique, or the notebook-level
        # dedupe would be the only thing this measures.
        body = document.read_bytes() + f"\n\nLoad test copy {uuid.uuid4().hex}.\n".encode()
        self.client.post(
            f"/chat/chats/{chat_id}/documents",
            files={"file": (document.name, body, "text/markdown")},
            name="POST /chat/chats/[id]/documents",
        )

        started = time.monotonic()
        while time.monotonic() - started < INDEX_TIMEOUT_S:
            chat = self.client.get(f"/chat/chats/{chat_id}", name="GET /chat/chats/[id] (waiting for index)").json()
            if chat.get("grounded"):
                break
            time.sleep(2)

        events.request.fire(
            request_type="INGEST",
            name="upload until answerable",
            response_time=(time.monotonic() - started) * 1000,
            response_length=len(body),
            exception=None if time.monotonic() - started < INDEX_TIMEOUT_S else TimeoutError("not indexed in time"),
            context={},
        )
        return chat_id

    # --- reading: cheap, frequent --------------------------------------------------

    @tag("browse")
    @task(6)
    def open_notebook(self) -> None:
        self.client.get(f"/chat/chats/{self.chat_id}", name="GET /chat/chats/[id]")
        self.client.get(f"/chat/chats/{self.chat_id}/assets", name="GET /chat/chats/[id]/assets")
        self.client.get(f"/chat/chats/{self.chat_id}/messages", name="GET /chat/chats/[id]/messages")

    @tag("browse")
    @task(3)
    def list_notebooks(self) -> None:
        self.client.get(f"/chat/users/{self.user_id}/chats", name="GET /chat/users/[id]/chats")
        self.client.get("/auth/me", name="GET /auth/me")

    @tag("browse")
    @task(2)
    def poll_indexing(self) -> None:
        # What the sources panel does several times a second during an upload.
        self.client.get(f"/chat/chats/{self.chat_id}/indexing", name="GET /chat/chats/[id]/indexing")

    # --- retrieval without a chat model ---------------------------------------------

    @tag("search")
    @task(4)
    def search(self) -> None:
        self.client.post(
            f"/nlp/index/search/{self.chat_id}",
            json={"text": random.choice(QUESTIONS), "limit": 5},
            name="POST /nlp/index/search/[id]",
        )

    # --- a full answer: expensive, rare ----------------------------------------------

    @tag("ask")
    @task(1)
    def ask(self) -> None:
        """Ask a question and read the whole stream.

        Reported as three entries. The POST itself is timed by Locust when
        the response *starts*, which for a stream is only the headers, so the
        two numbers that matter are fired by hand: "first token", which is
        what a reader feels, and "complete", the whole answer.
        """
        started = time.monotonic()
        first_token = None
        failure = None

        with self.client.post(
            f"/chat/chats/{self.chat_id}/message",
            json={"text": random.choice(QUESTIONS)},
            name="POST /chat/chats/[id]/message (stream opened)",
            stream=True,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return

            text = 0
            for event in parse_events(response.iter_lines()):
                if event.get("type") == "delta":
                    if first_token is None:
                        first_token = time.monotonic() - started
                    text += len(event.get("text", ""))
                elif event.get("type") == "error":
                    failure = event.get("detail") or "error event"

            if failure:
                response.failure(f"stream ended in an error: {failure}")
            elif not text:
                response.failure("the stream carried no answer text")
            else:
                response.success()

        if first_token is not None:
            events.request.fire(
                request_type="SSE",
                name="answer: first token",
                response_time=first_token * 1000,
                response_length=0,
                exception=None,
                context={},
            )
            events.request.fire(
                request_type="SSE",
                name="answer: complete",
                response_time=(time.monotonic() - started) * 1000,
                response_length=text,
                exception=None,
                context={},
            )

    # --- ingestion -------------------------------------------------------------------

    @tag("ingest")
    @task(1)
    def upload_note(self) -> None:
        """Attach a small unique note: 202 means stored and queued, not indexed."""
        note = f"Load test note {uuid.uuid4().hex}.\n" + ("The ridge was quiet that night. " * 40)
        self.client.post(
            f"/chat/chats/{self.chat_id}/documents",
            files={"file": (f"note-{uuid.uuid4().hex[:8]}.txt", note.encode(), "text/plain")},
            name="POST /chat/chats/[id]/documents",
        )
