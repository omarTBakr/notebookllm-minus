# Fine-tuning `gemma4:e4b` to correct Arabic extraction damage

## Why

`gemma4:e4b` is this project's default local model
(`GENERATION_MODEL_ID` in `src/.env`), and it is the model
`TextCorrectionController` calls at ingest time to repair Arabic text that a
PDF's own extraction damaged — see `src/application/tasks/jobs/ingest/postprocess.py` and
`src/application/ocr_prompts/en/ocr_correction.py`.

Measured on this same corpus, though, its zero-shot correction is close to a
no-op: asked to repair a fragmented Arabic page, it returned text 0.998
similar to the damaged input — one character changed. That is why production
ended up calling a hosted NVIDIA NIM model instead
(`nvidia/nemotron-3-super-120b-a12b`, see the comment above
`POSTPROCESS_MODEL_ID` in `src/shared/utils/config.py`), which works, but is metered,
rate-limited, and not private.

Fine-tuning `gemma4:e4b` on the task directly is the alternative: teach the
weights to do this correction, rather than lean on prompting, so a free,
private, always-available local model can do what currently requires a
hosted endpoint.

## The data: distillation, not manual labelling

There is no human-corrected ground truth for this corpus at any useful scale.
What exists instead is a *better* automated extractor — this project's own
`tesseract-best` (Tesseract 5 against the `tessdata_best` Arabic model),
independently benchmarked at 0.172 word-error-rate on real pages of this same
corpus (`src/application/arabic_extraction/benchmark/reports/`). That is accurate enough to stand in as a teacher
label, and far too slow to run at ingest time on every document (it OCRs a
rendered 300-dpi page image, ~2.3s/page pinned to two cores) — which is
exactly the gap a fine-tuned model closes: do what tesseract-best does,
straight from the text layer, in a single fast text-in/text-out call.

So one training example is one page, paired as:

