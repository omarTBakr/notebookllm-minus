"""Classical OCR engines: detect text regions, then recognise characters.

All three predate the vision-language models below them and share an
architecture — a detector proposes boxes, a recogniser reads each one — which
means they also share a weakness on Arabic. The script is cursive and its
letters change shape by position, so a per-box recogniser has to segment a word
into characters that were never separate to begin with. KITAB-Bench (ACL 2025)
measured this: modern VLMs beat this whole class on Arabic by around 60% CER.

They are here anyway, and not as a formality. They run on a CPU, they need no
API key, they cost nothing per page, and the deployment target is a 2-vCPU box.
If one of them is good enough, that is the answer — "good enough and free"
beats "better and metered" for a self-hosted service. The benchmark exists to
find out which of those two worlds this corpus lives in.
"""

from __future__ import annotations

from ..base import ArabicExtractor, Page


class TesseractExtractor(ArabicExtractor):
    """The classical baseline, via the `ara` language pack.

    Cheapest thing here by a wide margin and the only one that adds tens of
    megabytes rather than hundreds. `--psm 6` treats the page as one uniform
    block, which suits body text; the default tries to detect layout and on
    dense RTL prose tends to invent columns that are not there.
    """

    name = "tesseract"
    description = "Tesseract 5 with the ara traineddata (CPU)"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import pytesseract
        except ImportError:
            return False, "pytesseract is not installed"

        try:
            languages = pytesseract.get_languages(config="")
        except Exception as exc:  # noqa: BLE001
            return False, f"the tesseract binary is missing or broken: {exc}"

        if "ara" not in languages:
            return False, "the ara traineddata is not installed (tesseract-langpack-ara)"

        return True, ""

    def _extract(self, page: Page) -> str:
        import pytesseract

        return pytesseract.image_to_string(
            page.image,
            lang=self.options.get("lang", "ara"),
            config=self.options.get("config", "--psm 6"),
        )


class TesseractBestExtractor(TesseractExtractor):
    """The same engine against the `tessdata_best` Arabic model.

    Worth its own row because distributions ship the *fast* traineddata — 1.4 MB
    on Fedora against 12.6 MB for best — and the two are different models, not
    two speed settings on one. Comparing "Tesseract" to a neural engine while
    running the fast model understates it, which is how Tesseract acquires its
    reputation for being hopeless at Arabic.

    Point TESSDATA_BEST at a directory holding ara.traineddata from
    github.com/tesseract-ocr/tessdata_best.
    """

    name = "tesseract-best"
    description = "Tesseract 5 with the tessdata_best ara model (CPU)"

    @staticmethod
    def _tessdata_dir() -> str:
        """Where ara.traineddata from tessdata_best lives.

        Environment first, so the package still runs standalone, then the
        application's own setting. Reading only the environment was a real bug:
        `TESSDATA_BEST` existed in Settings and in the image, nothing exported
        it into the process, and OCR_ENABLED=true therefore took the "cannot
        run" path and quietly kept the broken text layer — a feature that looks
        enabled and does nothing.
        """
        import os

        directory = os.environ.get("TESSDATA_BEST", "")

        if not directory:
            try:
                from utils import get_settings

                directory = getattr(get_settings(), "TESSDATA_BEST", "") or ""
            except Exception:  # noqa: BLE001 - standalone use is expected
                pass

        return directory

    @classmethod
    def available(cls) -> tuple[bool, str]:
        from pathlib import Path as _Path

        ok, reason = TesseractExtractor.available()

        if not ok:
            return ok, reason

        directory = cls._tessdata_dir()

        if not directory:
            return False, "TESSDATA_BEST is not set"

        if not (_Path(directory) / "ara.traineddata").is_file():
            return False, f"no ara.traineddata under {directory}"

        return True, ""

    def _extract(self, page: Page) -> str:
        import pytesseract

        return pytesseract.image_to_string(
            page.image,
            lang=self.options.get("lang", "ara"),
            config=f'--tessdata-dir "{self._tessdata_dir()}" ' + self.options.get("config", "--psm 6"),
        )


class EasyOCRExtractor(ArabicExtractor):
    """PyTorch detector + recogniser, ~500 MB of weights.

    Returns boxes rather than a page of text, so the lines have to be
    reassembled here. Sorting purely top-to-bottom would interleave a
    two-column page; sorting by (row band, then x) keeps reading order, and for
    Arabic the x order within a band is right-to-left.
    """

    name = "easyocr"
    description = "EasyOCR ar+en, PyTorch detector + recogniser"
    wants_gpu = True

    _reader = None

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import easyocr  # noqa: F401
        except ImportError:
            return False, "easyocr is not installed"
        return True, ""

    def warm_up(self) -> None:
        import easyocr

        if EasyOCRExtractor._reader is None:
            EasyOCRExtractor._reader = easyocr.Reader(
                self.options.get("languages", ["ar", "en"]),
                gpu=self.options.get("gpu", True),
                verbose=False,
            )

    def _extract(self, page: Page) -> str:
        import numpy as np

        self.warm_up()

        results = EasyOCRExtractor._reader.readtext(np.array(page.image))

        if not results:
            return ""

        # Group into lines by vertical midpoint, then order right-to-left
        # within each. A flat sort by y alone shuffles words between adjacent
        # lines whenever their boxes overlap by a pixel, which they routinely do.
        boxes = []
        for box, text, confidence in results:
            ys = [point[1] for point in box]
            xs = [point[0] for point in box]
            boxes.append(((min(ys) + max(ys)) / 2, max(xs), text, confidence))

        # A box is [[x0,y0],[x1,y0],[x1,y1],[x0,y1]], so its height is the y of
        # the third corner minus the first. Reaching one level deeper than that
        # indexes into a scalar, which is how this silently returned nothing at
        # all for every page until the error was read rather than the score.
        heights = [abs(box[2][1] - box[0][1]) for box, _, _ in results] or [10]
        tolerance = max(8.0, sum(heights) / len(heights) * 0.6)

        lines: list[list[tuple]] = []
        for entry in sorted(boxes, key=lambda b: b[0]):
            if lines and abs(entry[0] - lines[-1][0][0]) <= tolerance:
                lines[-1].append(entry)
            else:
                lines.append([entry])

        return "\n".join(" ".join(word[2] for word in sorted(line, key=lambda w: -w[1])) for line in lines)


