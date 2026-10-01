"""The animated logo.

A timeline, in order: the brackets draw, the root appears with its eyes shut, the
eyes open, the mind map grows out of it (branches draw, nodes pop), and then
highlighter-yellow pulses flow out along the branches. The eyes blink now and then.

Plain CSS keyframes inside the SVG, so it animates in a README <img> with no
script. `prefers-reduced-motion` gets the finished mark. Each element is drawn in
its final state and animated *from* a hidden one (fill-mode: backwards), which is
also what a viewer with animation off sees.

    python3 demo/logo-src/animate.py demo
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from build import STYLE, YEL, word_path  # noqa: E402

ROOT, R = (72, 132), 24
KIDS = [(136, 90), (136, 132), (136, 174)]
LEAF_X = 186

CSS = f"""
  .a{{transform-box:fill-box;transform-origin:center}}
  .draw{{stroke-dasharray:1;animation:draw var(--d,.6s) cubic-bezier(.3,.7,.2,1) var(--t,0s) backwards}}
  .pop{{animation:pop .45s cubic-bezier(.3,1.5,.5,1) var(--t,0s) backwards}}
  .fade{{animation:fade .5s ease-out var(--t,0s) backwards}}
  .slide-l{{animation:slide-l .6s cubic-bezier(.3,.7,.2,1) var(--t,0s) backwards}}
  .slide-r{{animation:slide-r .6s cubic-bezier(.3,.7,.2,1) var(--t,0s) backwards}}
  .eyes-open{{animation:open .35s cubic-bezier(.3,1.4,.5,1) 1.15s backwards}}
  .blink{{animation:blink 5.5s ease-in-out 4.6s infinite}}
  .glance{{animation:glance 5.5s ease-in-out 4.6s infinite}}
  .flow{{stroke:{YEL};fill:none;stroke-linecap:round;stroke-dasharray:.07 1.4;animation:flow 3s linear var(--t,0s) infinite;opacity:0}}
  .flow.leafy{{stroke-width:3.4}}
  @keyframes draw{{from{{stroke-dashoffset:1}}to{{stroke-dashoffset:0}}}}
  @keyframes pop{{from{{transform:scale(0)}}to{{transform:scale(1)}}}}
  @keyframes fade{{from{{opacity:0}}}}
  @keyframes slide-l{{from{{opacity:0;transform:translateX(-26px)}}}}
  @keyframes slide-r{{from{{opacity:0;transform:translateX(26px)}}}}
  @keyframes open{{from{{transform:scaleY(.06)}}to{{transform:scaleY(1)}}}}
  @keyframes blink{{0%,90%,100%{{transform:scaleY(1)}}94%{{transform:scaleY(.08)}}}}
  @keyframes glance{{0%,38%,100%{{transform:translateX(0)}}46%,70%{{transform:translateX(-2.6px)}}}}
  @keyframes flow{{0%{{stroke-dashoffset:.07;opacity:1}}45%{{stroke-dashoffset:-1;opacity:1}}46%,100%{{stroke-dashoffset:-1;opacity:0}}}}
  @media (prefers-reduced-motion: reduce){{ *{{animation:none!important}} .flow{{display:none}} }}
