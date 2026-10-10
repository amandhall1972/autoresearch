"""
RAAS Capital brand layer for generated documents (reportlab + matplotlib).

Everything here follows the RAAS Capital Brand Identity Kit (v1.0 tokens, v2.0 "neela-white" primary
expression, September 2026):

* palette   - Neela (navy -> sky), Kagaz (paper), Syahi (ink), Safed (white), Kesari (the one accent);
              gains green and losses red, and nothing else is.
* type      - Outfit for headlines and large numbers (sentence case, never below 12 pt), IBM Plex Sans for
              text (10.5 pt, 1.5 line height, never justified), IBM Plex Mono for data (tabular figures,
              true minus, Indian digit grouping for rupees).
* logo      - the primary lockup (Anant ribbon + Twin Apex wordmark). `full-colour` (lifted ribbon Neela
              600->100, white wordmark) is the logo on Neela 900; `full-colour-light` (full ribbon Neela
              900->100, ink wordmark) is the logo on paper. Both are derived from the kit's master SVG by
              the kit's own colour tables; the geometry is never touched.

Brand assets live in `brand/` next to this file: `brand/fonts/*.ttf` (SIL OFL 1.1, see brand/fonts/OFL.txt)
and `brand/logo/raas-capital-primary-neela-ink.svg` (from the "RAAS Capital - Brand Identity Kit" Drive folder).
"""
import copy
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND_DIR = os.environ.get("RAAS_BRAND_DIR", os.path.join(HERE, "brand"))
FONT_DIR = os.path.join(BRAND_DIR, "fonts")
LOGO_SVG = os.path.join(BRAND_DIR, "logo", "raas-capital-primary-neela-ink.svg")

# --------------------------------------------------------------------------------------------------
# Colour (brand.css / palette-v2.json)
# --------------------------------------------------------------------------------------------------
NEELA = {900: "#101F58", 800: "#1E3269", 700: "#2E457A", 600: "#3F598C", 500: "#506D9E",
         400: "#6381B0", 300: "#7696C2", 200: "#94B6DD", 100: "#A8CCF0"}
KAGAZ = {50: "#FAF8F3", 100: "#F4F1E9", 200: "#E9E5DA", 300: "#D8D3C5"}
SYAHI = {100: "#101B29", 80: "#3B4453", 60: "#5F6979", 40: "#8A93A1", 20: "#C3C9D2"}
KESARI = {100: "#D07018", 60: "#E9A15A", 20: "#F8E3CB"}
SAFED = "#FFFFFF"
GAIN, LOSS = "#1F7A4D", "#B3261E"
GAIN_ON_NAVY, LOSS_ON_NAVY = "#5FCB8F", "#F08A7E"

RIBBON_FULL = ["#101F58", "#172961", "#1E3269", "#263C72", "#2E457A", "#364F83", "#3F598C", "#476395", "#506D9E",
               "#5977A7", "#6381B0", "#6C8CB9", "#7696C2", "#80A1CB", "#8AABD4", "#94B6DD", "#9EC1E7", "#A8CCF0"]
RIBBON_LIFTED = ["#3F598C", "#455F92", "#4A6697", "#506C9D", "#5673A3", "#5C79A9", "#6280AF", "#6887B4", "#6E8DBA",
                 "#7494C0", "#7B9BC6", "#81A2CC", "#87A9D2", "#8EB0D8", "#94B7DE", "#9BBEE4", "#A1C5EA", "#A8CCF0"]
WORDMARK_INK = "#101B29"

# chart series order (brand.css --raas-chart-1..8); Kesari (4, 6) is the one-per-view accent
CHART = ["#101F58", "#506D9E", "#94B6DD", "#D07018", "#5F6979", "#E9A15A", "#2E457A", "#C3C9D2"]

TAGLINE = "Research is our raas — edge before exposure."

# --------------------------------------------------------------------------------------------------
# Type
# --------------------------------------------------------------------------------------------------
FONT_FILES = {
    "Outfit-Light": "Outfit-Light.ttf", "Outfit": "Outfit-Regular.ttf", "Outfit-Medium": "Outfit-Medium.ttf",
    "Outfit-SemiBold": "Outfit-SemiBold.ttf", "Outfit-Bold": "Outfit-Bold.ttf",
    "Plex": "IBMPlexSans-Regular.ttf", "Plex-Italic": "IBMPlexSans-Italic.ttf",
    "Plex-Medium": "IBMPlexSans-Medium.ttf", "Plex-SemiBold": "IBMPlexSans-SemiBold.ttf",
    "PlexMono": "IBMPlexMono-Regular.ttf", "PlexMono-Medium": "IBMPlexMono-Medium.ttf",
    "PlexMono-SemiBold": "IBMPlexMono-SemiBold.ttf",
}
MINUS = "−"


def check_assets():
    """Raise a clear error when the brand assets are missing (they are not part of the public repo)."""
    missing = [f for f in FONT_FILES.values() if not os.path.exists(os.path.join(FONT_DIR, f))]
    if not os.path.exists(LOGO_SVG):
        missing.append(os.path.relpath(LOGO_SVG, BRAND_DIR))
    if missing:
        raise FileNotFoundError(
            f"RAAS brand assets missing under {BRAND_DIR}: {', '.join(missing)}. Copy the fonts (Outfit, IBM Plex "
            "Sans, IBM Plex Mono static TTFs) into brand/fonts/ and the primary neela-ink logo SVG from the "
            "'RAAS Capital - Brand Identity Kit' Drive folder into brand/logo/, or point RAAS_BRAND_DIR at a copy.")


_RL_DONE = False


