# Colab notebooks

Workbench notebooks. Nothing here is on the ingestion path — the application
runs `tesseract-best` inline and never depends on a Colab session.

| notebook | needs | what it answers |
| --- | --- | --- |
| `tesseract_benchmark.ipynb` | CPU | what `tesseract-best` costs per page, how it scales across threads, and what lowering DPI actually buys |
| `qari_benchmark.ipynb` | **T4 GPU** | what Qari-OCR reads on the same pages, and what its accuracy costs in seconds |
| `OllamaAPI.ipynb` | — | unrelated; kept from earlier work |

Both benchmarks score identically — same normalisation, same CER/WER, same
space-ratio signal — so run them on the **same pages of the same PDF** and the
two tables can be read as one.

## Why measure on Colab at all

Seconds-per-page is not a property of the engine, it is a property of the engine
*and the machine*. Measured on the same 8M-iteration Python loop:

```
laptop        0.44s
t3.medium     1.48s     ← the deployment box, ~3.4x slower
```

Quoting a laptop's figure at a server is how a 214-page Arabic book came to need
1136s against a 540s task budget: the book was fine, the estimate was not. Colab
gives a third reference point, and the ratios between the three are worth more
than any single absolute number.

## The trade these two measure

| | `tesseract-best` | `qari` |
| --- | ---: | ---: |
| WER, this project's fixtures | 0.172 | **0.063** |
| s/page, real book page | ~2 fast CPU · ~5-6 on t3.medium | ~30 on a T4 |
| needs | nothing | ~5 GB VRAM |

Roughly an order of magnitude slower per page for about three times the accuracy.
Worth it for a document you care about; not for a 200-page book in bulk. That is
why `tesseract-best` is what ingestion runs, and Qari is the deliberate path.

`src/ocr/reports/FINDINGS.md` has the full ten-engine comparison, and
`src/ocr/colab/qari_server.ipynb` is the *other* Qari notebook — that one serves
the model over HTTP to the running app rather than benchmarking it.