"""


def mark():
    def d_root(k):  # root -> child branch
        x, y = k
        return f"M{ROOT[0]+R-4} {ROOT[1]} C{ROOT[0]+R+22} {ROOT[1]} {x-22} {y} {x} {y}"

    def d_leaf(k, ly):  # child -> leaf link
        x, y = k
        return f"M{x} {y} C{x+20} {y} {LEAF_X-18} {ly} {LEAF_X} {ly}"

    o = [
        '<defs><linearGradient id="gb" x1="0" x2="1"><stop offset="0" class="b1"/><stop offset="1" class="b2"/></linearGradient></defs>'
    ]
    # the highlighter strips slide in from the sides
    o.append(
        '<g class="slide-l" style="--t:.5s"><rect x="40" y="176" width="64" height="18" rx="9" fill="url(#gb)" opacity=".85"/></g>'
    )
    o.append(
        '<g class="slide-r" style="--t:.5s"><rect x="136" y="46" width="64" height="18" rx="9" class="yel ystrip"/></g>'
    )
    # brackets draw themselves
    o.append(
        '<g class="ind-s" stroke-width="16" stroke-linecap="round" stroke-linejoin="round" fill="none">'
        '<path class="draw" pathLength="1" style="--d:.8s" d="M80 30 H30 V210 H80"/>'
        '<path class="draw" pathLength="1" style="--d:.8s" d="M160 30 H210 V210 H160"/></g>'
    )
    # branches (root -> child)
    o.append('<g class="ind-s" stroke-width="7" stroke-linecap="round" fill="none">')
    for i, k in enumerate(KIDS):
        o.append(f'<path class="draw" pathLength="1" style="--t:{1.55+i*.12:.2f}s;--d:.5s" d="{d_root(k)}"/>')
    o.append("</g>")
    # leaf links and leaves
    leaves = []
    o.append('<g class="lt-s" stroke-width="5" stroke-linecap="round" fill="none">')
    for i, k in enumerate(KIDS):
        for j, dy in enumerate((-11, 11)):
            ly = k[1] + dy
            leaves.append((i, j, LEAF_X, ly))
            o.append(
                f'<path class="draw" pathLength="1" style="--t:{2.15+i*.12+j*.06:.2f}s;--d:.4s" d="{d_leaf(k, ly)}"/>'
            )
    o.append("</g>")
    for i, j, lx, ly in leaves:
        o.append(f'<circle class="a pop lt" style="--t:{2.45+i*.12+j*.06:.2f}s" cx="{lx}" cy="{ly}" r="6"/>')
    for i, (x, y) in enumerate(KIDS):
        o.append(f'<circle class="a pop ind" style="--t:{1.95+i*.12:.2f}s" cx="{x}" cy="{y}" r="11"/>')
    # yellow pulses flowing out along the branches, then on to the leaves
    o.append("<g>")
    for i, k in enumerate(KIDS):
        o.append(f'<path class="flow" pathLength="1" style="--t:{3.2+i*.35:.2f}s" stroke-width="4.5" d="{d_root(k)}"/>')
    for i, k in enumerate(KIDS):
        for j, dy in enumerate((-11, 11)):
            o.append(
                f'<path class="flow leafy" pathLength="1" style="--t:{3.75+i*.35+j*.1:.2f}s" d="{d_leaf(k, k[1]+dy)}"/>'
            )
    o.append("</g>")
    # the root, and its eyes
    o.append(f'<circle class="a pop ind" style="--t:.9s" cx="{ROOT[0]}" cy="{ROOT[1]}" r="{R}"/>')
    ey = ROOT[1] - 3
    o.append('<g class="a eyes-open"><g class="a blink">')
    for ex in (ROOT[0] - 9, ROOT[0] + 9):
        o.append(f'<circle cx="{ex}" cy="{ey}" r="8.5" fill="#fff"/>')
    o.append('<g class="glance">')
    for ex in (ROOT[0] - 9, ROOT[0] + 9):
        o.append(f'<circle cx="{ex+3}" cy="{ey}" r="4.6" fill="#1f2a6b"/>')
        o.append(f'<circle cx="{ex+4.6}" cy="{ey-2}" r="1.6" fill="#fff"/>')
    o.append("</g></g></g>")
    return "\n  ".join(o)


def svg(lockup):
    style = STYLE.replace("</style>", CSS + "</style>")
    if not lockup:
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">\n  {style}\n  {mark()}\n</svg>\n'
    d, w = word_path("NotebookLLM", 78, tracking=-1)
    tx, base = 270, 142
    mx = tx + w + 8
    total = int(mx + 52 + 20)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} 240" role="img" aria-label="NotebookLLM minus">\n  {style}\n'
        f"  <g>{mark()}</g>\n"
        f'  <path class="txt slide-r" style="--t:1.3s" transform="translate({tx} {base})" d="{d}"/>\n'
        f'  <rect class="a pop yel" style="--t:2.3s" x="{mx:.0f}" y="{base-64}" width="40" height="12" rx="6"/>\n</svg>\n'
    )


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "demo"
    open(f"{out}/logo-animated.svg", "w").write(svg(True))
    open(f"{out}/logo-mark-animated.svg", "w").write(svg(False))
