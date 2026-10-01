# OCR Benchmark Report

## Comparison

| extractor | pages | success | CER | WER | seconds/page | CPU seconds | peak RSS MiB | peak GPU MiB | usable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qari | 4 | 4/4 | 0.0331 | 0.0625 | 3.29 | 3.51 | 5232.18 | 4756.39 | 4/4 |
| gemini | 4 | 1/4 | 0.1081 | 0.1667 | 0.99 | 0.09 | 686.8 | - | 1/1 |
| tesseract-best | 4 | 4/4 | 0.1304 | 0.1717 | 0.26 | 0.09 | 680.86 | - | 4/4 |
| easyocr | 4 | 4/4 | 0.0989 | 0.3164 | 0.42 | 0.5 | 1342.95 | 1444.11 | 4/4 |
| tesseract | 4 | 4/4 | 0.3158 | 0.545 | 0.2 | 0.09 | 682.13 | - | 4/4 |
| paddleocr | 4 | 4/4 | 0.4327 | 0.6796 | 16.09 | 96.08 | 13208.43 | - | 4/4 |
| pdfplumber | 4 | 0/4 | - | - | 0.0 | 0.0 | 613.67 | - | 0/0 |
| pymupdf-raw | 4 | 0/4 | - | - | 0.0 | 0.0 | 642.32 | - | 0/0 |
| pymupdf-words | 4 | 0/4 | - | - | 0.0 | 0.0 | 644.68 | - | 0/0 |
| surya | 4 | 0/4 | - | - | 0.08 | 0.06 | 783.94 | 0.0 | 0/0 |

## Plots

- [Time](plots/time.png)
- [CPU time](plots/cpu.png)
- [Resident memory](plots/rss.png)
- [GPU allocation](plots/gpu.png)
- [CER](plots/cer.png)
- [WER](plots/wer.png)

## Requirements and allocation

| extractor | available | text layer | GPU | requirements | description |
| --- | --- | --- | --- | --- | --- |
| pymupdf-raw | yes | yes | no | CPU | pymupdf get_text() — the page's own text layer, untouched |
| pymupdf-words | yes | yes | no | CPU | rebuilt from per-word boxes — what PdfLayoutController does |
| pdfplumber | yes | yes | no | CPU | pdfplumber's own word segmentation — a third opinion |
| tesseract | yes | no | no | CPU | Tesseract 5 with the ara traineddata (CPU) |
| tesseract-best | yes | no | no | CPU | Tesseract 5 with the tessdata_best ara model (CPU) |
| easyocr | yes | no | yes | GPU | EasyOCR ar+en, PyTorch detector + recogniser |
| paddleocr | no | no | yes | paddleocr is not installed | PaddleOCR with the arabic recognition model |
| surya | no | no | yes | needs the NVIDIA container runtime; docker reports none (install nvidia-container-toolkit) | Surya detection + recognition (transformer, GPU-oriented) |
| qari | yes | no | yes | GPU | Qari-OCR (Qwen-VL fine-tune, Arabic-specific, local) |
| gemini | no | no | no | GOOGLE_API_KEY is not set in the environment or .env | Gemini multimodal via the API (metered) |

## The same line, every engine

Metrics describe output; this shows it. Read these before trusting any number above them.

### `diacritics.pdf` page 1

Ground truth:

```
بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ
```

```
easyocr          بسم الله الدًحمن الرحيدم
الحمد للًه ربً العالمين
paddleocr        بِسم الَ لَخمِ لرّحيِ
الحمَد َِهِرَبّ اْعَمِينَ
qari             بِسْمِ اللَّهِ الرَّحْمُنِ الرَّحِيمِ الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ
tesseract        5 شي ج هط َه
بِسْم الله الرحمن الرَحِيعم
الْحَمْدُ لِلَّهِ رَبّ الْعَالْمِينَ
tesseract-best   ‎1١ 0 8‏ 0
بسع اللهِ الرحمّن الرجيع
الْحَمْدُلِلَّهِ رَبٌّ الْعَالَمِينَ
```

### `mixed.pdf` page 1

Ground truth:

```
البند الأول: مراجعة البيانات المالية لعام 2025.
```

```
easyocr          البند الأول : مراجعة البيانات المالية لعام 2025
gemini           البند الأول: مراجعة البيانات المالية لعام 2025.
paddleocr        البيانات المالية لعام
لبند الأول مراجعة
المرفق
الصفحة  من التقرير
راجع
النسبةمقارنة في
الع
qari             البند الأول: مراجعة البيانات المالية لعام 2025. راجع الصفحة 42 من التقرير المرفق ..... ..... ..
tesseract        البند الأول: مراجعة البيانات المالية لعام 2025.
tesseract-best   البند الأول: مراجعة البيانات المالية لعام 2025.
```

## Interpretation

CER measures character edits; WER measures retrieval-relevant word edits.
Lower is better for both. Time and CPU seconds are per page. RSS is the
process peak and GPU is the allocator peak when Torch is available; a dash
means the runtime could not expose that metric. Compare synthetic scores
only against other synthetic scores, and use real-document agreement and
manual review for pages without ground truth.