- **`user`** — LangChain's `PyMuPDFLoader` (`mode="page"`), a fast, literal
  `page.get_text()`. This is the *damaged* side: presentation-form glyphs,
  no logical-order reconstruction, no normalisation. (Not the same extractor
  production ingest uses — see [Design notes](#design-notes).)
- **`model`** — `tesseract-best`, this repo's own extractor
  (`src/application/arabic_extraction/extractors/traditional.py`), run through the same
  `application.arabic_extraction.registry`/`application.arabic_extraction.base.Page` machinery the OCR benchmark uses. This is
  the *corrected* target.

Real example, page 21 of `ذخائر_لبنان.pdf`:

```
user:  ﻟﺒﻨﺎن ،وﻳﻮﺟﺪ اﻟﻔﺤﻢ اﻟﺤﺠﺮي أﻳﻀًﺎﰲﻧﻮاﺣﻲ اﻟﺸﻮﻳﺮ، وﺑﻜﻔﻴﺎ...
model: ويوجد الفحم الحجري أيضًا في نواحي الشوير، وبكفيا...
```

## Schema

One directory per example, numbered `1..n`, under `fineTuning/data/`:

```
fineTuning/data/
├── 1/conversation.json
├── 2/conversation.json
├── ...
├── n/conversation.json
└── manifest.json
```

`conversation.json`:

```json
{
  "conversations": [
    { "role": "user", "content": "<damaged page text>" },
    { "role": "model", "content": "<corrected page text>" }
  ]
}
```

**`"model"`, not `"assistant"`.** Gemma's own chat template is literally
`<start_of_turn>model ... <end_of_turn>` — there is no `assistant` turn in it
— and Unsloth's dataset standardiser (`standardize_data_formats`, used in its
own Gemma fine-tuning notebooks) emits exactly `"role": "model"` for this
model family. Google's generic Hugging Face/QLoRA guide uses `"assistant"`
instead, which only works because `apply_chat_template` remaps it for you;
since the target here is Gemma specifically, `"model"` is the template-native
choice and needs no remapping step.

`manifest.json` records, per example: source PDF, the 0-based page indices it
covers, and character counts on each side — the only thing that makes a
few-hundred-directory output auditable without opening each one.

## Generating it

`genrateData/` is a small package, not one script:

| File | Owns |
| --- | --- |
| `paths.py` | repo-root/`src` resolution — every other module imports this first |
| `extract.py` | the two extractors: pymupdf (damaged) and tesseract-best (corrected) |
| `grouping.py` | pairing pages into examples, merging short ones forward |
| `writer.py` | one numbered directory + `conversation.json` per example |
| `preflight.py` | failing fast if `tesseract-best` can't run, or `--out` is dirty |
| `cli.py` | argument parsing and the run loop |
| `build_dataset.py` | the entry point — `python fineTuning/genrateData/build_dataset.py` |

```bash
# one-time: fetch the accurate Arabic model tesseract-best needs (12.6 MB,
# the same source Docker/notebookllm-minus/Dockerfile already pulls into the
# image — this repo's dev machine does not have it under /usr/share)
mkdir -p fineTuning/tessdata_best
curl -fsSL -o fineTuning/tessdata_best/ara.traineddata \
     https://github.com/tesseract-ocr/tessdata_best/raw/main/ara.traineddata

# smoke test first — a handful of pages, one book, before committing to a
# multi-hundred-page OCR run
uv run --project src python fineTuning/genrateData/build_dataset.py \
    --pdf "fineTuning/assets/ذخائر_لبنان.pdf" --max-pages 5 \
    --out /tmp/ft-smoke --tessdata-dir fineTuning/tessdata_best --force

# the real thing — every PDF under fineTuning/assets/, into fineTuning/data/
uv run --project src python fineTuning/genrateData/build_dataset.py \
    --tessdata-dir fineTuning/tessdata_best --force
```

`build_dataset.py` fails fast with a clear reason (via `application.arabic_extraction.registry.survey()`)
if `tesseract-best` cannot run, rather than silently writing an empty
dataset. See `--help` for every flag; the ones worth knowing:

| Flag | Does |
| --- | --- |
| `--pdf` | one PDF to process (repeatable). Default: every PDF under `fineTuning/assets/` |
| `--min-chars` | a page whose damaged text is shorter than this is merged forward into the next page, rather than becoming a degenerate standalone example (default 200) |
| `--max-pages` | cap per PDF, for a fast run before the real one |
| `--force` | wipe `--out` first if it already has content |

Both source PDFs together are 496 pages. A full run took 2194s (~37 minutes)
on this machine and produced 471 examples with zero extraction failures on
either side — most pages stood alone; the ~25-page difference is pages merged
under `--min-chars` or dropped for being unreadable on one side.

## Design notes

**Why `PyMuPDFLoader`, not this repo's own `extract_pages`.** Production
ingest (`ProcessController._process_pdf_with_layout`) uses a different
pymupdf path — `page.get_text("words")`, cleaned and rejoined with single
spaces, which is what produces the "`اليسار`→`ا ليسا ر`" over-segmentation
documented in the main project README. LangChain's `PyMuPDFLoader` instead
calls plain `page.get_text()`, which produces a *different* kind of damage:
presentation-form glyphs (`ﻟﺒﻨﺎن` instead of `لبنان`) without ligature or
logical-order normalisation. Both are real, both are worth a model learning
to fix, but they are not the same distribution — a model fine-tuned purely on
this dataset will be strongest on `PyMuPDFLoader`-shaped damage, and the
gap to production's actual input is a reasonable next thing to measure
before relying on this for the deployed correction pass.

**Why one page, never a sub-page split.** `tesseract-best` OCRs a whole
rendered page in a single call — there is no way to cut its output at an
arbitrary character offset to match an independently-split `pymupdf` chunk
without the two drifting out of alignment. Page boundaries are the one place
both extractors naturally agree, so grouping (never splitting) is the only
merge operation this script performs.

**A known weak spot: mixed-script front matter.** Title pages, publisher
boilerplate, and any page mixing Arabic prose with Latin-script URLs, emails
or phone numbers get *worse* under `tesseract-best` — its `lang="ara"`
configuration reads Arabic well and Latin/numeric content poorly, sometimes
turning a working URL into garbage the `pymupdf` side had rendered more
faithfully. Prose-only body pages (the large majority of both source books)
do not have this problem — the example above is representative of them. If
this dataset is used to actually fine-tune, filtering `manifest.json` for
suspiciously short pages near the start of a document (title pages, colophons)
before training is worth doing; this script does not do that filtering itself.

## Next steps (not implemented here)

This directory only builds the dataset. Actually fine-tuning and shipping the
result, roughly:

1. **Train** with [Unsloth](https://unsloth.ai/docs) (LoRA/QLoRA over
   `gemma4:e4b`'s base weights), loading this data with:
   ```python
   from datasets import load_dataset
   dataset = load_dataset("json", data_files="fineTuning/data/*/conversation.json")
   ```
2. **Export** the result to GGUF (`unsloth.save_pretrained_gguf` or
   equivalent).
3. **Serve it locally** with a Modelfile and `ollama create
   gemma4-corrector -f Modelfile`, pointing `POSTPROCESS_MODEL_ID` at the new
   local tag once it is measured to actually help.

None of that is built yet — this is deliberately scoped to the dataset.
