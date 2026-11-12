"""End-to-end answer evaluation with Ragas, against a running stack.

    uv run --project evals python evals/ragas_eval.py                 # the sample dataset
    uv run --project evals python evals/ragas_eval.py --dataset evals/datasets/mine --min faithfulness=0.8

What it does, in order:

1. signs in (the account in evals/.env; signed up on first use);
2. makes a notebook and uploads every file under ``<dataset>/documents/``;
3. waits for the workers to finish chunking and indexing;
4. asks each question in ``<dataset>/questions.jsonl`` through the same
   streaming endpoint the browser uses;
5. reads back the passages each answer cited;
6. has a judge model score every (question, answer, passages, reference) with
   Ragas, and writes the scores to ``evals/reports/``.

Nothing is imported from the application, so the numbers describe the deployed
system: its chunking, embedder, reranker, planner, prompts and answer model.

A dataset is a folder:

    documents/          the sources to upload (pdf, txt, md, csv, xlsx)
    questions.jsonl     one {"question": ..., "reference": ...} per line

``reference`` is the answer a person would accept. Metrics that need one are
skipped for a line without it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from notebook_client import Answer, NotebookClient, NotebookError

HERE = Path(__file__).parent

#: name -> (needs a reference answer, needs embeddings)
METRICS = {
    "faithfulness": (False, False),
    "answer_relevancy": (False, True),
    "context_precision": (True, False),
    "context_recall": (True, False),
    "factual_correctness": (True, False),
}


def load_env(path: Path) -> None:
    """KEY=VALUE lines from evals/.env, without overriding the real environment."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=env("EVAL_BASE_URL", "http://localhost:8081"))
    parser.add_argument("--dataset", type=Path, default=HERE / "datasets" / "sample")
    parser.add_argument("--notebook", help="an existing notebook id: skip creating one and uploading")
    parser.add_argument("--lang", default="en", help="the notebook's language (en or ar)")
    parser.add_argument("--limit", type=int, help="ask only the first N questions")
    parser.add_argument("--top-k", type=int, help="passages per answer; the app's default when omitted")
    parser.add_argument("--metrics", default=",".join(METRICS), help=f"comma-separated, from: {', '.join(METRICS)}")
    parser.add_argument(
        "--min",
        action="append",
        default=[],
        metavar="METRIC=SCORE",
        help="fail (exit 1) when a metric's mean is below this; repeatable",
    )
    parser.add_argument("--judge-backend", choices=["ollama", "openai"], default=env("RAGAS_JUDGE_BACKEND", "ollama"))
    parser.add_argument("--judge-model", default=env("RAGAS_JUDGE_MODEL", "llama3.1:8b"))
    parser.add_argument("--embed-model", default=env("RAGAS_EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--judge-base-url", default=env("RAGAS_JUDGE_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--judge-timeout", type=int, default=300, help="seconds one judge call may take")
    parser.add_argument(
        "--judge-workers", type=int, default=2, help="judge calls in flight; keep low for a local model"
    )
    parser.add_argument("--index-timeout", type=int, default=1800)
    parser.add_argument("--out", type=Path, default=HERE / "reports")
    parser.add_argument("--answers-only", action="store_true", help="collect answers and stop before judging")
    return parser.parse_args()


def read_questions(dataset: Path, limit: int | None) -> list[dict]:
    path = dataset / "questions.jsonl"
    if not path.is_file():
        sys.exit(f"{path} does not exist")

    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for number, row in enumerate(rows, start=1):
        if not (row.get("question") or "").strip():
            sys.exit(f"{path}: line {number} has no question")

    return rows[:limit] if limit else rows


def thresholds(pairs: list[str], chosen: list[str]) -> dict[str, float]:
    out = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        if name not in chosen:
            sys.exit(f"--min {pair}: {name!r} is not among the metrics being run ({', '.join(chosen)})")
        out[name] = float(value)
    return out


# --- 1-5: the system under test ----------------------------------------------------------


def prepare_notebook(client: NotebookClient, args: argparse.Namespace) -> str:
    if args.notebook:
        return args.notebook

    documents = sorted(p for p in (args.dataset / "documents").glob("*") if p.is_file())
    if not documents:
        sys.exit(f"{args.dataset / 'documents'} holds no files to upload")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    chat_id = client.create_notebook(f"ragas {args.dataset.name} {stamp}", args.lang)
    print(f"notebook {chat_id}")

    for document in documents:
        client.upload(chat_id, document)
        print(f"  uploaded {document.name}")

    waited = client.wait_until_indexed(chat_id, timeout=args.index_timeout)
    print(f"  indexed in {waited:.0f}s")
    return chat_id


def collect(client: NotebookClient, chat_id: str, questions: list[dict], top_k: int | None) -> list[dict]:
    """Ask every question; one record each, with the passages its answer cited."""
    records = []

    for number, row in enumerate(questions, start=1):
        try:
            answer = client.ask(chat_id, row["question"], top_k)
        except NotebookError as exc:
            answer = Answer(question=row["question"], error=str(exc))

        contexts = [text for text in (client.context_text(chat_id, c) for c in answer.citations) if text]

        records.append(
            {
                "question": row["question"],
                "reference": (row.get("reference") or "").strip() or None,
                "answer": answer.text,
                "contexts": contexts,
                "citations": len(answer.citations),
                "grounded": answer.grounded,
                "refused": answer.refused,
                "error": answer.error,
                "first_token_s": round(answer.first_token_s, 2) if answer.first_token_s is not None else None,
                "total_s": round(answer.total_s, 2),
            }
        )

        status = "error" if answer.error else "refused" if answer.refused else f"{len(contexts)} passages"
        print(f"  [{number}/{len(questions)}] {answer.total_s:5.1f}s  {status:12}  {row['question'][:70]}")

    return records


# --- 6: the judge ------------------------------------------------------------------------


def build_judge(args: argparse.Namespace):
    """The judge chat model and embedder, wrapped for Ragas."""
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    if args.judge_backend == "ollama":
        from langchain_ollama import ChatOllama, OllamaEmbeddings

        # temperature 0: a judge should give the same verdict twice. A wide
        # context because a judge prompt carries every cited passage.
        chat = ChatOllama(model=args.judge_model, base_url=args.judge_base_url, temperature=0, num_ctx=8192)
        embedder = OllamaEmbeddings(model=args.embed_model, base_url=args.judge_base_url)
    else:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        key = os.environ.get("RAGAS_JUDGE_API_KEY")
        if not key:
            sys.exit("--judge-backend openai needs RAGAS_JUDGE_API_KEY in the environment or evals/.env")

        chat = ChatOpenAI(model=args.judge_model, base_url=args.judge_base_url, api_key=key, temperature=0)
        embedder = OpenAIEmbeddings(model=args.embed_model, base_url=args.judge_base_url, api_key=key)

    return LangchainLLMWrapper(chat), LangchainEmbeddingsWrapper(embedder)


def build_metrics(names: list[str]):
    import warnings

    # ragas 0.4 has two sets of metric classes. The ones in
    # ragas.metrics.collections need an instructor-based client and are scored
    # one call at a time; these work with `evaluate` and any LangChain chat
    # model, which is what lets the judge be a local Ollama model. They are
    # deprecated for removal in ragas 1.0, hence the pin in pyproject.toml.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from ragas.metrics import (
            FactualCorrectness,
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
            ResponseRelevancy,
        )

    available = {
        "faithfulness": Faithfulness,
        "answer_relevancy": ResponseRelevancy,
        "context_precision": LLMContextPrecisionWithReference,
        "context_recall": LLMContextRecall,
        "factual_correctness": FactualCorrectness,
    }
    return {name: available[name]() for name in names}


def judge(records: list[dict], names: list[str], args: argparse.Namespace) -> dict[str, list[float | None]]:
    """Per-record scores for each metric. None where a record could not be scored.

    Each metric is run over the records it applies to: one that needs a
    reference skips the lines without one, and a record with no answer or no
    passages is not judged at all (it is counted as unanswered instead).
    """
    from ragas import EvaluationDataset, RunConfig, SingleTurnSample, evaluate

    llm, embeddings = build_judge(args)
    metrics = build_metrics(names)
    config = RunConfig(timeout=args.judge_timeout, max_workers=args.judge_workers, max_retries=3)

    scores: dict[str, list[float | None]] = {name: [None] * len(records) for name in names}
    answered = [i for i, r in enumerate(records) if r["answer"] and r["contexts"] and not r["error"]]

    for name, metric in metrics.items():
        needs_reference = METRICS[name][0]
        rows = [i for i in answered if records[i]["reference"] or not needs_reference]
        if not rows:
            continue

        dataset = EvaluationDataset(
            samples=[
                SingleTurnSample(
                    user_input=records[i]["question"],
                    response=records[i]["answer"],
                    retrieved_contexts=records[i]["contexts"],
                    reference=records[i]["reference"],
                )
                for i in rows
            ]
        )

        print(f"judging {name} on {len(rows)} answer(s)")
        result = evaluate(
            dataset,
            metrics=[metric],
            llm=llm,
            embeddings=embeddings,
            run_config=config,
            raise_exceptions=False,
            show_progress=False,
        )

        frame = result.to_pandas()
        # The one column that is not part of the sample is the score.
        column = next(
            c for c in frame.columns if c not in {"user_input", "response", "retrieved_contexts", "reference"}
        )

        for i, value in zip(rows, frame[column].tolist()):
            # NaN is how Ragas reports a judge call that failed or timed out.
            scores[name][i] = None if value is None or value != value else round(float(value), 4)

    return scores


# --- report ------------------------------------------------------------------------------


def mean(values: list[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return round(sum(kept) / len(kept), 4) if kept else None


def write_report(args, chat_id: str, records: list[dict], scores: dict[str, list], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for i, record in enumerate(records):
        record["scores"] = {name: values[i] for name, values in scores.items()}

    totals = [r["total_s"] for r in records if not r["error"]]
    firsts = [r["first_token_s"] for r in records if r["first_token_s"] is not None]

    summary = {
        "run": stamp,
        "base_url": args.base_url,
        "dataset": str(args.dataset),
        "notebook": chat_id,
        "judge": {"backend": args.judge_backend, "model": args.judge_model, "embedder": args.embed_model},
        "questions": len(records),
        "answered": sum(1 for r in records if r["answer"] and not r["error"]),
        "with_passages": sum(1 for r in records if r["contexts"]),
        "refused": sum(1 for r in records if r["refused"]),
        "errors": sum(1 for r in records if r["error"]),
        "mean_first_token_s": round(sum(firsts) / len(firsts), 2) if firsts else None,
        "mean_total_s": round(sum(totals) / len(totals), 2) if totals else None,
        "scores": {name: mean(values) for name, values in scores.items()},
        "scored": {name: sum(v is not None for v in values) for name, values in scores.items()},
    }

    report = out / f"ragas_{stamp}.json"
    report.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2))
    (out / "ragas_latest.json").write_text(report.read_text())

    print()
    print(f"answered {summary['answered']}/{summary['questions']}, with passages {summary['with_passages']}, ", end="")
    print(f"refused {summary['refused']}, errors {summary['errors']}")
    print(f"mean first token {summary['mean_first_token_s']}s, mean answer {summary['mean_total_s']}s")
    for name, value in summary["scores"].items():
        shown = "   n/a" if value is None else f"{value:6.3f}"
        print(f"  {name:22} {shown}   ({summary['scored'][name]} scored)")
    print(f"report: {report}")

    return summary


def main() -> int:
    load_env(HERE / ".env")
    args = parse_args()

    names = [name.strip() for name in args.metrics.split(",") if name.strip()]
    unknown = [name for name in names if name not in METRICS]
    if unknown:
        sys.exit(f"unknown metric(s): {', '.join(unknown)}")
    floors = thresholds(args.min, names)

    email, password = os.environ.get("EVAL_EMAIL"), os.environ.get("EVAL_PASSWORD")
    if not email or not password:
        sys.exit("Set EVAL_EMAIL and EVAL_PASSWORD (copy evals/.env.example to evals/.env)")

    questions = read_questions(args.dataset, args.limit)

    client = NotebookClient(args.base_url)
    try:
        client.sign_in(email, password)
        chat_id = prepare_notebook(client, args)

        print(f"asking {len(questions)} question(s)")
        started = time.monotonic()
        records = collect(client, chat_id, questions, args.top_k)
        print(f"  done in {time.monotonic() - started:.0f}s")
    except NotebookError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    scores = {} if args.answers_only else judge(records, names, args)
    summary = write_report(args, chat_id, records, scores, args.out)

    failed = [
        f"{name} {summary['scores'].get(name)} < {floor}"
        for name, floor in floors.items()
        if summary["scores"].get(name) is None or summary["scores"][name] < floor
    ]
    if failed:
        print("below threshold: " + "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
