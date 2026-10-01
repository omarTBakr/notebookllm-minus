import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONT = "src/presentation/web/static/fonts/IBMPlexSansArabic-700-latin.woff2"  # run from the repo root
IND, YEL, LT = "#3b5bdb", "#ffd43b", "#8da2f4"


def word_path(text, size, tracking=0):
    f = TTFont(FONT)
    gs = f.getGlyphSet()
    cmap = f.getBestCmap()
    upm = f["head"].unitsPerEm
    s = size / upm
    x = 0
    parts = []
    for ch in text:
        g = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[g].draw(TransformPen(pen, (s, 0, 0, -s, x, 0)))
        parts.append(pen.getCommands())
        x += gs[g].width * s + tracking
    return " ".join(parts), x - tracking


def mark(detail=True):
    """A mind map held in brackets, with a pair of friendly eyes at its root.

    Two highlighter strips sit behind it in the top corners -- yellow on the right,
    shades of blue on the left -- like lines of text someone has been marking up.
    """
    o = [
        '<defs><linearGradient id="gb" x1="0" x2="1"><stop offset="0" class="b1"/><stop offset="1" class="b2"/></linearGradient></defs>'
    ]
    if detail:
        o.append('<rect x="40" y="176" width="64" height="18" rx="9" fill="url(#gb)" opacity=".85"/>')
        o.append('<rect x="136" y="46" width="64" height="18" rx="9" class="yel ystrip"/>')
    o.append(
        '<g class="ind-s" stroke-width="16" stroke-linecap="round" stroke-linejoin="round" fill="none">'
        '<path d="M80 30 H30 V210 H80"/><path d="M160 30 H210 V210 H160"/></g>'
    )
    root, r = (72, 132), 24
    kids = [(136, 90), (136, 132), (136, 174)]
    o.append('<g class="ind-s" stroke-width="7" stroke-linecap="round" fill="none">')
    for x, y in kids:
        o.append(f'<path d="M{root[0]+r-4} {root[1]} C{root[0]+r+22} {root[1]} {x-22} {y} {x} {y}"/>')
    o.append("</g>")
    if detail:
        leaves = []
        o.append('<g class="lt-s" stroke-width="5" stroke-linecap="round" fill="none">')
        for x, y in kids:
            for dy in (-11, 11):
                lx, ly = 186, y + dy
                leaves.append((lx, ly))
                o.append(f'<path d="M{x} {y} C{x+20} {y} {lx-18} {ly} {lx} {ly}"/>')
        o.append("</g>")
        for x, y in leaves:
            o.append(f'<circle cx="{x}" cy="{y}" r="6" class="lt"/>')
    for x, y in kids:
        o.append(f'<circle cx="{x}" cy="{y}" r="11" class="ind"/>')
    # the root, with eyes that look along the branches
    o.append(f'<circle cx="{root[0]}" cy="{root[1]}" r="{r}" class="ind"/>')
    for ex in (root[0] - 9, root[0] + 9):
        o.append(f'<circle cx="{ex}" cy="{root[1]-3}" r="8.5" fill="#fff"/>')
        o.append(f'<circle cx="{ex+3}" cy="{root[1]-3}" r="4.6" fill="#1f2a6b"/>')
        o.append(f'<circle cx="{ex+4.6}" cy="{root[1]-5}" r="1.6" fill="#fff"/>')
    return "\n  ".join(o)


STYLE = f"""<style>
  .ind{{fill:{IND}}} .ind-s{{stroke:{IND}}} .lt{{fill:{LT}}} .lt-s{{stroke:{LT}}} .yel{{fill:{YEL}}} .ystrip{{opacity:.7}} .b1{{stop-color:#b9c6fa}} .b2{{stop-color:#5b78e8}} .txt{{fill:#1c1e26}}
  @media (prefers-color-scheme: dark){{ .ystrip{{opacity:.95}} .ind{{fill:#7089f5}} .ind-s{{stroke:#7089f5}} .lt{{fill:#9db0f7}} .lt-s{{stroke:#9db0f7}} .txt{{fill:#eceef6}} .b1{{stop-color:#a9b8f7}} .b2{{stop-color:#7089f5}} }}
</style>"""


def icon(detail=True):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">\n  {STYLE}\n  {mark(detail)}\n</svg>\n'


def lockup():
    d, w = word_path("NotebookLLM", 78, tracking=-1)
    tx = 270
    base = 142
    minus_x = tx + w + 8
    total = int(minus_x + 52 + 20)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} 240" role="img" aria-label="NotebookLLM minus">\n  {STYLE}\n'
        f"  <g>{mark(True)}</g>\n"
        f'  <path class="txt" transform="translate({tx} {base})" d="{d}"/>\n'
        f'  <rect x="{minus_x:.0f}" y="{base-64}" width="40" height="12" rx="6" class="yel"/>\n</svg>\n'
    )


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "demo"
    open(f"{out}/logo-mark.svg", "w").write(icon(True))
    open(f"{out}/logo.svg", "w").write(lockup())
    if len(sys.argv) > 2:
        open(sys.argv[2], "w").write(icon(False))  # favicon: no leaves, no corner strips
