"""A plain HTTP client for a running NotebookLLM-minus, used by the evaluation.

It does what a browser does and nothing else: sign in, make a notebook, attach
a file, wait for the workers, ask a question and read the event stream. No
application code is imported, so what is measured is the deployed system.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class NotebookError(RuntimeError):
    """The system answered, and the answer was no."""


@dataclass
class Answer:
    question: str
    text: str = ""
    grounded: bool = False
    citations: list[dict] = field(default_factory=list)
    #: Set when the stream ended in an ``error`` event, or a guardrail refused.
    error: str | None = None
    refused: bool = False
    #: Seconds to the first piece of answer text, and to the end of the stream.
    first_token_s: float | None = None
    total_s: float = 0.0


def parse_events(lines):
    """The JSON payload of each ``data:`` frame in a server-sent event stream."""
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                yield json.loads(payload)


class NotebookClient:
    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=True)
        self.user_id: str | None = None

    def close(self) -> None:
        self.http.close()

    def _check(self, response: httpx.Response, what: str) -> dict:
        if response.status_code >= 400:
            raise NotebookError(f"{what} failed: HTTP {response.status_code} {response.text[:300]}")
        return response.json()

    # --- account ------------------------------------------------------------------

    def sign_in(self, email: str, password: str, create: bool = True) -> str:
        """Log in; with *create*, sign the account up first if it does not exist."""
        response = self.http.post("/auth/login", json={"email": email, "password": password})

        if response.status_code == 401 and create:
            signup = self.http.post(
                "/auth/signup", json={"email": email, "password": password, "display_name": "Evaluation"}
            )
            # 409: the account exists, so the 401 above was a wrong password.
            if signup.status_code == 409:
                raise NotebookError("The account exists and the password is wrong")
            response = signup

        self.user_id = self._check(response, "Signing in")["user"]["user_id"]
        return self.user_id

    # --- notebooks and sources ---------------------------------------------------------

    def create_notebook(self, title: str, lang: str = "en") -> str:
        response = self.http.post(f"/chat/users/{self.user_id}/chats", json={"title": title, "lang": lang})
        return self._check(response, "Creating a notebook")["chat_id"]

    def upload(self, chat_id: str, path: Path) -> dict | None:
        """Attach a file. None when the notebook already holds these exact bytes."""
        mime = MIME.get(path.suffix.lower(), "application/octet-stream")
        with path.open("rb") as handle:
            response = self.http.post(f"/chat/chats/{chat_id}/documents", files={"file": (path.name, handle, mime)})

        if response.status_code == 409:
            return None
        return self._check(response, f"Uploading {path.name}")

    def wait_until_indexed(self, chat_id: str, timeout: float = 1800.0, poll: float = 2.0) -> float:
        """Block until no ingestion is running and the notebook answers from its sources.

        Returns the seconds waited. A failed task raises with the task's own error.
        """
        started = time.monotonic()

        while time.monotonic() - started < timeout:
            progress = self._check(self.http.get(f"/chat/chats/{chat_id}/indexing"), "Reading ingest progress")

            if progress.get("status") == "FAILURE" or progress.get("error"):
                raise NotebookError(f"Ingestion failed: {progress.get('error')}")

            if not progress.get("active"):
                chat = self._check(self.http.get(f"/chat/chats/{chat_id}"), "Reading the notebook")
                if chat.get("grounded"):
                    return time.monotonic() - started

            time.sleep(poll)

        raise NotebookError(f"Notebook {chat_id} was not indexed within {timeout:.0f}s")

    # --- asking -----------------------------------------------------------------------

    def ask(self, chat_id: str, question: str, top_k: int | None = None) -> Answer:
        """Ask one question and read the stream to its end."""
        body: dict = {"text": question}
        if top_k:
            body["top_k"] = top_k

        answer = Answer(question=question)
        pieces: list[str] = []
        started = time.monotonic()

        with self.http.stream("POST", f"/chat/chats/{chat_id}/message", json=body) as response:
            if response.status_code >= 400:
                response.read()
                raise NotebookError(f"Asking failed: HTTP {response.status_code} {response.text[:300]}")

            for event in parse_events(response.iter_lines()):
                kind = event.get("type")

                if kind == "meta":
                    answer.grounded = bool(event.get("grounded"))
                    answer.citations = event.get("citations") or []
                elif kind == "delta":
                    if answer.first_token_s is None:
                        answer.first_token_s = time.monotonic() - started
                    pieces.append(event.get("text", ""))
                elif kind == "guardrail":
                    answer.refused = True
                    pieces.append(event.get("message", ""))
                elif kind == "error":
                    answer.error = event.get("detail") or "error"

        answer.text = "".join(pieces).strip()
        answer.total_s = time.monotonic() - started
        return answer

    def context_text(self, chat_id: str, citation: dict) -> str:
        """The passage a citation points at.

        A citation carries where the passage is, not what it says, so the text
        is read back from the chunk the way the viewer does when it is clicked.
        """
        asset_id, order = citation.get("asset_id"), citation.get("chunk_order")
        if asset_id is None or order is None:
            return ""

        response = self.http.get(f"/chat/chats/{chat_id}/assets/{asset_id}/chunks/{order}/locate")
        if response.status_code >= 400:
            return ""
        return (response.json().get("text") or "").strip()
