"""Where things are, and making this repo's own code importable.

Every other module here needs `src/` on `sys.path` before it can reach
`arabic_extraction.base` / `arabic_extraction.registry` — this repo's own
extractors, not a third-party package. Centralised here, as an import-time
side effect, so every sibling module can just `import paths` first and the
rest of its imports work, rather than repeating the same `sys.path.insert` in
five places.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This file lives at <repo>/fineTuning/genrateData/paths.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FINE_TUNING = REPO_ROOT / "fineTuning"
DEFAULT_ASSETS = FINE_TUNING / "assets"
DEFAULT_OUT = FINE_TUNING / "data"
DEFAULT_TESSDATA_HINT = FINE_TUNING / "tessdata_best"

DEFAULT_MIN_CHARS = 200