class PaddleOCRExtractor(ArabicExtractor):
    """PaddlePaddle's OCR, which ships a dedicated Arabic recognition model.

    Its `arabic` model is trained on the script specifically rather than as one
    of eighty languages, which is the reason to try it over EasyOCR despite the
    heavier install.
    """

    name = "paddleocr"
    description = "PaddleOCR with the arabic recognition model"
    wants_gpu = True

    _engine = None

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            return False, "paddleocr is not installed"
        return True, ""

    def warm_up(self) -> None:
        from paddleocr import PaddleOCR

        if PaddleOCRExtractor._engine is not None:
            return

        # PaddleOCR renamed things between 2.x and 3.x: the Arabic code went
        # from "arabic" to "ar", and use_angle_cls/show_log stopped being
        # constructor arguments. Try the modern call and fall back, so a
        # version bump shows up as a clear failure rather than a TypeError
        # buried in a warm-up.
        lang = self.options.get("lang", "ar")

        # oneDNN off by default. With it on, this build raises
        #   NotImplementedError: ConvertPirAttribute2RuntimeAttribute not
        #   support [pir::ArrayAttribute<pir::DoubleAttribute>]
        # from inside Paddle's executor on the first page — an engine bug in
        # the acceleration path, not anything to do with Arabic, and one that
        # otherwise reads as "PaddleOCR produced no output".
        try:
            PaddleOCRExtractor._engine = PaddleOCR(lang=lang, enable_mkldnn=self.options.get("enable_mkldnn", False))
        except TypeError:
            PaddleOCRExtractor._engine = PaddleOCR(lang=lang)
        except ValueError:
            PaddleOCRExtractor._engine = PaddleOCR(
                lang=self.options.get("legacy_lang", "arabic"),
                use_angle_cls=False,
                show_log=False,
            )

    def _extract(self, page: Page) -> str:
        import numpy as np

        self.warm_up()

        engine = PaddleOCRExtractor._engine
        image = np.array(page.image)

        # 3.x exposes predict() and returns a list of result objects; 2.x
        # returns nested lists from ocr(). Both shapes are handled rather than
        # pinning a version, since this is a benchmark and the point is to run
        # whatever the user actually has installed.
        if hasattr(engine, "predict"):
            results = engine.predict(image)
            lines = []
            for result in results or []:
                texts = result.get("rec_texts") if isinstance(result, dict) else getattr(result, "rec_texts", None)
                if texts:
                    lines.extend(texts)
            return "\n".join(lines)

        result = engine.ocr(image, cls=False)

        if not result or not result[0]:
            return ""

        return "\n".join(line[1][0] for line in result[0] if line and line[1])


class SuryaExtractor(ArabicExtractor):
    """Surya's detection + recognition, which lists Arabic among its languages.

    Transformer-based and GPU-hungry — the reason `wants_gpu` exists on the
    base class. On CPU it is usable for a handful of pages and not for a book.
    """

    name = "surya"
    description = "Surya detection + recognition (transformer, GPU-oriented)"
    wants_gpu = True

    _predictors = None

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import surya  # noqa: F401
        except ImportError:
            return False, "surya-ocr is not installed"

        # Installed is not the same as runnable. This build spawns a container
        # to serve its model and fails with
        #   SpawnError: docker run failed: unknown or invalid runtime name: nvidia
        # unless the NVIDIA container runtime is configured. Reporting that as
        # an availability reason keeps it out of the results table, where an
        # empty row reads as "surya is bad at Arabic" rather than "surya never
        # ran here".
        import shutil
        import subprocess

        if shutil.which("docker") is None:
            return True, ""

        try:
            runtimes = subprocess.run(
                ["docker", "info", "--format", "{{json .Runtimes}}"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return True, ""

        if "nvidia" not in runtimes:
            return False, (
                "needs the NVIDIA container runtime; docker reports none " "(install nvidia-container-toolkit)"
            )

        return True, ""

    def warm_up(self) -> None:
        if SuryaExtractor._predictors is not None:
            return

        # Surya has reshuffled these across releases, so each layout is tried
        # in turn and the failure is reported rather than guessed at. The
        # version installed here exposes DetectionPredictor and
        # RecognitionPredictor directly, with no foundation model to pass.
        from surya.detection import DetectionPredictor
        from surya.recognition import RecognitionPredictor

        try:
            from surya.foundation import FoundationPredictor

            recognition = RecognitionPredictor(FoundationPredictor())
        except (ImportError, TypeError):
            recognition = RecognitionPredictor()

        SuryaExtractor._predictors = (DetectionPredictor(), recognition)

    def _extract(self, page: Page) -> str:
        self.warm_up()

        detection, recognition = SuryaExtractor._predictors

        # full_page reads the whole image rather than requiring layout boxes,
        # which is what a plain transcription wants.
        try:
            predictions = recognition([page.image], det_predictor=detection)
        except TypeError:
            predictions = recognition([page.image], [None], full_page=True)

        if not predictions:
            return ""

        lines = getattr(predictions[0], "text_lines", None) or []

        return "\n".join(line.text for line in lines)
