"""Draw the README's architecture figure: from the operator's job to the crew brief and the alerts.

    python tools/architecture_figure.py          # from the repository folder; needs Pillow and the Inter font
    python tools/architecture_figure.py --out DIR   # write somewhere else, to compare before replacing

Writes docs/architecture.svg (light), docs/architecture-dark.svg, and, when a Chromium
browser is found (CHROMIUM, or the Playwright browsers folder), renders both to
docs/architecture.png and docs/architecture-dark.png at twice the pixels.

Every line of text is measured with the Inter font before it is placed, and the script
stops if a line would not fit its box, so no label runs over its border.
"""

from __future__ import annotations

import glob
import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
FONT_DIRS = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/inter", "/Library/Fonts",
             str(Path.home() / ".fonts"), str(Path.home() / ".local/share/fonts")]
WEIGHTS = {400: "Inter-Regular", 500: "Inter-Medium", 600: "Inter-SemiBold", 700: "Inter-Bold"}

W = 1400
PAD = 14            # inner padding of a box, each side

THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#6f6e69", "line": "#52514e",
        "panel": "#f3f2ee", "panel_edge": "#e1e0d9", "box": "#ffffff", "box_edge": "#a9a8a0",
        "ai": "#76b900", "ai_fill": "#eef6dc", "ai_ink": "#2f4f00",
        "safe": "#d97706", "safe_fill": "#fff4e5", "safe_ink": "#8a4500",
        "out": "#2a78d6", "out_fill": "#eaf2fc", "out_ink": "#164a8a",
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#a3a29a", "line": "#a3a29a",
        "panel": "#232322", "panel_edge": "#383835", "box": "#2a2a28", "box_edge": "#5a5955",
        "ai": "#8fd400", "ai_fill": "#26331a", "ai_ink": "#c4ec7a",
        "safe": "#f59e0b", "safe_fill": "#3a2a12", "safe_ink": "#fcd08a",
        "out": "#3987e5", "out_fill": "#16263b", "out_ink": "#a9cdf7",
    },
}


def font_path(weight: int) -> str:
    name = WEIGHTS[weight]
    for d in FONT_DIRS:
        for ext in (".otf", ".ttf"):
            p = Path(d) / (name + ext)
            if p.exists():
                return str(p)
    sys.exit(f"Inter font ({name}) not found; install the Inter font family and run again")


_FONTS: dict = {}


def text_width(s: str, size: float, weight: int = 400) -> float:
    key = (size, weight)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(font_path(weight), size)
    return _FONTS[key].getlength(s)


def wrap(s: str, size: float, weight: int, width: float) -> list[str]:
    words, lines, cur = s.split(" "), [], ""
    for w in words:
        trial = w if not cur else cur + " " + w
        if text_width(trial, size, weight) <= width:
            cur = trial
        else:
            if not cur:
                raise SystemExit(f"word does not fit: {w!r}")
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


class Svg:
    def __init__(self, t: dict):
        self.t = t
        self.parts: list[str] = []

    def add(self, s: str):
        self.parts.append(s)

    def text(self, x, y, s, size, weight=400, fill=None, anchor="start", spacing=0, max_w=None):
        w = text_width(s, size, weight) + spacing * max(len(s) - 1, 0)
        if max_w is not None and w > max_w + 0.5:
            raise SystemExit(f"text overflows by {w - max_w:.1f}px: {s!r}")
        ls = f' letter-spacing="{spacing}"' if spacing else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
                 f'text-anchor="{anchor}" fill="{fill or self.t["ink"]}"{ls}>{html.escape(s)}</text>')
        return w

    def rect(self, x, y, w, h, fill, stroke, sw=2, rx=10, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def path(self, d, color, marker=True, dash=None, sw=2.5):
        m = ' marker-end="url(#arrow)"' if marker else ""
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
                 f'stroke-linejoin="round"{da}{m}/>')

    def render(self) -> str:
        t = self.t
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                f'font-family="Inter, Helvetica, Arial, sans-serif">\n'
                f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{t["line"]}"/></marker></defs>\n'
                f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>\n')
        return head + "\n".join(self.parts) + "\n</svg>\n"


