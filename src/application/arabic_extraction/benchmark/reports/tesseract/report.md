# OCR Benchmark Report

## Comparison

| extractor | pages | success | CER | WER | seconds/page | CPU seconds | peak RSS MiB | peak GPU MiB | usable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tesseract | 4 | 4/4 | 0.3058 | 0.3891 | 0.2 | 0.09 | 179.62 | - | 4/4 |

## Plots

- [Time](plots/time.png)
- [CPU time](plots/cpu.png)
- [Resident memory](plots/rss.png)
- [CER](plots/cer.png)
- [WER](plots/wer.png)

## Requirements and allocation

| extractor | available | text layer | GPU | requirements | description |
| --- | --- | --- | --- | --- | --- |
| pymupdf-raw | yes | yes | no | CPU | pymupdf get_text() — the page's own text layer, untouched |
| pymupdf-words | yes | yes | no | CPU | rebuilt from per-word boxes — what PdfLayoutController does |
| pdfplumber | no | yes | no | pdfplumber is not installed | pdfplumber's own word segmentation — a third opinion |
| tesseract | yes | no | no | CPU | Tesseract 5 with the ara traineddata (CPU) |
| tesseract-best | no | no | no | TESSDATA_BEST is not set | Tesseract 5 with the tessdata_best ara model (CPU) |
| easyocr | no | no | yes | easyocr is not installed | EasyOCR ar+en, PyTorch detector + recogniser |
| paddleocr | no | no | yes | paddleocr is not installed | PaddleOCR with the arabic recognition model |
| surya | no | no | yes | surya-ocr is not installed | Surya detection + recognition (transformer, GPU-oriented) |
| qari | no | no | yes | transformers/torch are not installed | Qari-OCR (Qwen-VL fine-tune, Arabic-specific, local) |
| gemini | no | no | no | GOOGLE_API_KEY is not set in the environment or .env | Gemini multimodal via the API (metered) |

## Interpretation

CER measures character edits; WER measures retrieval-relevant word edits.
Lower is better for both. Time and CPU seconds are per page. RSS is the
process peak and GPU is the allocator peak when Torch is available; a dash
means the runtime could not expose that metric. Compare synthetic scores
only against other synthetic scores, and use real-document agreement and
manual review for pages without ground truth.
