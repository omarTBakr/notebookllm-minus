# OCR Benchmark Report

## Comparison

| extractor | pages | success | CER | WER | seconds/page | CPU seconds | peak RSS MiB | peak GPU MiB | usable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pymupdf-raw | 4 | 4/4 | - | - | 0.47 | 0.47 | 1.48 | 295.44 | 0/4 |
| pymupdf-words | 4 | 4/4 | - | - | 0.47 | 0.47 | 1.48 | 295.44 | 1/4 |
| pdfplumber | 4 | 4/4 | - | - | 0.16 | 0.16 | 1.48 | 295.44 | 2/4 |
| tesseract | 4 | 4/4 | - | - | 1.55 | 0.71 | 1.52 | 295.44 | 4/4 |
| easyocr | 4 | 4/4 | - | - | 3.47 | 6.65 | 1.61 | 3808.82 | 4/4 |

## Plots

- [Time](plots/time.png)
- [CPU time](plots/cpu.png)
- [Resident memory](plots/rss.png)
- [GPU allocation](plots/gpu.png)

## Requirements and allocation

| extractor | available | text layer | GPU | requirements | description |
| --- | --- | --- | --- | --- | --- |
| pymupdf-raw | yes | yes | no | CPU | pymupdf get_text() — the page's own text layer, untouched |
| pymupdf-words | yes | yes | no | CPU | rebuilt from per-word boxes — what PdfLayoutController does |
| pdfplumber | yes | yes | no | CPU | pdfplumber's own word segmentation — a third opinion |
| tesseract | yes | no | no | CPU | Tesseract 5 with the ara traineddata (CPU) |
| tesseract-best | no | no | no | TESSDATA_BEST is not set | Tesseract 5 with the tessdata_best ara model (CPU) |
| easyocr | yes | no | yes | GPU | EasyOCR ar+en, PyTorch detector + recogniser |
| paddleocr | no | no | yes | paddleocr is not installed | PaddleOCR with the arabic recognition model |
| surya | no | no | yes | surya-ocr is not installed | Surya detection + recognition (transformer, GPU-oriented) |
| qari | no | no | yes | transformers/torch are not installed | Qari-OCR (Qwen-VL fine-tune, Arabic-specific, local) |
| gemini | no | no | no | GOOGLE_API_KEY is not set in the environment or .env | Gemini multimodal via the API (metered) |

## Snapshots

Each real page has a rendered image and one text file per engine.
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-easyocr.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-easyocr.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-pdfplumber.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-pdfplumber.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-pymupdf-raw.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-pymupdf-raw.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-pymupdf-words.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-pymupdf-words.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-tesseract.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183-tesseract.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183.png](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p183.png)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-easyocr.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-easyocr.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-pdfplumber.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-pdfplumber.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-pymupdf-raw.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-pymupdf-raw.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-pymupdf-words.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-pymupdf-words.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-tesseract.txt](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92-tesseract.txt)
- [snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92.png](snapshots/دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني-p92.png)
- [snapshots/ذخائر_لبنان-p149-easyocr.txt](snapshots/ذخائر_لبنان-p149-easyocr.txt)
- [snapshots/ذخائر_لبنان-p149-pdfplumber.txt](snapshots/ذخائر_لبنان-p149-pdfplumber.txt)
- [snapshots/ذخائر_لبنان-p149-pymupdf-raw.txt](snapshots/ذخائر_لبنان-p149-pymupdf-raw.txt)
- [snapshots/ذخائر_لبنان-p149-pymupdf-words.txt](snapshots/ذخائر_لبنان-p149-pymupdf-words.txt)
- [snapshots/ذخائر_لبنان-p149-tesseract.txt](snapshots/ذخائر_لبنان-p149-tesseract.txt)
- [snapshots/ذخائر_لبنان-p149.png](snapshots/ذخائر_لبنان-p149.png)
- [snapshots/ذخائر_لبنان-p75-easyocr.txt](snapshots/ذخائر_لبنان-p75-easyocr.txt)
- [snapshots/ذخائر_لبنان-p75-pdfplumber.txt](snapshots/ذخائر_لبنان-p75-pdfplumber.txt)
- [snapshots/ذخائر_لبنان-p75-pymupdf-raw.txt](snapshots/ذخائر_لبنان-p75-pymupdf-raw.txt)
- [snapshots/ذخائر_لبنان-p75-pymupdf-words.txt](snapshots/ذخائر_لبنان-p75-pymupdf-words.txt)
- [snapshots/ذخائر_لبنان-p75-tesseract.txt](snapshots/ذخائر_لبنان-p75-tesseract.txt)
- [snapshots/ذخائر_لبنان-p75.png](snapshots/ذخائر_لبنان-p75.png)

## Interpretation

CER measures character edits; WER measures retrieval-relevant word edits.
Lower is better for both. Time and CPU seconds are per page. RSS is the
process peak and GPU is the allocator peak when Torch is available; a dash
means the runtime could not expose that metric. Compare synthetic scores
only against other synthetic scores, and use real-document agreement and
manual review for pages without ground truth.