# ------------------------------------------------------------------------- layout
# Four columns read left to right. The first runs down, the second up, the third down,
# so most hand-overs between columns are one straight arrow. The second column holds five
# boxes in the height of the others' four.
M = 36                       # outer margin
TOP = 146                    # top of the column panels
COL_W, COL_GAP = 302, 38
ROW_Y0, ROW_H, ROW_GAP = TOP + 76, 152, 34
COLS = [M + i * (COL_W + COL_GAP) for i in range(4)]
ROWS = [ROW_Y0 + i * (ROW_H + ROW_GAP) for i in range(4)]
FLIGHT_H = (4 * ROW_H - ROW_GAP) / 5          # five boxes in the In flight column
FLIGHT_ROWS = [ROW_Y0 + i * (FLIGHT_H + ROW_GAP) for i in range(5)]
PANEL_BOTTOM = ROWS[3] + ROW_H + 22
H = PANEL_BOTTOM + 140
BOX_INSET = 18
BOX_W = COL_W - 2 * BOX_INSET
BODY = 17                    # box text size; the figure shows at about 60% in a README column


def rows_of(col: int) -> tuple[list[float], float]:
    return (FLIGHT_ROWS, FLIGHT_H) if col == 1 else (ROWS, ROW_H)


def box(svg: Svg, col: int, row: int, title: str, lines: list[str], kind: str = "code", tag: str | None = None):
    """One box. A tag is a small Nemotron-green label on the box's bottom edge, clear of the arrows."""
    t = svg.t
    rows, rh = rows_of(col)
    x, y = COLS[col] + BOX_INSET, rows[row]
    fits = int((rh - 68) // 22) + 1              # body lines that fit under the title
    fill, edge, ink = {"code": (t["box"], t["box_edge"], t["ink"]),
                       "ai": (t["ai_fill"], t["ai"], t["ai_ink"]),
                       "safe": (t["safe_fill"], t["safe"], t["safe_ink"]),
                       "out": (t["out_fill"], t["out"], t["out_ink"])}[kind]
    svg.rect(x, y, BOX_W, rh, fill, edge, sw=2.5 if kind != "code" else 2)
    inner = BOX_W - 2 * PAD
    svg.text(x + PAD, y + 30, title, 18.5, 700 if kind != "code" else 600, ink, max_w=inner)
    body = []
    for ln in lines:
        body += wrap(ln, BODY, 400, inner)
    if len(body) > fits:
        raise SystemExit(f"box {title!r} needs {len(body)} lines; it holds {fits}")
    if len(body) > 1 and " " not in body[-1]:
        raise SystemExit(f"box {title!r} ends on one word ({body[-1]!r}); reword it")
    for i, ln in enumerate(body):
        svg.text(x + PAD, y + 56 + i * 22, ln, BODY, 400, t["ink2"], max_w=inner)
    if tag:
        tw = text_width(tag, 12, 700) + 16
        svg.rect(x + BOX_W - tw - 10, y + rh - 11, tw, 22, t["ai"], t["ai"], sw=1, rx=11)
        svg.text(x + BOX_W - tw / 2 - 10, y + rh + 4.5, tag, 12, 700, "#0b0b0b", anchor="middle")
    return x, y


def draw(theme: str) -> str:
    t = THEMES[theme]
    svg = Svg(t)
    # title
    svg.text(M, 56, "How Halo turns a lightning strike into a crew brief and an alert", 30, 700, max_w=W - 2 * M)
    svg.text(M, 88, "NVIDIA Nemotron reads the job before take-off and, when a check team flies, picks the strike it "
             "looks at next.", 17, 400, t["ink2"], max_w=W - 2 * M)
    svg.text(M, 112, "Code makes the plan, flies the drones and runs the station: no language model flies a drone or "
             "decides an alert.", 17, 400, t["ink2"], max_w=W - 2 * M)

    heads = [("BEFORE TAKE-OFF", "Once per job"), ("IN FLIGHT", "The drones and their supervisor"),
             ("GROUND STATION", "Once a second, no language model"), ("WHO GETS WHAT", "As soon as the station knows")]
    for i, (hd, sub) in enumerate(heads):
        svg.rect(COLS[i], TOP, COL_W, PANEL_BOTTOM - TOP, t["panel"], t["panel_edge"], sw=1.5, rx=14)
        svg.text(COLS[i] + BOX_INSET, TOP + 30, hd, 14, 700, t["ink"], spacing=1.4, max_w=COL_W - 2 * BOX_INSET)
        svg.text(COLS[i] + BOX_INSET, TOP + 54, sub, 15, 400, t["ink2"], max_w=COL_W - 2 * BOX_INSET)

    # column 1, top to bottom
    box(svg, 0, 0, "Operator", ["Types the job in plain words: \u201ccheck last night\u2019s strikes on the "
                                "valley floor\u201d"])
    box(svg, 0, 1, "Nemotron valley compiler", ["Nemotron 3 Nano on Nebius Token Factory reads the drones, "
                                                "areas and strikes the job means, or refuses it"], kind="ai")
    box(svg, 0, 2, "Plan check", ["Code picks the strikes and the squads, within drone and battery limits"],
        kind="safe")
    box(svg, 0, 3, "Patrol plan", ["Squads, their areas, the strikes to look at first, and how long they fly"])

    # column 2, bottom to top: the strike picker joins the chain at the validator
    box(svg, 1, 4, "Strike picker, optional", ["For a 4-drone check team, Nemotron picks the next strike; a rule "
                                               "did as well"], kind="ai")
    box(svg, 1, 3, "Supervisor, every 5 s", ["In the valley, hand-written rules set the formation"],
        tag="NEMOTRON IN THE LAB")
    box(svg, 1, 2, "Safety validator", ["Checks every order and strike pick, the operator\u2019s too: clamps or "
                                        "rejects it"], kind="safe")
    box(svg, 1, 1, "Controller, 50 Hz", ["A safety filter keeps each drone clear of the others, obstacles "
                                         "and the ground"], kind="safe")
    box(svg, 1, 0, "Drones", ["Cameras look down for heat and ahead for smoke; each drone measures the wind"])

    # column 3, top to bottom
    box(svg, 2, 0, "Locate fires", ["Places fires from thermal finds and smoke bearings, and maps the burning ground"])
    box(svg, 2, 1, "Spread forecast", ["A Rothermel ensemble on fuel, slope and the wind, every 2 min, "
                                       "sooner when a fire is new or grows"])
    box(svg, 2, 2, "Crew brief", ["Where the fire is and which way its head will run, with a cone of "
                                  "likely directions"])
    box(svg, 2, 3, "Alert check", ["Fixed rules set each zone\u2019s alert level; each alert is checked "
                                   "first"], kind="safe")

    # column 4
    box(svg, 3, 0, "Fire dispatch", ["Under the pre-signed rules a typed job uses, a card for every fire the "
                                     "station locates"], kind="out")
    box(svg, 3, 1, "Operator", ["Sees every card, brief and alert; can cancel an alert, give the all-clear "
                                "or hold the swarm low for aircraft"], kind="out")
    box(svg, 3, 2, "Fire crews", ["The brief, a map file, and an update whenever the forecast changes"], kind="out")
    box(svg, 3, 3, "Phones in the path", ["Zone alerts, from prepare to emergency; simulated phones only"],
        kind="out")

    # arrows inside columns
    bx = lambda c: COLS[c] + COL_W / 2
    for r in range(3):
        svg.path(f"M {bx(0)} {ROWS[r] + ROW_H} L {bx(0)} {ROWS[r + 1] - 2}", t["line"])
        svg.path(f"M {bx(1)} {FLIGHT_ROWS[r + 1]} L {bx(1)} {FLIGHT_ROWS[r] + FLIGHT_H + 2}", t["line"])
    # strike picker to the validator: up the right edge of column 2, then into the box from below
    rx, ex2 = COLS[1] + COL_W - 9, COLS[1] + COL_W - BOX_INSET - 40
    svg.path(f"M {COLS[1] + COL_W - BOX_INSET} {FLIGHT_ROWS[4] + 28} L {rx} {FLIGHT_ROWS[4] + 28} "
             f"L {rx} {FLIGHT_ROWS[3] - 10} L {ex2} {FLIGHT_ROWS[3] - 10} L {ex2} {FLIGHT_ROWS[2] + FLIGHT_H + 2}",
             t["line"])
    svg.path(f"M {bx(2)} {ROWS[0] + ROW_H} L {bx(2)} {ROWS[1] - 2}", t["line"])
    svg.path(f"M {bx(2)} {ROWS[1] + ROW_H} L {bx(2)} {ROWS[2] - 2}", t["line"])
    # forecast to alert check: down the left edge of column 3, then into the box from above
    lx, ex = COLS[2] + 9, COLS[2] + BOX_INSET + 40
    svg.path(f"M {COLS[2] + BOX_INSET} {ROWS[1] + ROW_H - 26} L {lx} {ROWS[1] + ROW_H - 26} "
             f"L {lx} {ROWS[3] - 24} L {ex} {ROWS[3] - 24} L {ex} {ROWS[3] - 2}", t["line"])
    # hand-overs between columns
    def across(c, r, y=None):
        x1, x2 = COLS[c] + COL_W - BOX_INSET, COLS[c + 1] + BOX_INSET - 2
        y = ROWS[r] + ROW_H / 2 if y is None else y
        svg.path(f"M {x1} {y} L {x2} {y}", t["line"])
    # the patrol plan feeds the supervisor (an elbow up through the gap) and the strike picker (straight across)
    gx = (COLS[0] + COL_W + COLS[1]) / 2
    svg.path(f"M {COLS[0] + COL_W - BOX_INSET} {ROWS[3] + 22} L {gx} {ROWS[3] + 22} "
             f"L {gx} {FLIGHT_ROWS[3] + FLIGHT_H / 2} L {COLS[1] + BOX_INSET - 2} {FLIGHT_ROWS[3] + FLIGHT_H / 2}",
             t["line"])
    across(0, 3, y=FLIGHT_ROWS[4] + FLIGHT_H / 2)
    across(1, 0, y=FLIGHT_ROWS[0] + FLIGHT_H / 2)
    across(2, 0)
    across(2, 1)
    across(2, 2)
    across(2, 3)

    # legend and footnote
    ly = PANEL_BOTTOM + 40
    x = M
    for kind, label in (("ai", "NVIDIA Nemotron on Nebius Token Factory"), ("safe", "Safety check"),
                        ("code", "No language model"), ("out", "Who receives it")):
        fill, edge = {"ai": (t["ai_fill"], t["ai"]), "safe": (t["safe_fill"], t["safe"]),
                      "code": (t["box"], t["box_edge"]), "out": (t["out_fill"], t["out"])}[kind]
        svg.rect(x, ly - 15, 26, 20, fill, edge, sw=2, rx=5)
        x += 36 + svg.text(x + 36, ly, label, 16, 500, t["ink2"]) + 30
    svg.text(M, ly + 38, "Without a key, recorded jobs replay Nemotron\u2019s reading and new ones go to hand-written "
             "rules, as when the link drops; flight, the station and the alerts carry on.", 16, 400, t["muted"],
             max_w=W - 2 * M)
    svg.text(M, ly + 62, "A check team flies only in the strike-check measurement, not in a typed job; if a Nemotron "
             "pick fails, the team keeps patrolling its square.", 16, 400, t["muted"], max_w=W - 2 * M)
    return svg.render()


def chromium() -> str | None:
    env = os.environ.get("CHROMIUM")
    if env:
        return env
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux*/chrome",
                str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome")):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    for name in ("chromium", "chromium-browser", "google-chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def main():
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else DOCS
    out.mkdir(parents=True, exist_ok=True)
    outputs = {"light": out / "architecture.svg", "dark": out / "architecture-dark.svg"}
    for theme, path in outputs.items():
        path.write_text(draw(theme))
        print("wrote", path)
    exe = chromium()
    if not exe:
        print("no Chromium found: set CHROMIUM to render the PNGs")
        return
    for theme, path in outputs.items():
        png = path.with_suffix(".png")
        with tempfile.TemporaryDirectory() as tmp:
            # the browser's window is taller than the page it shows, so render tall and crop
            subprocess.run([exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                            f"--user-data-dir={tmp}", "--force-device-scale-factor=2", f"--window-size={W},{H + 200}",
                            f"--screenshot={png}", path.resolve().as_uri()],
                           check=True, capture_output=True)
        with Image.open(png) as im:
            # a flat diagram needs no more than 256 colours, which keeps the file small
            im = im.crop((0, 0, 2 * W, 2 * H)).convert("RGB")
            im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(png, optimize=True)
        print("wrote", png)


if __name__ == "__main__":
    main()
