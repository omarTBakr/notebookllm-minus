"""Turn the raw recording into demo/walkthrough.mp4 and demo/walkthrough.gif.

    uv run --project evals --group demo python demo/record/render.py

Reads out/walkthrough.webm and out/timeline.json (written by walkthrough.py).
Every stretch the timeline marks as "wait" (a model answering, a worker
indexing) is played fast, so the video shows the work without making a viewer
sit through it. The caption on screen during those stretches already says
"fast-forwarded": it was drawn in the page while recording.

Needs ffmpeg. Nothing else: the cut list is plain arithmetic.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
DEMO = HERE.parent

#: A wait is compressed to about this many seconds, whatever its length, but
#: never sped up more than MAX_SPEED (beyond that the page just flickers).
WAIT_TARGET_S = 2.5
MAX_SPEED = 40.0
#: Waits shorter than this are left alone: speeding them up saves nothing.
MIN_WAIT_S = 4.0


def cut_list(duration: float, segments: list[dict]) -> list[tuple[float, float, float]]:
    """``(start, end, speed)`` covering ``[0, duration]`` with no gap or overlap."""
    waits = sorted(
        (max(0.0, s["start"]), min(duration, s["end"]))
        for s in segments
        if s["kind"] == "wait" and s["end"] - s["start"] >= MIN_WAIT_S
    )

    cuts: list[tuple[float, float, float]] = []
    cursor = 0.0

    for start, end in waits:
        if start < cursor:  # overlapping waits: keep the part not yet covered
            start = cursor
        if end <= start:
            continue
        if start > cursor:
            cuts.append((cursor, start, 1.0))
        cuts.append((start, end, min(MAX_SPEED, max(1.0, (end - start) / WAIT_TARGET_S))))
        cursor = end

    if cursor < duration:
        cuts.append((cursor, duration, 1.0))

    return cuts


def filter_graph(cuts: list[tuple[float, float, float]]) -> str:
    parts = []
    for index, (start, end, speed) in enumerate(cuts):
        parts.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}[v{index}]")
    joined = "".join(f"[v{index}]" for index in range(len(cuts)))
    parts.append(f"{joined}concat=n={len(cuts)}:v=1:a=0[out]")
    return ";".join(parts)


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{result.stderr[-2000:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gif-width", type=int, default=960)
    parser.add_argument("--gif-fps", type=int, default=8)
    parser.add_argument("--to", type=Path, default=DEMO, help="where walkthrough.mp4 and .gif are written")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not installed")

    raw, timeline_file = OUT / "walkthrough.webm", OUT / "timeline.json"
    if not raw.is_file() or not timeline_file.is_file():
        sys.exit("Run walkthrough.py first: out/walkthrough.webm or out/timeline.json is missing")

    timeline = json.loads(timeline_file.read_text())
    cuts = cut_list(timeline["duration"], timeline["segments"])
    final = sum((end - start) / speed for start, end, speed in cuts)
    print(
        f"{timeline['duration']:.0f}s recorded -> {final:.0f}s after fast-forwarding {sum(s > 1 for *_, s in cuts)} wait(s)"
    )

    args.to.mkdir(parents=True, exist_ok=True)
    mp4, gif = args.to / "walkthrough.mp4", args.to / "walkthrough.gif"

    run(
        ["ffmpeg", "-y", "-i", str(raw), "-filter_complex", filter_graph(cuts), "-map", "[out]"]
        + ["-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p"]
        + ["-movflags", "+faststart", str(mp4)]
    )

    # Two passes: a palette made from the video itself, then the GIF drawn
    # with it. One pass with the default palette bands every gradient.
    palette = OUT / "palette.png"
    scale = f"fps={args.gif_fps},scale={args.gif_width}:-1:flags=lanczos"
    run(["ffmpeg", "-y", "-i", str(mp4), "-vf", f"{scale},palettegen=stats_mode=diff", str(palette)])
    run(
        ["ffmpeg", "-y", "-i", str(mp4), "-i", str(palette)]
        + ["-lavfi", f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4", str(gif)]
    )

    for path in (mp4, gif):
        print(f"  {path}  {path.stat().st_size / 1_000_000:.1f} MB")
    for name, reason in timeline.get("skipped", []):
        print(f"  not in the video: {name} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