def register_reportlab_fonts():
    """Register the brand TTFs with reportlab (idempotent) and the bold/italic family mappings."""
    global _RL_DONE
    if _RL_DONE:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping
    for name, f in FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, f)))
    addMapping("Plex", 0, 0, "Plex"); addMapping("Plex", 1, 0, "Plex-SemiBold")
    addMapping("Plex", 0, 1, "Plex-Italic"); addMapping("Plex", 1, 1, "Plex-SemiBold")
    addMapping("PlexMono", 0, 0, "PlexMono"); addMapping("PlexMono", 1, 0, "PlexMono-SemiBold")
    addMapping("PlexMono", 0, 1, "PlexMono"); addMapping("PlexMono", 1, 1, "PlexMono-SemiBold")
    addMapping("Outfit-Medium", 0, 0, "Outfit-Medium"); addMapping("Outfit-Medium", 1, 0, "Outfit-SemiBold")
    addMapping("Outfit-Medium", 0, 1, "Outfit-Medium"); addMapping("Outfit-Medium", 1, 1, "Outfit-SemiBold")
    _RL_DONE = True


def register_matplotlib_fonts():
    from matplotlib import font_manager
    for f in FONT_FILES.values():
        font_manager.fontManager.addfont(os.path.join(FONT_DIR, f))


def mpl_rc():
    """rcParams for brand charts: Plex Sans labels, Plex Mono ticks, paper-and-ink colours, text as paths."""
    return {
        "font.family": "IBM Plex Sans", "font.size": 7.5,
        "axes.edgecolor": KAGAZ[300], "axes.labelcolor": SYAHI[60], "axes.linewidth": 0.6,
        "axes.facecolor": SAFED, "figure.facecolor": SAFED, "savefig.facecolor": SAFED,
        "axes.grid": True, "grid.color": KAGAZ[200], "grid.linewidth": 0.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.color": SYAHI[60], "ytick.color": SYAHI[60], "xtick.labelsize": 7, "ytick.labelsize": 7,
        "xtick.major.size": 2.5, "ytick.major.size": 0, "xtick.major.width": 0.6,
        "legend.frameon": False, "legend.fontsize": 7.5, "axes.titlesize": 8.5, "axes.titleweight": "medium",
        "axes.unicode_minus": True, "svg.fonttype": "path", "pdf.fonttype": 42,
        "axes.prop_cycle": __import__("cycler").cycler(color=CHART),
    }


# --------------------------------------------------------------------------------------------------
# Numbers: true minus, tabular mono, Indian grouping
# --------------------------------------------------------------------------------------------------
def num(x, dp=2, pct=False, sign=False):
    """Format a number with a true minus (U+2212); pct multiplies by 100 and appends %."""
    if x is None or (isinstance(x, float) and x != x):
        return "–"
    v = x * 100 if pct else x
    s = f"{abs(v):,.{dp}f}"
    if v < 0 and float(s.replace(",", "")) != 0:
        s = MINUS + s
    elif sign and v > 0:
        s = "+" + s
    return s + ("%" if pct else "")


def inr(x, dp=0):
    """Rupees with Indian digit grouping: 1234567 -> 12,34,567."""
    neg = x < 0
    s = f"{abs(x):.{dp}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = "₹" + whole + ("." + frac if frac else "")
    return (MINUS if neg else "") + out


# --------------------------------------------------------------------------------------------------
# Logo
# --------------------------------------------------------------------------------------------------
COLOURWAYS = ("full-colour", "full-colour-light", "white", "black")


def logo_svg(colourway="full-colour"):
    """The primary lockup SVG text in one of the kit's colourways (geometry untouched).

    full-colour        lifted ribbon (Neela 600 -> 100) + white wordmark   - the logo, on Neela 900
    full-colour-light  full ribbon (Neela 900 -> 100) + ink wordmark      - on paper / white (the master)
    white / black      single-colour versions for one-colour processes
    """
    if colourway not in COLOURWAYS:
        raise ValueError(f"unknown colourway {colourway!r}; one of {COLOURWAYS}")
    svg = open(LOGO_SVG, encoding="utf-8").read()
    strokes = re.findall(r'stroke="(#[0-9A-Fa-f]{6})"', svg)
    if [s.upper() for s in strokes] != RIBBON_FULL:
        raise ValueError("logo master does not carry the 18-line full ribbon in order; refusing to recolour")
    if colourway == "full-colour-light":
        return svg
    it = iter({"full-colour": RIBBON_LIFTED, "white": [SAFED] * 18, "black": ["#000000"] * 18}[colourway])
    svg = re.sub(r'stroke="#[0-9A-Fa-f]{6}"', lambda m: f'stroke="{next(it)}"', svg)
    word = {"full-colour": SAFED, "white": SAFED, "black": "#000000"}[colourway]
    return svg.replace(f'fill="{WORDMARK_INK}"', f'fill="{word}"')


_LOGO_CACHE = {}


def logo_drawing(colourway="full-colour", width=60.0):
    """A reportlab Drawing of the primary lockup scaled to `width` points (aspect preserved)."""
    from svglib.svglib import svg2rlg
    import io
    if colourway not in _LOGO_CACHE:
        _LOGO_CACHE[colourway] = svg2rlg(io.BytesIO(logo_svg(colourway).encode("utf-8")))
    d = copy.deepcopy(_LOGO_CACHE[colourway])
    s = width / d.width
    d.width, d.height = d.width * s, d.height * s
    d.scale(s, s)
    return d


def min_logo_width_pt():
    """Primary lockup minimum width: 22 mm (brand rule 2)."""
    return 22.0 / 25.4 * 72.0
