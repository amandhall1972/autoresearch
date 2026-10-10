"""
make_pdf.py - builds the RAAS Capital research note on the RSI + EMA + VWAP strategy:

    reports/RCG-RN-REV-001_RSI-EMA-VWAP_Nifty50.pdf

A complete, self-contained document: the idea and its three indicators, the data and the backtest harness,
the research process, the final strategy rule by rule (every parameter, every formula, a cohort traced
through the backtest), the results, the robustness checks, the out-of-sample record and its diagnosis, the
operating rules for a paper trial, and the full Python source of the strategy, its indicators, the execution
engine and the live signal script. Every number is read from the repository (reports/*.json, results.tsv,
strategy.py) or recomputed from the data at build time, so the document cannot drift from the code.

    python validate.py --boot 2000 --out reports && python lineage.py --holdout && python diagnose.py
    python make_pdf.py                                   # -> reports/RCG-RN-REV-001_RSI-EMA-VWAP_Nifty50.pdf
    python make_pdf.py --date 2026-10-10 --out /tmp/note.pdf

Typeset in the RAAS Capital identity (raas_brand.py; fonts and logo in brand/, see brand/fonts/OFL.txt).
"""
import argparse
import datetime as dt
import inspect
import io
import json
import os
import re
import sys
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, NextPageTemplate, KeepTogether, Flowable, CondPageBreak)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Group
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import raas_brand as B  # noqa: E402

R = os.path.join(HERE, "reports")
DOC_ID = "RCG-RN-REV-001"
DOC_VERSION = "1.0"
DOC_TITLE = "RSI + EMA + VWAP on the Nifty 50"
DOC_SUBTITLE = "An autonomous research loop, the strategy it produced, and why we would not trade it"
DESK = "Quant Desk"
DEFAULT_OUT = os.path.join(R, f"{DOC_ID}_RSI-EMA-VWAP_Nifty50.pdf")

# ---------------------------------------------------------------------------------------------------------
# Geometry and colour
# ---------------------------------------------------------------------------------------------------------
PW, PH = A4
ML = MR = 20 * mm
MT, MB = 27 * mm, 22 * mm
FW = PW - ML - MR                      # frame width (170 mm): tables and figures
MEASURE_INDENT = 30 * mm               # body text measure 140 mm (about 70 characters a line)
C = lambda h: HexColor(h)              # noqa: E731
NAVY, PAPER, INK, SAFED = C(B.NEELA[900]), C(B.KAGAZ[100]), C(B.SYAHI[100]), C(B.SAFED)
MUTED, SUBTLE, RULE = C(B.SYAHI[60]), C(B.SYAHI[40]), C(B.KAGAZ[300])
GAIN, LOSS = C(B.GAIN), C(B.LOSS)


def esc(s):
    return escape(str(s))


def signed_colour(x, on_navy=False):
    if x is None or x != x or abs(x) < 1e-12:
        return B.SYAHI[100] if not on_navy else B.KAGAZ[100]
    if on_navy:
        return B.GAIN_ON_NAVY if x > 0 else B.LOSS_ON_NAVY
    return B.GAIN if x > 0 else B.LOSS


def snum(x, dp=2, pct=False, sign=False, colour=True, on_navy=False):
    """Signed number as Paragraph markup: mono, true minus, green/red only for gains/losses."""
    s = esc(B.num(x, dp, pct, sign))
    if colour:
        return f'<font color="{signed_colour(x, on_navy)}">{s}</font>'
    return s


# ---------------------------------------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------------------------------------
def styles():
    S = {}
    S["body"] = ParagraphStyle("body", fontName="Plex", fontSize=10.5, leading=15.75, textColor=INK,
                               alignment=TA_LEFT, rightIndent=MEASURE_INDENT, spaceAfter=7)
    S["body_full"] = ParagraphStyle("body_full", parent=S["body"], rightIndent=0)
    S["bullet"] = ParagraphStyle("bullet", parent=S["body"], leftIndent=14, bulletIndent=2, spaceAfter=4,
                                 bulletFontName="Plex", bulletColor=MUTED)
    S["num_item"] = ParagraphStyle("num_item", parent=S["bullet"], leftIndent=18, bulletIndent=0)
    S["standfirst"] = ParagraphStyle("standfirst", fontName="Outfit-Light", fontSize=14, leading=20, textColor=INK,
                                     rightIndent=MEASURE_INDENT, spaceAfter=14)
    S["part"] = ParagraphStyle("part", fontName="PlexMono", fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=4)
    S["h1"] = ParagraphStyle("h1", fontName="Outfit-Medium", fontSize=27, leading=32, textColor=NAVY, spaceAfter=10)
    S["h2"] = ParagraphStyle("h2", fontName="Outfit-Medium", fontSize=15, leading=20, textColor=NAVY,
                             spaceBefore=14, spaceAfter=5, keepWithNext=1)
    S["h3"] = ParagraphStyle("h3", fontName="Plex-SemiBold", fontSize=10.5, leading=15, textColor=INK,
                             spaceBefore=8, spaceAfter=2, keepWithNext=1, rightIndent=MEASURE_INDENT)
    S["caption"] = ParagraphStyle("caption", fontName="PlexMono", fontSize=7.4, leading=10, textColor=MUTED)
    S["source"] = ParagraphStyle("source", parent=S["caption"], spaceBefore=3, spaceAfter=10)
    S["note"] = ParagraphStyle("note", fontName="Plex", fontSize=7.9, leading=10.6, textColor=MUTED)
    S["note_source"] = ParagraphStyle("note_source", parent=S["note"], spaceBefore=3, spaceAfter=10)
    S["formula"] = ParagraphStyle("formula", fontName="Plex", fontSize=10, leading=15, textColor=NAVY)
    S["cell"] = ParagraphStyle("cell", fontName="Plex", fontSize=8.3, leading=11, textColor=INK)
    S["cell_r"] = ParagraphStyle("cell_r", parent=S["cell"], alignment=TA_RIGHT)
    S["cell_mono"] = ParagraphStyle("cell_mono", parent=S["cell"], fontName="PlexMono", fontSize=8.1)
    S["cell_num"] = ParagraphStyle("cell_num", parent=S["cell_mono"], alignment=TA_RIGHT)
    S["cell_head"] = ParagraphStyle("cell_head", fontName="Plex-SemiBold", fontSize=7.8, leading=10, textColor=MUTED)
    S["cell_head_r"] = ParagraphStyle("cell_head_r", parent=S["cell_head"], alignment=TA_RIGHT)
    S["navy_title"] = ParagraphStyle("navy_title", fontName="Outfit-Medium", fontSize=14, leading=19,
                                     textColor=SAFED, spaceAfter=4)
    S["navy_body"] = ParagraphStyle("navy_body", fontName="Plex", fontSize=10, leading=15, textColor=PAPER,
                                    spaceAfter=5)
    S["navy_label"] = ParagraphStyle("navy_label", fontName="PlexMono", fontSize=7.4, leading=10,
                                     textColor=C(B.NEELA[100]))
    S["tile_label"] = ParagraphStyle("tile_label", fontName="Plex-SemiBold", fontSize=7.8, leading=10, textColor=MUTED)
    S["tile_sub"] = ParagraphStyle("tile_sub", fontName="PlexMono", fontSize=7.2, leading=9.5, textColor=MUTED)
    S["tile_big"] = ParagraphStyle("tile_big", fontName="Outfit-Medium", fontSize=25, leading=29, textColor=NAVY)
    S["toc0"] = ParagraphStyle("toc0", fontName="Plex-Medium", fontSize=9.6, leading=13, textColor=INK, spaceBefore=4)
    S["toc1"] = ParagraphStyle("toc1", fontName="Plex", fontSize=8.2, leading=10.4, textColor=MUTED, leftIndent=14)
    S["cell_c"] = ParagraphStyle("cell_c", parent=S["cell"], fontSize=7.8, leading=9.6)
    S["cell_mono_c"] = ParagraphStyle("cell_mono_c", parent=S["cell_mono"], fontSize=7.6, leading=9.6)
    S["cell_num_c"] = ParagraphStyle("cell_num_c", parent=S["cell_num"], fontSize=7.6, leading=9.6)
    return S


ST = None   # filled in build()
_MONO_CMAP = None
MONO_FALLBACK = {"caption": "note", "source": "note_source", "cell_mono": "cell", "cell_num": "cell_r",
                 "cell_mono_c": "cell_c", "cell_num_c": "cell_c",
                 "part": "note", "tile_sub": "note", "navy_label": "navy_body"}


def _mono_safe(text):
    """True when every visible character of `text` exists in IBM Plex Mono (it has no Greek)."""
    global _MONO_CMAP
    if _MONO_CMAP is None:
        from fontTools.ttLib import TTFont
        _MONO_CMAP = set(TTFont(os.path.join(B.FONT_DIR, B.FONT_FILES["PlexMono"])).getBestCmap())
    visible = re.sub(r"<[^>]+>", "", text)
    visible = re.sub(r"&(amp|lt|gt|quot|#\d+);", "x", visible)
    return all(ord(ch) in _MONO_CMAP for ch in visible)


def para(text, style="body"):
    """Paragraph that falls back from Plex Mono to Plex Sans when the text needs glyphs Mono lacks."""
    if style in MONO_FALLBACK and not _mono_safe(text):
        style = MONO_FALLBACK[style]
    return Paragraph(text, ST[style])


def P(text, style="body"):
    return para(text, style)


def bullets(items, style="bullet"):
    return [Paragraph(t, ST[style], bulletText="•") for t in items]


def numbered(items):
    return [Paragraph(t, ST["num_item"], bulletText=f"{i}.") for i, t in enumerate(items, 1)]


# ---------------------------------------------------------------------------------------------------------
# Flowables
# ---------------------------------------------------------------------------------------------------------
class KesariRule(Flowable):
    """The single Kesari accent of a page: a short saffron rule above a section opener."""

    def __init__(self, width=22 * mm, thickness=2.4):
        super().__init__()
        self.w, self.t = width, thickness

    def wrap(self, aw, ah):
        return self.w, self.t + 10

    def draw(self):
        self.canv.setFillColor(C(B.KESARI[100]))
        self.canv.rect(0, 10, self.w, self.t, stroke=0, fill=1)


class Heading(Paragraph):
    """A Paragraph that registers itself in the table of contents and the PDF outline."""

    def __init__(self, text, style, level, toc_text=None):
        super().__init__(text, ST[style])
        self.toc_level = level
        self.toc_text = toc_text or re.sub("<[^>]+>", "", text)


class CodeBlock(Flowable):
    """Line-numbered, syntax-coloured Python listing in IBM Plex Mono that wraps long lines and splits
    across pages. Colours stay inside the Neela/Syahi range (green and red are reserved for gains/losses)."""

    SIZE, LEAD, PAD, GUTTER = 6.7, 8.7, 6.0, 26.0

    def __init__(self, vlines, width):
        super().__init__()
        self.vlines, self.width = vlines, width

    @classmethod
    def from_source(cls, src, width, first_line=1):
        from pygments.lexers import PythonLexer
        from pygments.token import Token
        palette = [
            (Token.Comment, B.SYAHI[60], "PlexMono"), (Token.Literal.String.Doc, B.SYAHI[60], "PlexMono"),
            (Token.Literal.String, B.NEELA[500], "PlexMono"), (Token.Literal.Number, B.NEELA[700], "PlexMono"),
            (Token.Keyword, B.NEELA[700], "PlexMono-SemiBold"), (Token.Name.Builtin, B.NEELA[600], "PlexMono"),
            (Token.Name.Function, B.NEELA[900], "PlexMono-SemiBold"), (Token.Name.Class, B.NEELA[900], "PlexMono-SemiBold"),
            (Token.Name.Decorator, B.NEELA[600], "PlexMono"), (Token.Operator.Word, B.NEELA[700], "PlexMono-SemiBold"),
        ]

        def style_of(tok):
            for t, col, font in palette:
                if tok in t:
                    return col, font
            return B.SYAHI[100], "PlexMono"

        char_w = cls.SIZE * 0.6
        maxcols = int((width - cls.GUTTER - 2 * cls.PAD) / char_w)
        lines = [[]]
        for tok, val in PythonLexer(stripnl=False).get_tokens(src):
            col, font = style_of(tok)
            parts = val.split("\n")
            for k, part in enumerate(parts):
                if k > 0:
                    lines.append([])
                if part:
                    lines[-1].append((part, col, font))
        if lines and not lines[-1]:
            lines.pop()
        vlines = []
        for i, frags in enumerate(lines):
            chars = [(ch, col, font) for text, col, font in frags for ch in text]
            indent = len("".join(ch for ch, _, _ in chars)) - len("".join(ch for ch, _, _ in chars).lstrip(" "))
            hang = min(indent + 4, maxcols // 2)
            first, pos = True, 0
            while True:
                room = maxcols if first else maxcols - hang
                rest = chars[pos:]
                if len(rest) <= room:
                    piece = rest
                    nxt = len(chars)
                else:
                    cut = room
                    for k in range(room, max(room - 28, 1), -1):     # break after a space, comma or operator
                        if rest[k - 1][0] in " ,(=+-*/" and rest[k][0] != " ":
                            cut = k
                            break
                    piece = rest[:cut]
                    nxt = pos + cut
                runs = [] if first else [(" " * hang, B.SYAHI[40], "PlexMono")]
                for ch, col, font in piece:
                    if runs and runs[-1][1] == col and runs[-1][2] == font:
                        runs[-1] = (runs[-1][0] + ch, col, font)
                    else:
                        runs.append((ch, col, font))
                vlines.append((first_line + i if first else None, runs))
                if nxt >= len(chars):
                    break
                pos, first = nxt, False
                while pos < len(chars) and chars[pos][0] == " ":
                    pos += 1
        return cls(vlines, width)

    def wrap(self, aw, ah):
        return self.width, len(self.vlines) * self.LEAD + 2 * self.PAD

    def split(self, aw, ah):
        n = int((ah - 2 * self.PAD) // self.LEAD)
        if n < 4 or n >= len(self.vlines):
            return [] if n < 4 else [self]
        return [CodeBlock(self.vlines[:n], self.width), CodeBlock(self.vlines[n:], self.width)]

    def draw(self):
        c = self.canv
        h = len(self.vlines) * self.LEAD + 2 * self.PAD
        c.setFillColor(SAFED); c.setStrokeColor(RULE); c.setLineWidth(0.5)
        c.rect(0, 0, self.width, h, stroke=1, fill=1)
        c.setFillColor(C(B.KAGAZ[50])); c.rect(0.25, 0.25, self.GUTTER - 4, h - 0.5, stroke=0, fill=1)
        y = h - self.PAD - self.SIZE
        for no, frags in self.vlines:
            if no is not None:
                c.setFont("PlexMono", self.SIZE - 0.6); c.setFillColor(SUBTLE)
                c.drawRightString(self.GUTTER - 8, y, str(no))
            else:
                c.setFont("PlexMono", self.SIZE); c.setFillColor(SUBTLE)
                c.drawRightString(self.GUTTER - 8, y, "↪")
            t = c.beginText(self.GUTTER + self.PAD - 2, y)      # one text object per line: clean copy-paste
            for text, col, font in frags:
                t.setFont(font, self.SIZE); t.setFillColor(C(col))
                t.textOut(text)
            c.drawText(t)
            y -= self.LEAD


def card(flowables, width=FW, pad=8, bg=SAFED, border=RULE):
    t = Table([[f] for f in flowables], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.5, border),
        ("LEFTPADDING", (0, 0), (-1, -1), pad), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, 0), pad), ("BOTTOMPADDING", (0, -1), (-1, -1), pad),
    ]))
    return t


def navy_card(title, paras, label=None, width=FW):
    items = []
    if label:
        items.append(P(esc(label), "navy_label"))
    items.append(P(title, "navy_title"))
    items += [P(p, "navy_body") for p in paras]
    return card(items, width=width, pad=12, bg=NAVY, border=NAVY)


def formula_card(lines, note=None):
    items = [P(l, "formula") for l in lines]
    if note:
        items.append(P(note, "note"))
    t = card(items, width=FW - MEASURE_INDENT + 20 * mm, pad=10)
    return t


def data_table(header, rows, widths, num_cols=(), mono_cols=(), zebra=True, head_rows=1, compact=False,
               keep=None):
    """Brand table: Safed card, Kagaz rules, Plex text, Plex Mono right-aligned numbers.
    Tables of up to 12 rows are kept on one page; longer ones split with the header repeated."""
    sfx = "_c" if compact else ""

    def cell(v, j, head):
        if isinstance(v, Flowable):
            return v
        if head:
            return para(v, "cell_head_r" if j in num_cols else "cell_head")
        if j in num_cols:
            return para(v, "cell_num" + sfx)
        if j in mono_cols:
            return para(v, "cell_mono" + sfx)
        return para(v, "cell" + sfx)
    data = [[cell(v, j, True) for j, v in enumerate(header)]] + [[cell(v, j, False) for j, v in enumerate(r)] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=head_rows, hAlign="LEFT")
    pad = 2.0 if compact else 3.2
    cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), SAFED), ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data)):
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.25, C(B.KAGAZ[200])))
        if zebra and i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C(B.KAGAZ[50])))
    t.setStyle(TableStyle(cmds))
    if keep if keep is not None else len(rows) <= 12:
        return KeepTogether([t])
    return t


def figure(drawing, caption, source=None):
    items = [drawing, Spacer(1, 4), P(caption, "caption")]
    if source:
        items.append(P(source, "caption"))
    return KeepTogether([card(items), Spacer(1, 10)])


def stat_tiles(tiles, width=FW):
    """Row of KPI tiles: label, period, big Outfit number (signed colour), note."""
    n = len(tiles)
    gap = 6
    w = (width - gap * (n - 1)) / n
    cells = []
    for label, period, value, note in tiles:
        big = f'<font color="{signed_colour(value) if isinstance(value, float) else B.NEELA[900]}">' \
              f'{esc(B.num(value, 2) if isinstance(value, float) else value)}</font>'
        inner = Table([[P(esc(label), "tile_label")], [P(esc(period), "tile_sub")], [P(big, "tile_big")],
                       [P(esc(note), "tile_sub")]], colWidths=[w - 2])
        inner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SAFED), ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, 0), 9),
                                   ("BOTTOMPADDING", (0, -1), (-1, -1), 9), ("TOPPADDING", (0, 1), (-1, -1), 1),
                                   ("BOTTOMPADDING", (0, 0), (-1, -2), 1)]))
        cells.append(inner)
    row = []
    for k, c in enumerate(cells):
        row.append(c)
        if k < n - 1:
            row.append("")
    widths = []
    for k in range(n):
        widths.append(w)
        if k < n - 1:
            widths.append(gap)
    t = Table([row], colWidths=widths)
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("FONTNAME", (0, 0), (-1, -1), "Plex")]))
    return t


def opener(label, title, standfirst=None, level_text=None):
    """Section opener: Kesari rule (the page's one accent), mono label, Outfit title, Outfit Light standfirst."""
    out = [KesariRule(), P(esc(label), "part"),
           Heading(esc(title), "h1", 0, toc_text=level_text or (f"{label}  ·  {title}" if label else title))]
    if standfirst:
        out.append(P(standfirst, "standfirst"))
    return out


def h2(text):
    return Heading(esc(text), "h2", 1)


def h3(text):
    return P(esc(text), "h3")


# ---------------------------------------------------------------------------------------------------------
# Charts (matplotlib -> SVG with text as paths -> vector reportlab Drawing)
# ---------------------------------------------------------------------------------------------------------
CHART_W = FW - 16


def to_drawing(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="svg")
    plt.close(fig)
    buf.seek(0)
    d = svg2rlg(buf)
    if d is None:
        raise RuntimeError("svglib could not convert a chart")
    return d


def new_fig(h_pt, nrows=1, height_ratios=None, sharex=True):
    fig, ax = plt.subplots(nrows, 1, figsize=(CHART_W / 72, h_pt / 72), sharex=sharex,
                           gridspec_kw={"height_ratios": height_ratios} if height_ratios else None)
    return fig, ax


def date_axis(ax, fmt="%Y", major=None):
    ax.margins(x=0)
    ax.xaxis.set_major_locator(major or mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
    for lab in ax.get_xticklabels():
        lab.set_fontfamily("IBM Plex Mono")
    for lab in ax.get_yticklabels():
        lab.set_fontfamily("IBM Plex Mono")


def mono_ticks(ax):
    for lab in ax.get_xticklabels() + ax.get_yticklabels():
        lab.set_fontfamily("IBM Plex Mono")


def chart_equity(eq, expo):
    fig, (ax, ax2) = new_fig(255, 2, [3.2, 1])
    s, b = eq["strategy"], eq["niftybees"]
    ax.axvspan(pd.Timestamp("2024-01-01"), s.index[-1], color=B.KAGAZ[200], lw=0, zorder=0)
    ax.plot(b.index, b.values, color=B.NEELA[300], lw=1.0, label="NIFTYBEES buy and hold")
    ax.plot(s.index, s.values, color=B.NEELA[900], lw=1.5, label="Strategy, net of costs")
    ax.set_yscale("log")
    ticks = [0.8, 1, 1.25, 1.5, 2, 2.5, 3, 4]
    ax.set_yticks(ticks); ax.set_yticklabels([f"{t:g}" for t in ticks]); ax.minorticks_off()
    ax.text(pd.Timestamp("2024-02-01"), 0.83, "Validation window: never used to select rules", fontsize=7,
            color=B.SYAHI[60])
    ax.legend(loc="upper left", ncol=2)
    ax.set_ylabel("Growth of 1 (log scale)")
    ax2.fill_between(expo.index, expo.values, color=B.NEELA[200], lw=0, step="post")
    ax2.set_ylim(0, 1.05); ax2.set_yticks([0, 0.5, 1]); ax2.set_yticklabels(["0%", "50%", "100%"])
    ax2.set_ylabel("Invested")
    ax2.axvspan(pd.Timestamp("2024-01-01"), s.index[-1], color=B.KAGAZ[200], lw=0, zorder=0)
    ax2.set_xlim(s.index[0], s.index[-1])
    date_axis(ax2); mono_ticks(ax)
    fig.tight_layout(pad=0.4, h_pad=0.3)
    return to_drawing(fig)


def chart_drawdown(eq):
    fig, ax = new_fig(120)
    for col, colour, lw, lab in [("niftybees", B.NEELA[300], 1.0, "NIFTYBEES"), ("strategy", B.NEELA[900], 1.3, "Strategy")]:
        e = eq[col]
        dd = e / e.cummax() - 1
        ax.plot(dd.index, dd.values * 100, color=colour, lw=lw, label=lab)
    ax.set_ylabel("Drawdown, %")
    ax.legend(loc="lower right", ncol=2)
    date_axis(ax)
    fig.tight_layout(pad=0.4)
    return to_drawing(fig)


def chart_regime(md, S_mod, p, year="2023"):
    from indicators import rsi
    idx = md.index_close["NIFTY50"]
    r = rsi(md.index_close[["NIFTY50"]], p["regime_rsi_n"])["NIFTY50"]
    hy = S_mod.hysteresis(r, p["regime_rsi_mid"] + p["regime_rsi_band"], p["regime_rsi_mid"] - p["regime_rsi_band"])
    on = pd.Series(hy & (r <= p["regime_rsi_max"]).values, index=r.index)
    sl = slice(f"{year}-01-01", f"{year}-12-31")
    fig, (ax, ax2) = new_fig(235, 2, [1.6, 1])
    x, o = idx.loc[sl], on.loc[sl]
    for a in (ax, ax2):
        a.fill_between(o.index, 0, 1, where=o.values, transform=a.get_xaxis_transform(), color=B.NEELA[100],
                       alpha=0.55, lw=0, step="post")
    ax.plot(x.index, x.values, color=B.NEELA[900], lw=1.3)
    ax.set_ylabel("NIFTY 50")
    rr = r.loc[sl]
    ax2.plot(rr.index, rr.values, color=B.NEELA[900], lw=1.1)
    on_l, off_l, max_l = (p["regime_rsi_mid"] + p["regime_rsi_band"], p["regime_rsi_mid"] - p["regime_rsi_band"],
                          p["regime_rsi_max"])
    for lvl, ls, lab in [(on_l, "-", f"switch on above {on_l:g}"), (off_l, "--", f"switch off below {off_l:g}"),
                         (max_l, ":", f"stand aside above {max_l:g}")]:
        ax2.axhline(lvl, color=B.SYAHI[60], lw=0.8, ls=ls, label=lab)
    ax2.set_ylabel("RSI(14)"); ax2.set_ylim(20, 92)
    ax2.legend(loc="lower right", ncol=3, fontsize=6.6, framealpha=0.9, frameon=True, edgecolor="none")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=B.NEELA[100], alpha=0.55, label="In market")], loc="upper left")
    ax.set_xlim(x.index[0], x.index[-1])
    date_axis(ax2, "%b", mdates.MonthLocator()); mono_ticks(ax)
    fig.tight_layout(pad=0.4, h_pad=0.3)
    return to_drawing(fig)


def chart_indicators(md, p, ticker, start, end, t_form, t_exit):
    from indicators import ema, rsi, rolling_vwap
    c, h, l, v = (md.close[[ticker]], md.high[[ticker]], md.low[[ticker]], md.volume[[ticker]])
    e20, e50 = ema(c, p["ema_fast"])[ticker], ema(c, p["ema_slow"])[ticker]
    vw20 = rolling_vwap(h, l, c, v, p["vwap_n"])[ticker]
    vw3 = rolling_vwap(h, l, c, v, p["vwap_short_n"])[ticker]
    vw50 = rolling_vwap(h, l, c, v, p["vwap_long_n"])[ticker]
    sr = ema(rsi(c, p["rsi_n"]), p["rsi_smooth"])[ticker]
    sl = slice(start, end)
    fig, (ax, ax2) = new_fig(250, 2, [2.2, 1])
    ax.plot(c.loc[sl].index, c[ticker].loc[sl], color=B.SYAHI[100], lw=1.2, label="Close")
    ax.plot(e20.loc[sl].index, e20.loc[sl], color=B.NEELA[700], lw=1.0, label="EMA20")
    ax.plot(e50.loc[sl].index, e50.loc[sl], color=B.NEELA[300], lw=1.0, label="EMA50")
    ax.plot(vw20.loc[sl].index, vw20.loc[sl], color=B.NEELA[500], lw=1.0, ls="--", label="VWAP20")
    ax.plot(vw3.loc[sl].index, vw3.loc[sl], color=B.SYAHI[40], lw=0.8, ls=":", label="VWAP3")
    ax.plot(vw50.loc[sl].index, vw50.loc[sl], color=B.NEELA[200], lw=1.0, ls="-.", label="VWAP50")
    ax.set_xlim(c.loc[sl].index[0], c.loc[sl].index[-1])
    for d, lab, ha, dx in [(t_form, "formation close ", "right", -0.4), (t_exit, " exit close", "left", 0.4)]:
        for a in (ax, ax2):
            a.axvline(pd.Timestamp(d), color=B.SYAHI[60], lw=0.8)
        ax.text(pd.Timestamp(d) + pd.Timedelta(days=dx), ax.get_ylim()[0], lab, fontsize=6.6, color=B.SYAHI[60],
                va="bottom", ha=ha)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=6, fontsize=6.8)
    ax.set_ylabel(f"{ticker}, rupees")
    ax2.plot(sr.loc[sl].index, sr.loc[sl], color=B.NEELA[900], lw=1.1)
    ax2.axhline(p["rsi_hi"], color=B.SYAHI[60], lw=0.8, ls="--")
    ax2.axhline(p["rsi_peak"], color=B.SYAHI[60], lw=0.8, ls=":")
    ax2.set_ylabel("Smoothed RSI"); ax2.set_ylim(25, 90)
    ax2.text(sr.loc[sl].index[0], p["rsi_hi"] + 1.5, f" entry needs < {p['rsi_hi']:g}", fontsize=6.4, color=B.SYAHI[60])
    ax2.text(sr.loc[sl].index[0], p["rsi_peak"] + 1.5, f" no reading above {p['rsi_peak']:g} in {p['rsi_peak_n']} days",
             fontsize=6.4, color=B.SYAHI[60])
    date_axis(ax2, "%d %b", mdates.WeekdayLocator(byweekday=mdates.MO, interval=2)); mono_ticks(ax)
    fig.tight_layout(pad=0.4, h_pad=0.3)
    return to_drawing(fig)


def chart_lineage(lin):
    fig, ax = new_fig(210)
    n = len(lin)
    x = np.arange(n)
    wbar = 0.27
    series = [("sharpe_is", "2020–23 (selected on)", B.NEELA[900]),
              ("sharpe_oos", "2024–26 (validation)", B.NEELA[400]),
              ("sharpe_pre", "2017–19 (sealed hold-out)", B.NEELA[200])]
    for k, (key, lab, col) in enumerate(series):
        ax.bar(x + (k - 1) * wbar, [o.get(key, np.nan) for o in lin], wbar, color=col, label=lab, lw=0)
    ax.axhline(0, color=B.SYAHI[100], lw=0.7)
    ax.axhline(lin[-1].get("bench_sharpe_pre", np.nan), color=B.SYAHI[60], lw=0.8, ls="--")
    ax.text(n - 0.5, lin[-1].get("bench_sharpe_pre", 0) + 0.12, "Nifty 2017–19", fontsize=6.6, color=B.SYAHI[60],
            ha="right")
    ax.set_xticks(x)
    ax.set_xticklabels(["base"] + [str(i) for i in range(1, n - 1)] + ["final"])
    ax.set_ylabel("Sharpe ratio (excess of repo)")
    ax.set_xlabel("Kept strategy, in the order the loop kept them")
    ax.legend(loc="upper left", ncol=3)
    ax.grid(axis="x", visible=False)
    mono_ticks(ax)
    fig.tight_layout(pad=0.4)
    return to_drawing(fig)


def chart_yearly(yearly):
    fig, ax = new_fig(150)
    y = [r["year"] for r in yearly]
    x = np.arange(len(y))
    sv = [r["strat_ret"] * 100 for r in yearly]
    nv = [r["bees_ret"] * 100 for r in yearly]
    ax.bar(x - 0.2, sv, 0.38, color=[B.GAIN if v > 0 else B.LOSS for v in sv], lw=0, label="Strategy")
    ax.bar(x + 0.2, nv, 0.38, color=B.NEELA[300], lw=0, label="NIFTYBEES")
    ax.axhline(0, color=B.SYAHI[100], lw=0.7)
    ax.set_xticks(x); ax.set_xticklabels([str(v) + ("*" if v == 2026 else "") for v in y])
    ax.set_ylabel("Calendar-year return, %")
    ax.grid(axis="x", visible=False)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=B.GAIN, label="Strategy, up year"), Patch(color=B.LOSS, label="Strategy, down year"),
                       Patch(color=B.NEELA[300], label="NIFTYBEES")], loc="upper right", ncol=3)
    mono_ticks(ax)
    fig.tight_layout(pad=0.4)
    return to_drawing(fig)


def chart_rolling(ret, rf):
    ex = (ret - rf.reindex(ret.index)).dropna()
    roll = ex.rolling(126).mean() / ex.rolling(126).std() * np.sqrt(252)
    fig, ax = new_fig(130)
    ax.axvspan(pd.Timestamp("2024-01-01"), roll.index[-1], color=B.KAGAZ[200], lw=0, zorder=0)
    ax.plot(roll.index, roll.values, color=B.NEELA[900], lw=1.1)
    ax.axhline(0, color=B.SYAHI[100], lw=0.7)
    ax.set_ylabel("Rolling 6-month Sharpe")
    date_axis(ax)
    fig.tight_layout(pad=0.4)
    return to_drawing(fig)


def chart_perturb(ps):
    rows = [r for r in ps if r["param"] != "base"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(CHART_W / 72, 130 / 72))
    base = next(r for r in ps if r["param"] == "base")
    for a, key, lab in [(a1, "sharpe_is", "2020–23 Sharpe"), (a2, "sharpe_oos", "2024–26 Sharpe")]:
        vals = [r[key] for r in rows]
        a.hist(vals, bins=28, color=B.NEELA[400], lw=0)
        a.axvline(base[key], color=B.NEELA[900], lw=1.2)
        a.text(base[key], a.get_ylim()[1] * 0.95, " final", fontsize=6.6, color=B.NEELA[900], va="top")
        a.set_xlabel(lab); a.set_ylabel("Perturbations")
        a.grid(axis="x", visible=False)
        mono_ticks(a)
    fig.tight_layout(pad=0.4, w_pad=1.5)
    return to_drawing(fig)


def chart_holdout(hold):
    fig, ax = new_fig(150)
    hs = (1 + hold["strategy"]).cumprod(); hb = (1 + hold["niftybees"].fillna(0)).cumprod()
    hc = (1 + hold["cash"]).cumprod()
    ax.plot(hb.index, hb.values, color=B.NEELA[300], lw=1.0, label="NIFTYBEES buy and hold")
    ax.plot(hc.index, hc.values, color=B.SYAHI[40], lw=0.9, ls="--", label="Cash at the repo rate")
    ax.plot(hs.index, hs.values, color=B.NEELA[900], lw=1.4, label="Strategy, net of costs")
    ax.set_ylabel("Growth of 1")
    ax.legend(loc="upper left", ncol=3)
    date_axis(ax)
    fig.tight_layout(pad=0.4)
    return to_drawing(fig)


def chart_spells(spells):
    fig, ax = new_fig(150)
    d = pd.DataFrame(spells)
    d["start"] = pd.to_datetime(d["start"])
    x = np.arange(len(d))
    vals = d["excess_return"].values * 100
    ax.bar(x, vals, 0.75, color=[B.GAIN if v > 0 else B.LOSS for v in vals], lw=0)
    split = int((d["period"] == "2020-23").sum())
    ax.axvline(split - 0.5, color=B.SYAHI[60], lw=0.8, ls="--")
    ax.text(split / 2, ax.get_ylim()[1] * 0.92, "2020–23: selection window", ha="center", fontsize=7,
            color=B.SYAHI[60])
    ax.text(split + (len(d) - split) / 2, ax.get_ylim()[1] * 0.92, "2024–26: validation window", ha="center",
            fontsize=7, color=B.SYAHI[60])
    ax.axhline(0, color=B.SYAHI[100], lw=0.7)
    ax.set_ylabel("Spell return over cash, %")
    ax.set_xlabel("Invested spells in date order (switch-on to switch-off)")
    ax.set_xticks([])
    ax.grid(axis="x", visible=False)
    mono_ticks(ax)
    fig.tight_layout(pad=0.4)
    return to_drawing(fig)


def chart_edge(edge):
    fig, ax = new_fig(120)
    y = [r["year"] for r in edge]
    v = [r["edge"] * 100 for r in edge]
    ax.bar(np.arange(len(y)), v, 0.6, color=[B.GAIN if t > 0 else B.LOSS for t in v], lw=0)
    ax.axhline(0, color=B.SYAHI[100], lw=0.7)
    ax.set_xticks(np.arange(len(y))); ax.set_xticklabels([str(t) for t in y])
    ax.set_ylabel("Selection edge, % per 20 sessions")
    ax.grid(axis="x", visible=False)
    mono_ticks(ax)
    fig.tight_layout(pad=0.4)
    return to_drawing(fig)


# ---------------------------------------------------------------------------------------------------------
# Diagram: one day of the strategy (reportlab vector shapes)
# ---------------------------------------------------------------------------------------------------------
def flow_diagram(width=CHART_W):
    """The daily decision flow as vector shapes: three columns (cash, formation, riding) feeding the next open."""
    h = 372
    d = Drawing(width, h)
    gap = 22
    bw = (width - 2 * gap) / 3
    xs = [0, bw + gap, 2 * (bw + gap)]

    def box(x, top, title, sub=None, hh=34, dark=False):
        y = top - hh
        d.add(Rect(x, y, bw, hh, fillColor=NAVY if dark else SAFED, strokeColor=NAVY if dark else RULE,
                   strokeWidth=0.6, rx=2, ry=2))
        d.add(String(x + 8, top - 13, title, fontName="Plex-SemiBold", fontSize=8, fillColor=SAFED if dark else INK))
        for k, line in enumerate((sub or "").split("\n")):
            if line:
                d.add(String(x + 8, top - 24 - 9 * k, line, fontName="Plex", fontSize=6.8,
                             fillColor=C(B.NEELA[100]) if dark else MUTED))
        return y

    def head(x2, y2, ang):
        a = 4.5
        p1 = (x2 - a * np.cos(ang - 0.42), y2 - a * np.sin(ang - 0.42))
        p2 = (x2 - a * np.cos(ang + 0.42), y2 - a * np.sin(ang + 0.42))
        d.add(Polygon([x2, y2, p1[0], p1[1], p2[0], p2[1]], fillColor=MUTED, strokeColor=MUTED, strokeWidth=0.3))

    def path(points, label=None, at=None):
        for (x1, y1), (x2, y2) in zip(points[:-1], points[1:]):
            d.add(Line(x1, y1, x2, y2, strokeColor=MUTED, strokeWidth=0.8))
        (x1, y1), (x2, y2) = points[-2], points[-1]
        head(x2, y2, np.arctan2(y2 - y1, x2 - x1))
        if label:
            d.add(String(at[0], at[1], label, fontName="PlexMono", fontSize=6.6, fillColor=MUTED))

    cx = [x + bw / 2 for x in xs]
    top = h
    y_close = box(xs[0], top, "Close of day t", "indicators on data up to the close", dark=True)
    t_reg = y_close - 22
    y_reg = box(xs[0], t_reg, "Regime in?", "NIFTY RSI(14) switch on above 57,\noff below 53; and RSI \u2264 80", hh=42)
    path([(cx[0], y_close), (cx[0], t_reg)])
    y_yday = box(xs[1], t_reg, "Regime in yesterday?", "no: today is a formation day", hh=42)
    path([(xs[0] + bw, t_reg - 21), (xs[1], t_reg - 21)], "yes", (xs[0] + bw + 5, t_reg - 17))
    y_ride = box(xs[2], t_reg, "Ride the cohort", "weights drift with prices,\nno names added", hh=42)
    path([(xs[1] + bw, t_reg - 21), (xs[2], t_reg - 21)], "yes", (xs[1] + bw + 5, t_reg - 17))
    t_cash = y_reg - 26
    y_cash = box(xs[0], t_cash, "Target: 100% cash", "everything is sold at the next open")
    path([(cx[0], y_reg), (cx[0], t_cash)], "no", (cx[0] + 5, y_reg - 15))
    t_form = y_yday - 26
    y_form = box(xs[1], t_form, "Form one cohort", "fresh: confluence names, weights\n\u03b2 / idiosyncratic variance\nre-entry: top 5, ladder \u00d7 RSI lead", hh=50)
    path([(cx[1], y_yday), (cx[1], t_form)], "no", (cx[1] + 5, y_yday - 15))
    t_size = y_form - 20
    y_size = box(xs[1], t_size, "Size and cap", "Bank Nifty variance ratio; whipsaw\nhalf size; 40% cap, rest to NIFTYBEES", hh=42)
    path([(cx[1], y_form), (cx[1], t_size)])
    steps = [("1  Exits", "VWAP20 band, VWAP ladder,\nidiosyncratic shock, volume spike"),
             ("2  Exhaustion", "less than 40% of the cohort left:\nsell the stock remainder"),
             ("3  Re-rank (day 3, once)", "sell the bottom third by score"),
             ("4  Vol budget (from day 3)", "trim to 10% model vol, lowest\nscores first; never adds"),
             ("5  Breadth-dip top-up", "fresh cohorts: idle cash into the\nriders, within the budget")]
    y = y_ride
    for t, sub in steps:
        nt = y - 12
        ny = box(xs[2], nt, t, sub, hh=37 if "\n" in sub else 27)
        path([(cx[2], y), (cx[2], nt)])
        y = ny
    t_exec = 44
    y_exec = box(xs[1], t_exec, "Targets \u2192 open of day t+1", "trade from drifted weights at 15 bps;\nidle cash earns the repo rate", hh=40, dark=True)
    path([(cx[1], y_size), (cx[1], t_exec)])
    path([(cx[0], y_cash), (cx[0], t_exec - 20), (xs[1], t_exec - 20)])
    path([(cx[2], y), (cx[2], t_exec - 20), (xs[1] + bw, t_exec - 20)])
    return d


# ---------------------------------------------------------------------------------------------------------
# Worked example: one cohort traced through the backtest
# ---------------------------------------------------------------------------------------------------------
def cohort_trace(md, W, p, start):
    """Formation weights and a day-by-day event log for the cohort formed on the close of `start`.
    Exit reasons are recomputed from the same formulas as strategy.py; every exit must be explained."""
    from indicators import ema, rsi, rolling_vwap, realized_vol
    c, h, l, v = md.close, md.high, md.low, md.volume
    vw = rolling_vwap(h, l, c, v, p["vwap_n"])
    vw3 = rolling_vwap(h, l, c, v, p["vwap_short_n"]); vw50 = rolling_vwap(h, l, c, v, p["vwap_long_n"])
    ret, idx_ret = c.pct_change(), md.index_close["NIFTY50"].pct_change()
    vol = realized_vol(c, p["vol_n"])
    nvol = realized_vol(md.index_close[["NIFTY50"]], p["vol_n"])["NIFTY50"]
    corr = ret.rolling(p["corr_n"], min_periods=p["corr_n"]).corr(idx_ret)
    ivol = vol * np.sqrt((1.0 - corr ** 2).clip(lower=0.0))
    beta = corr * vol.div(nvol, axis=0)
    shock = (ret - beta.shift(1).mul(idx_ret, axis=0)) < -p["shock_k"] * ivol.shift(1) / np.sqrt(252)
    spike = v > p["vspike_k"] * v.rolling(p["vwap_n"], min_periods=p["vwap_n"]).mean().shift(1)
    band = c <= vw * p["vwap_exit"]
    ladder = (c < vw3) & (vw3 < vw50)
    ef, es = ema(c, p["ema_fast"]), ema(c, p["ema_slow"])
    sr = ema(rsi(c, p["rsi_n"]), p["rsi_smooth"])
    t0 = W.index.get_loc(pd.Timestamp(start))
    gross = W.sum(axis=1)
    t1 = t0
    while t1 + 1 < len(W) and gross.iloc[t1 + 1] > 0:
        t1 += 1
    w0 = W.iloc[t0]
    names = w0[w0 > 0].sort_values(ascending=False)
    form = []
    for tk, wt in names.items():
        if tk == "NIFTYBEES":
            form.append((tk, wt, None, None, None, None))
            continue
        d = W.index[t0]
        form.append((tk, wt, float(c.at[d, tk] / vw.at[d, tk] - 1), float(ef.at[d, tk] / es.at[d, tk] - 1),
                     float(sr.at[d, tk]), float(c.at[d, tk] / vw3.at[d, tk] - 1)))
    events, unexplained = [], []
    prev = set(names.index)
    for k in range(t0 + 1, t1 + 2):
        d = W.index[k]
        row = W.iloc[k]
        held = set(row[row > 0].index)
        out = sorted(prev - held)
        day = k - t0
        if not out and k <= t1:
            ex_w = W.iloc[k - 1]
            g = (c.iloc[k] / c.iloc[k - 1]).reindex(ex_w.index).fillna(1.0)
            drift = ex_w * g / (1 + (ex_w * (g - 1)).sum())
            dsum = row.sum() - drift.sum()
            if dsum < -0.005:
                events.append((d, day, f"Trimmed to the 10% volatility budget (gross {B.num(drift.sum() * 100, 1)}% "
                                       f"→ {B.num(row.sum() * 100, 1)}%)", row.sum(), len(held)))
            elif dsum > 0.005:
                events.append((d, day, f"Breadth-dip top-up (gross {B.num(drift.sum() * 100, 1)}% → "
                                       f"{B.num(row.sum() * 100, 1)}%)", row.sum(), len(held)))
            prev = held
            continue
        if not out:
            prev = held
            continue
        if k > t1:
            events.append((d, day, "Regime off: everything sold (" + ", ".join(out) + ")", 0.0, 0))
            break
        reasons, flagged = [], {}
        for tk in out:
            why = [lab for flag, lab in [(band, f"close under {p['vwap_exit']:g} × VWAP{p['vwap_n']}"),
                                         (ladder, "VWAP ladder break"), (shock, "idiosyncratic shock"),
                                         (spike, "volume spike")] if tk != "NIFTYBEES" and bool(flag.at[d, tk])]
            if not md.member.at[d, tk]:
                why.append("left the index")
            flagged[tk] = why
        stocks0 = w0.drop("NIFTYBEES", errors="ignore")
        left = [tk for tk in prev if tk != "NIFTYBEES" and not flagged.get(tk)]
        exhausted = stocks0.sum() > 0 and stocks0.reindex(left).fillna(0).sum() < p["exhaust"] * stocks0.sum()
        for tk in out:
            why = flagged[tk]
            if not why and exhausted:
                why = ["exhaustion: the cohort is spent"]
            elif not why and day == p["vol_cap_delay"] + 1:
                why = ["re-rank: bottom third by score"]
            elif not why:
                why = ["unexplained"]
                unexplained.append((d, tk))
            reasons.append(f"{tk} ({'; '.join(why)})")
        events.append((d, day, "Sold " + ", ".join(reasons), row.sum(), len(held)))
        prev = held
    return dict(t0=W.index[t0], t1=W.index[t1], formation=form, events=events, unexplained=unexplained,
                gross0=float(w0.sum()), spell_days=t1 - t0 + 1)


# ---------------------------------------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------------------------------------
class NoteDoc(BaseDocTemplate):
    def __init__(self, filename, meta, **kw):
        super().__init__(filename, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=MT, bottomMargin=MB,
                         title=f"{DOC_ID} · {DOC_TITLE}", author=f"RAAS Capital · {DESK}",
                         subject=DOC_SUBTITLE, creator="make_pdf.py (RAAS Capital brand)",
                         keywords="RAAS Capital, research note, NSE, Nifty 50, RSI, EMA, VWAP, backtest", **kw)
        self.meta = meta
        frame = Frame(ML, MB, FW, PH - MT - MB, id="body", leftPadding=0, rightPadding=0, topPadding=0,
                      bottomPadding=0)
        full = Frame(0, 0, PW, PH, id="full", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate("cover", [full], onPage=self.draw_cover),
                               PageTemplate("body", [frame], onPage=self.draw_body),
                               PageTemplate("back", [full], onPage=self.draw_back)])
        self._key = 0

    def beforeDocument(self):
        self._key = 0                                  # stable bookmark keys on every multiBuild pass

    def afterFlowable(self, f):
        if isinstance(f, Heading):
            self._key += 1
            key = f"h{self._key}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(f.toc_text, key, level=f.toc_level, closed=f.toc_level > 0)
            self.notify("TOCEntry", (f.toc_level, f.toc_text, self.page, key))

    # --- cover: Neela 900 ground, the full-colour logo, title in white, one Kesari 60 accent
    def draw_cover(self, c, doc):
        m = self.meta
        c.saveState()
        c.setFillColor(NAVY); c.rect(0, 0, PW, PH, stroke=0, fill=1)
        logo_w = 64 * mm
        dlogo = B.logo_drawing("full-colour", logo_w)
        renderPDF.draw(dlogo, c, ML, PH - 30 * mm - dlogo.height)
        y = PH - 30 * mm - dlogo.height - 24 * mm
        c.setFillColor(C(B.NEELA[100])); c.setFont("PlexMono", 8.5)
        c.drawString(ML, y, f"{DESK}  ·  Research note  ·  {DOC_ID}  ·  v{DOC_VERSION}")
        c.setFillColor(C(B.KESARI[60])); c.rect(ML, y - 10 * mm, 22 * mm, 2.4, stroke=0, fill=1)
        c.setFillColor(SAFED)
        ty = y - 24 * mm
        for line in ["RSI + EMA + VWAP", "on the Nifty 50"]:
            c.setFont("Outfit-Medium", 38); c.drawString(ML, ty, line); ty -= 44
        ty -= 6
        c.setFont("Outfit-Light", 15); c.setFillColor(C(B.KAGAZ[100]))
        for line in ["An autonomous research loop, the strategy it", "produced, and why we would not trade it"]:
            c.drawString(ML, ty, line); ty -= 21
        # key figures, on navy colours
        ty -= 16 * mm
        figs = [("Selection window 2020–23", m["sharpe_is"]), ("Validation 2024–26", m["sharpe_oos"]),
                ("Sealed hold-out 2017–19", m["sharpe_pre"]), ("Nifty, 2017–19", m["bench_pre"])]
        colw = (PW - ML - MR) / 4
        for k, (lab, val) in enumerate(figs):
            x = ML + k * colw
            c.setStrokeColor(C(B.NEELA[600])); c.setLineWidth(0.6); c.line(x, ty + 30, x + colw - 10, ty + 30)
            c.setFillColor(C(B.NEELA[100])); c.setFont("Plex", 7.6); c.drawString(x, ty + 18, lab)
            c.setFillColor(C(signed_colour(val, on_navy=True))); c.setFont("Outfit-Medium", 24)
            c.drawString(x, ty - 6, B.num(val, 2))
        c.setFillColor(C(B.NEELA[100])); c.setFont("Plex", 7.6)
        c.drawString(ML, ty - 22, "Sharpe ratios of daily returns over the RBI repo rate, net of 15 bps one-way costs.")
        # foot
        c.setStrokeColor(C(B.NEELA[600])); c.line(ML, 24 * mm, PW - MR, 24 * mm)
        c.setFont("PlexMono", 7.6); c.setFillColor(C(B.NEELA[100]))
        c.drawString(ML, 18 * mm, f"RAAS Capital  ·  {m['date_long']}")
        c.drawRightString(PW - MR, 18 * mm, f"Code {m['commit']}  ·  data to {m['data_end']}")
        c.drawString(ML, 13.5 * mm, "Internal research. Hypothetical backtest results. Not investment advice.")
        c.restoreState()

    def draw_body(self, c, doc):
        c.saveState()
        c.setFillColor(PAPER); c.rect(0, 0, PW, PH, stroke=0, fill=1)
        c.setFont("PlexMono", 7.4); c.setFillColor(MUTED)
        c.drawString(ML, PH - 15 * mm, f"RAAS Capital  ·  {DESK}")
        c.drawRightString(PW - MR, PH - 15 * mm, f"{DOC_ID}  ·  {DOC_TITLE}")
        c.setStrokeColor(RULE); c.setLineWidth(0.5); c.line(ML, PH - 17.5 * mm, PW - MR, PH - 17.5 * mm)
        c.line(ML, 14.5 * mm, PW - MR, 14.5 * mm)
        c.drawString(ML, 10 * mm, "Internal research · hypothetical backtest results · not investment advice")
        c.drawRightString(PW - MR, 10 * mm, f"{doc.page}")
        c.restoreState()

    def draw_back(self, c, doc):
        c.saveState()
        c.setFillColor(NAVY); c.rect(0, 0, PW, PH, stroke=0, fill=1)
        w = 70 * mm
        d = B.logo_drawing("full-colour", w)
        renderPDF.draw(d, c, (PW - w) / 2, PH / 2 - d.height / 2 + 20 * mm)
        c.setFillColor(C(B.NEELA[100])); c.setFont("Outfit-Light", 13)
        c.drawCentredString(PW / 2, PH / 2 - d.height / 2 - 4 * mm, B.TAGLINE)
        c.setFont("PlexMono", 7.6)
        c.drawCentredString(PW / 2, 24 * mm, f"RAAS Capital  ·  {DESK}  ·  {DOC_ID}  ·  v{DOC_VERSION}  ·  {self.meta['date_long']}")
        c.restoreState()


# ---------------------------------------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------------------------------------
def load_inputs():
    j = lambda f: json.load(open(os.path.join(R, f)))  # noqa: E731
    need = ["validation.json", "lineage.json", "diagnostics.json", "equity_curves.csv", "holdout_final_returns.csv"]
    missing = [f for f in need if not os.path.exists(os.path.join(R, f))]
    if missing:
        raise FileNotFoundError(f"missing reports {missing}: run validate.py --out reports, lineage.py --holdout, "
                                "diagnose.py first")
    v, lin, dg = j("validation.json"), j("lineage.json"), j("diagnostics.json")
    eq = pd.read_csv(os.path.join(R, "equity_curves.csv"), index_col=0, parse_dates=True)
    hold = pd.read_csv(os.path.join(R, "holdout_final_returns.csv"), index_col=0, parse_dates=True)
    tsv = pd.read_csv(os.path.join(HERE, "results.tsv"), sep="\t")
    return v, lin, dg, eq, hold, tsv


LINEAGE_NOTES = {
    "baseline": "EMA20 > EMA50, close > EMA20 and > VWAP20, 50 < RSI < 70; Nifty above EMA200; top 20 equal weight",
    "keep 1": "Regime from the NIFTY's own RSI(14) with hysteresis; stand aside while it is above 80",
    "keep 2": "One cohort per regime switch-on, all confluence names, inverse-variance weights that ride; "
              "single exit: close under 0.985 × VWAP20",
    "keep 3": "Delete the close > EMA20 entry test (simpler, gate 4)",
    "keep 4": "Entry adds: no RSI peak above 80 in 20 days, EMA20 within 6% of EMA50, calm volatility rank",
    "keep 5": "Weights by inverse idiosyncratic variance",
    "keep 6": "Exits on a 2.5-sigma idiosyncratic shock and a 4× volume spike",
    "keep 7": "Trend-break stop at 0.975 × VWAP50",
    "keep 8": "Weights by (Blume beta) / idiosyncratic variance; 40% name cap, excess in NIFTYBEES",
    "keep 9": "VWAP-ladder stop (close < VWAP3 < VWAP50) replaces the VWAP50 stop",
    "keep 10": "10% single-index volatility budget from day 3; volatility-rank exit deleted",
    "keep 11": "Re-entry weights by VWAP ladder height; every weight tilted by (close / VWAP3)^20",
    "keep 12": "Budget trims by score instead of pro rata",
    "keep 13": "Re-entry cohort: top 5 by ladder height × RSI lead over the index",
    "keep 14": "One-time day-3 re-rank sells the bottom third; stretched names count at half score",
    "final": "Breadth-dip top-up of a fresh cohort with idle cash, within the budget",
}

PARAM_DOC = [
    ("Regime", [("regime_rsi_n", "NIFTY 50 RSI length for the regime switch"),
                ("regime_rsi_mid", "centre of the hysteresis band"),
                ("regime_rsi_band", "half-width: on above mid + band (57), off below mid − band (53)"),
                ("regime_rsi_max", "stand aside while the NIFTY RSI is above this")]),
    ("Entry", [("rsi_n", "stock RSI length (also the index RSI used for the re-entry lead)"),
               ("rsi_smooth", "EMA span that smooths the stock RSI"),
               ("rsi_hi", "smoothed RSI must be below this at entry"),
               ("rsi_peak", "no smoothed RSI reading above this ..."),
               ("rsi_peak_n", "... in this many days (no fresh blow-off)"),
               ("ema_fast", "fast EMA"), ("ema_slow", "slow EMA"),
               ("ema_spread", "trend cap: EMA20 at most 6% above EMA50; past it a held name is 'stretched'"),
               ("vwap_n", "VWAP window for entry and the exit band; also the volume, ramp and breadth windows"),
               ("vol_long_n", "long realised-vol window (calm filter, Bank Nifty sizing)"),
               ("vol_calm_q", "entry needs a vol20/vol120 rank below this quantile of members")]),
    ("Weights", [("vol_n", "realised-vol window (stocks, NIFTY, Bank Nifty short leg)"),
                 ("w_corr_n", "correlation window of the weight betas and the risk model"),
                 ("beta_shrink", "Blume shrinkage of beta towards 1"),
                 ("vol_power", "exponent on idiosyncratic vol in the weights (2 = variance)"),
                 ("rung_power", "tilt exponent on close / VWAP3"),
                 ("w_cap", "single-name cap; the excess is parked in NIFTYBEES"),
                 ("re_k", "re-entry cohort size"),
                 ("rsi_lag", "re-entry lead: stock RSI above (index RSI − lag)")]),
    ("Sizing", [("size_index", "index whose variance ratio scales a fresh cohort"),
                ("size_power", "exponent of the variance ratio (cap 1)"),
                ("whipsaw_days", "a re-cross after this many days off or fewer is a whipsaw ..."),
                ("whipsaw_size", "... and is bought at this fraction"),
                ("gross", "maximum gross exposure (no leverage)")]),
    ("Holding", [("vwap_exit", "sell on a close under this multiple of VWAP20"),
                 ("vwap_short_n", "short VWAP of the ladder stop and the rung tilt"),
                 ("vwap_long_n", "long VWAP of the ladder stop and the re-entry ladder height"),
                 ("corr_n", "correlation window of the shock-test beta"),
                 ("shock_k", "shock: residual return below −k daily idiosyncratic sigmas"),
                 ("vspike_k", "spike: volume above k × its previous 20-day average"),
                 ("exhaust", "sell the stock remainder once less than this share of the cohort is left"),
                 ("vol_cap", "model-volatility budget of the riding book (annualised)"),
                 ("vol_cap_delay", "the budget and the re-rank start on day vol_cap_delay + 1"),
                 ("rerank_drop", "share of the book sold by the one-time re-rank"),
                 ("stretch_score", "score multiplier for stretched names in the re-rank and trims"),
                 ("dip_k", "breadth dip: share above VWAP20 more than this below its 20-day mean")]),
]


def strategy_provenance(final_commit):
    """Which commit the listed strategy.py is, and whether its code (docstring aside) equals the final strategy's."""
    import ast
    import subprocess

    def code_only(src):
        tree = ast.parse(src)
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant):
            tree.body = tree.body[1:]
        return ast.dump(tree)
    cur = open(os.path.join(HERE, "strategy.py")).read()
    try:
        git = lambda *a: subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, check=True).stdout  # noqa: E731
        head = git("log", "-1", "--format=%h", "--", "strategy.py").strip()
        dirty = bool(git("status", "--porcelain", "--", "strategy.py").strip())
        final_src = git("show", f"{final_commit}:nse/strategy.py")
    except Exception:                                    # no git: describe the file only
        return dict(listed="working tree", same_code=None, dirty=None)
    return dict(listed=head + (" + local changes" if dirty else ""), same_code=code_only(cur) == code_only(final_src),
                dirty=dirty)


def fmt_param(v):
    if isinstance(v, str):
        return v
    if isinstance(v, float):
        for frac, s in [(1 / 3, "1/3"), (2 / 3, "2/3")]:
            if abs(v - frac) < 1e-9:
                return s
        return f"{v:g}"
    return str(v)


def build(out=DEFAULT_OUT, date=None, quick=False):
    global ST
    B.check_assets()
    B.register_reportlab_fonts()
    B.register_matplotlib_fonts()
    from reportlab import rl_config
    from reportlab.graphics import shapes as rl_shapes
    rl_config.canvas_basefontname = "Plex"           # no standard-14 fonts anywhere in the file
    rl_shapes.STATE_DEFAULTS["fontName"] = "Plex"
    from reportlab.platypus import tables as rl_tables
    rl_tables.CellStyle.fontname = "Plex"
    plt.rcParams.update(B.mpl_rc())
    ST = styles()

    import prepare as Pp
    import strategy as S_mod
    p = S_mod.PARAMS
    v, lin, dg, eq, hold, tsv = load_inputs()
    m = v["metrics"]
    base = lin[0]
    final = lin[-1]
    md = Pp.load_data()
    W = S_mod.compute_target_weights(md)
    res = Pp.run_backtest(W, md)
    date = date or dt.date.today()
    commit = final["commit"]
    prov = strategy_provenance(commit)
    if prov["listed"] == commit:
        prov_txt = f"strategy.py at commit {commit}, the final strategy"
    elif prov["same_code"]:
        prov_txt = (f"strategy.py at commit {prov['listed']}; its code is identical to the final strategy, commit "
                    f"{commit} (only the docstring changed)")
    elif prov["same_code"] is None:
        prov_txt = f"strategy.py from the working tree (final strategy: commit {commit})"
    else:
        raise RuntimeError(f"strategy.py differs from the final strategy {commit}; the reports describe {commit}")
    data_end = str(md.calendar[-1].date())
    meta = dict(sharpe_is=m["sharpe_is"], sharpe_oos=m["sharpe_oos"], sharpe_pre=final["sharpe_pre"],
                bench_pre=final["bench_sharpe_pre"], date_long=date.strftime("%d %B %Y").lstrip("0"),
                commit=commit, data_end=data_end)
    n_exp = len(tsv)
    rounds = tsv["description"].fillna("").str.extract(r"^\[r(\d+)")[0].dropna().astype(int).value_counts().sort_index()
    keeps = tsv[tsv.status == "keep"]
    summ = dg["summary"]
    s_is, s_oos = summ["2020-23"], summ["2024-26"]
    edge = dg["selection_edge_by_year"]
    yearly = v["yearly"]
    bs = v["bootstrap"]
    ps_sum = v["param_sensitivity_summary"]
    wf = v["walk_forward"]
    mc = v["monte_carlo"]
    rg = v["regime"]
    la = v["lookahead"]
    yrs_hold = len(hold) / 252.0
    hold_s = (1 + hold["strategy"]).prod() ** (1 / yrs_hold) - 1
    hold_b = (1 + hold["niftybees"].fillna(0)).prod() ** (1 / yrs_hold) - 1
    hold_c = (1 + hold["cash"]).prod() ** (1 / yrs_hold) - 1
    best_pre = max(lin, key=lambda o: o.get("sharpe_pre", -9))
    neg_oos = sum(o["sharpe_oos"] < 0 for o in lin)
    neg_pre = sum(o.get("sharpe_pre", 0) < 0 for o in lin)
    cost_tab = {r["cost_bps"]: r for r in v["cost_sensitivity"]}
    rf_oos = md.rf.loc[Pp.OOS_START:]
    cash_oos = float((1 + rf_oos).prod() ** (252 / len(rf_oos)) - 1)
    n_after_best = len(lin) - 1 - lin.index(best_pre)
    ex_start = "2023-09-05"
    trace = cohort_trace(md, W, p, ex_start)
    if trace["unexplained"]:
        raise RuntimeError(f"worked example: exits not explained by the strategy's rules: {trace['unexplained']}")

    story = [NextPageTemplate("body"), PageBreak()]

    # ---------------------------------------------------------------- contents and document control
    story += [KesariRule(), P("Contents", "part"), Paragraph("Document control and contents", ST["h1"])]
    ctl = [
        ("Document", f"{DOC_ID}, version {DOC_VERSION}"), ("Title", f"{DOC_TITLE}: {DOC_SUBTITLE.lower()}"),
        ("Date", meta["date_long"]), ("Desk", f"RAAS Capital · {DESK}"),
        ("Status", "Final research record. Not approved for allocation (see Part 8)"),
        ("Classification", "Internal. Hypothetical backtest results; not investment advice"),
        ("Strategy code", prov_txt + "; branch autoresearch/oct9-nse"),
        ("Data", f"NSE daily bars, 2016-06-01 to {data_end} (Financial Modeling Prep); point-in-time Nifty 50"),
        ("Build", "python make_pdf.py (every figure in this note is read from the repository at build time)"),
    ]
    story.append(data_table(["Field", "Value"], [[esc(a), esc(b_)] for a, b_ in ctl], [32 * mm, FW - 32 * mm],
                            zebra=False))
    story.append(Spacer(1, 14))
    toc = TableOfContents()
    toc.levelStyles = [ST["toc0"], ST["toc1"]]
    toc.dotsMinLevel = 0
    story.append(toc)

    # ---------------------------------------------------------------- summary
    story += [PageBreak()] + opener("Summary", "Executive summary", level_text="Executive summary", standfirst=
                                    "We asked an autonomous research loop to find the highest-Sharpe version of an "
                                    "RSI + EMA + VWAP strategy on NSE. It did, in sample. Out of sample, the strategy "
                                    "earns less than cash.")
    story.append(stat_tiles([
        ("Selection window", "2020–23, the loop maximised this", m["sharpe_is"], f"baseline {B.num(base['sharpe_is'])}"),
        ("Validation window", "2024 – Oct 2026, never selected on", m["sharpe_oos"], f"Nifty {B.num(m['bench_sharpe_oos'])}"),
        ("Sealed hold-out", "Apr 2017–2019, scored once", final["sharpe_pre"], f"Nifty {B.num(final['bench_sharpe_pre'])}"),
        ("Whole period", "2020 – Oct 2026", m["sharpe_full"], "selection + validation"),
    ]))
    story.append(P(f"Sharpe ratios of daily returns over the RBI repo rate, net of 15 bps one-way costs, next-open "
                   f"execution. {n_exp:,} experiments logged.", "source"))
    story.append(h2("What we did"))
    story.append(P(
        "The brief was a single image: <i>RSI + EMA + VWAP</i>. We turned it into a long-only strategy on the "
        "point-in-time Nifty 50, backtested it on NSE daily data from January 2020 to October 2026, and handed it "
        "to an autonomous research loop modelled on Karpathy's <i>autoresearch</i>: one editable strategy file, a "
        "fixed backtest harness, one metric (the 2020–2023 Sharpe ratio net of costs) and a keep-or-discard "
        f"decision on every experiment. Six parallel researchers per round ran {len(rounds)} rounds and logged "
        f"{n_exp:,} experiments; {len(lin) - 1} of them were merged into the strategy."))
    story.append(h2("What we found"))
    story += bullets([
        f"<b>In sample the loop worked.</b> The 2020–2023 Sharpe rose from {B.num(base['sharpe_is'])} to "
        f"{B.num(m['sharpe_is'])}: {B.num(m['cagr_is'] * 100, 1)}% a year with a {B.num(m['maxdd_is'] * 100, 1)}% "
        "worst drawdown. From round 2 every merge also had to pass in-sample robustness gates (both halves, the "
        "five best days removed, parameter plateaus; from round 5, leave-one-spell-out).",
        f"<b>Out of sample it did not.</b> On 2024–2026, which no selection ever used, the Sharpe is "
        f"{snum(m['sharpe_oos'])}: {B.num(m['cagr_oos'] * 100, 1)}% a year while cash earned "
        f"{B.num(cash_oos * 100, 1)}%. On the sealed April 2017 to December 2019 "
        f"hold-out, scored once after the research ended, it is {snum(final['sharpe_pre'])} against "
        f"{snum(final['bench_sharpe_pre'])} for the Nifty.",
        f"<b>It is not one unlucky strategy.</b> {neg_oos} of the {len(lin)} strategies the loop kept, baseline "
        f"included, are negative on 2024–2026, and {neg_pre} of {len(lin)} on the hold-out. The hold-out "
        f"score peaked at {best_pre['stage']} ({B.num(best_pre['sharpe_pre'])}); all {n_after_best} incumbents "
        "kept after it score lower.",
        f"<b>Costs are not the cause.</b> Before any cost the 2024–2026 Sharpe is "
        f"{snum(cost_tab[0]['sharpe_oos'])}. The edge itself is gone: {B.num(s_is['hit_rate'] * 100, 0)}% of the "
        f"{s_is['spells']} invested spells of 2020–2023 beat cash, {B.num(s_oos['hit_rate'] * 100, 0)}% of the "
        f"{s_oos['spells']} since; the names the strategy buys have lagged the average member in every year from 2024.",
    ])
    story.append(Spacer(1, 6))
    story.append(navy_card("Verdict: a measure of fit, not of expected return", [
        f"The {B.num(m['sharpe_is'])} in-sample Sharpe describes how well the rules explain 2020–2023, a "
        "crash and a strong recovery that a regime-timed, concentrated book with fast exits could exploit. "
        "The rules do not transfer to the years before or after. <b>We do not recommend allocating capital to "
        "this strategy.</b> If the desk wants a live answer, paper-trade it under the kill rules in Part 8 and "
        "judge it only on data from the next session onward.",
    ], label="Recommendation"))
    story.append(Spacer(1, 8))
    story.append(h2("What is worth keeping"))
    story += bullets([
        "The harness: point-in-time universe, next-open execution with exact drifted-state accounting, a "
        "dense lookahead detector, repo-rate cash and an honest 15 bps cost stack. It is strategy-agnostic.",
        "The protocol: hidden validation data, a sealed and pre-registered hold-out, an append-only log of "
        "every look, and a lineage scored on every window. That is what made this failure visible before any "
        "capital was at risk.",
        "The finding itself: in-sample robustness gates test stability inside one regime. They cannot "
        "substitute for data the search never saw.",
    ])

    # ---------------------------------------------------------------- part 1: the indicators
    story += [PageBreak()] + opener("Part 1", "The idea and its three indicators",
                                    "RSI measures how one-sided recent moves have been, EMA the direction of "
                                    "the trend, and VWAP where the volume actually traded. The brief asks for "
                                    "their confluence.")
    story.append(P(
        "All indicators are computed on daily bars, per stock, from data up to and including the close of the day "
        "(they are causal). The definitions below are the exact ones in <font name='PlexMono'>indicators.py</font> "
        "(Appendix B); the strategy uses nothing else."))
    story.append(h2("Relative strength index (Wilder)"))
    story.append(formula_card([
        "Δ<sub>t</sub> = C<sub>t</sub> − C<sub>t−1</sub>;   "
        "U<sub>t</sub> = max(Δ<sub>t</sub>, 0);   D<sub>t</sub> = max(−Δ<sub>t</sub>, 0)",
        "AU<sub>t</sub>, AD<sub>t</sub> = exponential averages of U and D with α = 1/n",
        "RSI<sub>t</sub> = 100 − 100 / (1 + AU<sub>t</sub> / AD<sub>t</sub>)   (100 if AD = 0; 50 if both are 0)",
    ], note=f"n = {p['rsi_n']}. For stocks the RSI is smoothed again by an EMA of span {p['rsi_smooth']} "
            "(\"smoothed RSI\"). The regime uses the raw RSI(14) of the NIFTY 50 index."))
    story.append(P(
        "Two readings matter. A smoothed RSI below 70 means the stock is not overbought at entry; a 20-day "
        "maximum below 80 means it has not had a fresh blow-off. On the index, RSI is the regime switch: the book "
        "is invested only while the NIFTY's own RSI says the market is in an up-leg, and stands aside when the "
        "index itself is overbought (above 80)."))
    story.append(h2("Exponential moving average"))
    story.append(formula_card([
        "EMA<sub>t</sub> = α · C<sub>t</sub> + (1 − α) · EMA<sub>t−1</sub>,   "
        "α = 2 / (n + 1),   seeded with the first close",
        f"Trend:  EMA{p['ema_fast']} > EMA{p['ema_slow']}   and   EMA{p['ema_fast']} < "
        f"{1 + p['ema_spread']:g} × EMA{p['ema_slow']}",
    ], note="The upper bound keeps the strategy out of names whose trend is already stretched; a held name that "
            "runs past it is marked 'stretched' and counts at half score when the book is trimmed."))
    story.append(h2("Volume-weighted average price"))
    story.append(formula_card([
        "TP<sub>t</sub> = (H<sub>t</sub> + L<sub>t</sub> + C<sub>t</sub>) / 3",
        "VWAP<sub>n,t</sub> = Σ TP<sub>s</sub> · V<sub>s</sub> / Σ V<sub>s</sub>,   "
        "s = t − n + 1 … t",
    ], note="Three windows are used: VWAP3 (where the last three sessions traded), VWAP20 (the month) and "
            "VWAP50 (the quarter)."))
    story.append(P(
        "A note on VWAP. Intraday traders use a session VWAP that resets every morning. That needs intraday bars; "
        "the vendor's NSE intraday history starts in 2023, so it cannot cover 2020–2026. The strategy "
        "therefore uses rolling multi-day VWAPs of the typical price, which keep the idea (the average price "
        "paid, weighted by volume) at a daily horizon. Close above VWAP20 means recent buyers are in profit; "
        "the VWAP <i>ladder</i> (VWAP3 against VWAP50) says whether short-term trading is above or below the "
        "quarter's average cost."))
    ex_tk = trace["formation"][0][0]
    exit_day = next((e[0] for e in trace["events"] if ex_tk in e[2]), trace["t1"])
    story.append(figure(
        chart_indicators(md, p, ex_tk, "2023-07-10", str((pd.Timestamp(exit_day) + pd.Timedelta(days=12)).date()),
                         trace["t0"], exit_day),
        f"Figure 1. The three indicators on {ex_tk}, the largest position of the cohort traced in Part 4.7. The name "
        f"entered on the close of {trace['t0']:%d %b %Y} (EMA20 above EMA50 by less than 6%, close above VWAP20, "
        "smoothed RSI under 70) and left on the exit close marked.",
        "Source: FMP daily bars; indicators.py; RAAS Capital Quant Desk."))
    story.append(h2("Supporting measures"))
    story.append(P("The final strategy adds risk measures that the three indicators do not provide. All are "
                   "standard and all are computed from the same daily closes:"))
    story.append(data_table(
        ["Measure", "Definition", "Used for"],
        [["Realised volatility σ<sub>n</sub>", "standard deviation of daily returns over n days × √252",
          "calm filter, risk model, Bank Nifty sizing"],
         ["Correlation ρ<sub>n</sub>", "n-day correlation of the stock's daily return with the NIFTY 50's",
          "beta, idiosyncratic volatility"],
         ["Beta β", "ρ · σ<sub>stock</sub> / σ<sub>NIFTY</sub>", "shock test, risk model"],
         ["Blume beta β<sup>B</sup>", f"max(0, (1 − {fmt_param(p['beta_shrink'])}) · β + "
          f"{fmt_param(p['beta_shrink'])}), with a {p['w_corr_n']}-day correlation", "weights, risk model"],
         ["Idiosyncratic vol σ<sub>ε</sub>", "σ · √(1 − ρ²)",
          "weights, shock test, risk model"],
         ["Breadth", "share of eligible members whose close is above their VWAP20", "dip top-up"]],
        [40 * mm, 82 * mm, FW - 122 * mm]))

    # ---------------------------------------------------------------- part 2: data and harness
    story += [PageBreak()] + opener("Part 2", "Data, universe and the backtest harness",
                                    "A strategy can only be as honest as the harness that scores it. The harness "
                                    "is fixed: the research loop could not edit it.")
    story.append(h2("Universe and data"))
    story += bullets([
        "<b>Universe.</b> The point-in-time Nifty 50: a stock can be held only on days it is an index member. "
        "Membership comes from the <i>Historical Nifty 50 Constituent Weights</i> dataset (NSE Indices, CC "
        "BY-NC-SA 4.0), extended with NSE's official changes to September 2026 and re-dated to NSE effective "
        "dates. 66 stocks were members at some point in 2020–2026; the sealed hold-out adds the 11 that left "
        "before 2020. There is no survivorship bias.",
        "<b>Prices.</b> Financial Modeling Prep end-of-day bars from June 2016 (3.5 years of indicator warm-up "
        "before 2020), split and bonus adjusted. The four demergers in the window (Reliance–Jio Financial, "
        "ITC–ITC Hotels, Tata Motors–TMCV, HUL–Kwality Wall's) are corrected to the NSE special-"
        "session prices. Spin-offs that sat in the index at a frozen price are not tradable.",
        "<b>Index and ETF.</b> NIFTY 50 and BANK NIFTY are signals only and can never be traded. NIFTYBEES is the "
        "tradable index proxy; its vendor opens are replaced by the index's open gap applied to the ETF's "
        "previous close.",
        "<b>Calendar.</b> A trading day is a day on which at least 30 stocks have a bar, minus Muhurat and the "
        "two short 2024 disaster-recovery sessions. Seven regular sessions the vendor's index series lacks are "
        "rebuilt from the constituents and pinned to NSE's published closes.",
        "<b>Cash.</b> Idle capital earns the RBI repo rate, accrued daily. Sharpe ratios are measured over it.",
    ])
    story.append(h2("Execution: decision at the close of T, fill at the open of T+1"))
    story.append(P(
        "The strategy returns target weights computed from data up to the close of day <i>t</i>. The harness "
        "executes them at the <b>open of day t+1</b>, never at the same close. It keeps exact portfolio state in "
        "weight space: overnight, the holdings earn their open-to-previous-close gap and cash earns half a day "
        "of repo; at the open it trades from the <i>drifted</i> weights to the new target and charges costs on "
        "the traded fraction; intraday, the new book earns its open-to-close return and cash the other half "
        "day. The drifted state at the close, not the target, carries into the next day."))
    story.append(data_table(
        ["Cost item, one way", "bps of traded value", "Note"],
        [["Securities transaction tax (delivery)", "10.0", "0.1% on each side of a delivery trade"],
         ["Exchange, SEBI, stamp duty, GST", "~1.0", "aggregate of the statutory and exchange charges"],
         ["Slippage and impact", "~4.0", "large caps, modest size, market-on-open"],
         ["<b>Total charged by the harness</b>", "<b>15.0</b>", "applied to every rupee traded, both sides"]],
        [70 * mm, 32 * mm, FW - 102 * mm], num_cols=(1,)))
    story.append(P("Harness rules the strategy cannot override: long-only, gross exposure at most 100%, members "
                   "only (a name leaving the index is sold at the next open), a stock with no fresh bar keeps its "
                   "position (it cannot trade that day), and a stock that stops trading is liquidated at its last "
                   "close.", "body"))
    story.append(h2("Lookahead detector"))
    story.append(P(
        f"Every run recomputes the weights on data truncated at about 60 dates, and on about 30 dates where every "
        f"later bar (prices, volumes, membership, index, rates) is replaced by a random permutation of later bars. "
        f"The weights up to each date must be identical to the full-sample run. The final strategy passes "
        f"{la['n_checks']} checks ({la['n_checks_with_holdings']} with open positions), maximum difference "
        f"{la['max_abs_diff']:g}. Any use of a future bar, including 'neutral when the future is missing' "
        f"constructions, fails the run."))
    story.append(h2("Metrics"))
    story.append(formula_card([
        "Sharpe = mean(r<sub>t</sub> − rf<sub>t</sub>) / sd(r<sub>t</sub> − rf<sub>t</sub>) · √252",
        "r<sub>t</sub>: daily portfolio return net of costs;   rf<sub>t</sub>: daily repo accrual",
    ], note="CAGR uses 252 sessions a year. Turnover is the fraction of portfolio value traded per year, both sides. "
            "Exposure is the average gross weight in stocks and NIFTYBEES."))

    # ---------------------------------------------------------------- part 3: research process
    story += [PageBreak()] + opener("Part 3", "How the research was run",
                                    "One file to edit, one number to raise, and a set of gates that tried to "
                                    "stop the loop from buying noise.")
    story.append(h2("The loop"))
    story += numbered([
        "A researcher changes <font name='PlexMono'>strategy.py</font> with one idea and commits it.",
        "<font name='PlexMono'>run.py</font> builds the weights, runs the lookahead detector and scores the "
        "2020–2023 window. It prints in-sample numbers only.",
        "If the Sharpe rises and every gate passes, the commit is kept and becomes the new incumbent; otherwise "
        "it is reset. Every experiment, kept or not, is a row in <font name='PlexMono'>results.tsv</font>.",
        "Each round, six researchers work in parallel on separate git worktrees, each through one lens (for example entry, "
        "exits, weights, regime, risk, simplification). The lead verifies, merges the winners best-first, and "
        "runs a combination pass on the merged book.",
    ])
    story.append(h2("Protocol and gates"))
    story.append(data_table(
        ["Gate", "Rule for a keep (all in sample)", "From"],
        [["Sanity", "lookahead check passes; invested ≥ 30% of days; ≥ 5 names on average; turnover "
          "≤ 60× a year; max drawdown better than −45%", "round 1"],
         ["1  Both halves", "neither 2020–21 nor 2022–23 Sharpe more than 0.10 below the incumbent's", "round 2"],
         ["2  Not a few days", "Sharpe with the 5 best days removed strictly above the incumbent's", "round 2"],
         ["3  Plateau", "every new or moved parameter: both ±20% neighbours also beat the incumbent", "round 2"],
         ["4  Simplicity", "a deletion that costs under 0.02 of Sharpe and passes gates 1–2 is a keep", "round 2"],
         ["5  Not one episode", "with any single invested spell removed, the candidate still beats the incumbent",
          "round 5"]],
        [30 * mm, FW - 50 * mm, 20 * mm]))
    story.append(Spacer(1, 6))
    story.append(P(
        "<b>Why protocol v2 exists.</b> After round 1 the lead looked at the 2024–2026 results of the round-1 "
        "candidates (they had been logged as report-only columns). Every candidate lost money there, even before "
        "costs. From round 2 the 2024–2026 window was hidden from researchers, the gates above were added, "
        "and a second, <b>sealed hold-out</b> was built: April 2017 to December 2019, with its own point-in-time "
        "Nifty 50. The set of strategies to be scored on it was fixed in advance (the baseline, every merged "
        "incumbent, the final strategy) and each look is appended to an audit log. Because the lead had seen "
        "2024–2026, this note calls it a <i>validation</i> window, not a hold-out."))
    story.append(P(
        f"<b>Stopping rule</b> (declared before round 5): at most 8 rounds, or stop after two consecutive rounds "
        f"that add less than 0.10 of Sharpe. Round 8 added {B.num(final['sharpe_is'] - lin[-3]['sharpe_is'])}, so "
        f"the loop stopped on the round limit."))
    story.append(h2("Experiments by round"))
    story.append(data_table(
        ["Round"] + [str(k) for k in rounds.index] + ["Total"],
        [["Experiments"] + [f"{n:,}" for n in rounds.values] + [f"{n_exp:,}"]],
        [24 * mm] + [(FW - 44 * mm) / len(rounds)] * len(rounds) + [20 * mm],
        num_cols=tuple(range(1, len(rounds) + 2)), zebra=False))
    story.append(P(f"{n_exp - rounds.sum()} further rows are the baseline and harness re-measurements. "
                   f"{int((tsv.status == 'crash').sum())} experiments crashed and were logged as such.", "source"))
    story.append(CondPageBreak(120 * mm))
    story.append(h2("The lineage: every kept strategy on every window"))
    rows = []
    for o in lin:
        rows.append([esc(o["stage"]), f"<font name='PlexMono'>{esc(o['commit'])}</font>",
                     esc(LINEAGE_NOTES.get(o["stage"], "")), B.num(o["sharpe_is"]),
                     snum(o["sharpe_oos"]), snum(o.get("sharpe_pre"))])
    story.append(data_table(["Stage", "Commit", "What it added", "2020–23", "2024–26", "2017–19"], rows,
                            [16 * mm, 17 * mm, FW - 87 * mm, 18 * mm, 18 * mm, 18 * mm], num_cols=(3, 4, 5)))
    story.append(P("All strategies re-scored on the final harness. The 2017–19 column is the first and only look "
                   "at those years for each strategy (reports/holdout_log.tsv).", "source"))
    story.append(figure(chart_lineage(lin),
                        "Figure 2. In-sample Sharpe rose at every stage; the two windows the loop never used did not "
                        "follow. The dashed line is buy-and-hold Nifty over the hold-out."))

    # ---------------------------------------------------------------- part 4: the strategy
    story += [PageBreak()] + opener("Part 4", "The final strategy, rule by rule",
                                    "Everything below is what strategy.py does, in the order it does it. The "
                                    "source is reproduced in full in Appendix A.")
    story.append(P(
        "In one paragraph: the book is in the market only while the NIFTY 50's own RSI says the index is in an "
        "up-leg. On the first day of each up-leg it buys one basket (a <i>cohort</i>) of Nifty 50 stocks that "
        "show the RSI + EMA + VWAP confluence, weighted towards names whose risk is mostly market risk. It then "
        "lets the cohort ride without rebalancing or adding names, sells names one by one on VWAP and news "
        "signals, keeps the book inside a volatility budget, and goes entirely to cash when the up-leg ends."))
    story.append(figure(flow_diagram(), "Figure 3. One day of the strategy. Signals use data to the close of day t; "
                                        "the harness trades at the open of day t+1."))
    story.append(h2("4.1  Regime: when to be in the market"))
    on_lvl = p["regime_rsi_mid"] + p["regime_rsi_band"]
    off_lvl = p["regime_rsi_mid"] - p["regime_rsi_band"]
    story.append(formula_card([
        f"Switch<sub>t</sub> = on if RSI<sup>N</sup><sub>t</sub> > {on_lvl:g};   off if RSI<sup>N</sup><sub>t</sub> < "
        f"{off_lvl:g};   otherwise unchanged",
        f"In market<sub>t</sub> = Switch<sub>t</sub> and RSI<sup>N</sup><sub>t</sub> ≤ {p['regime_rsi_max']:g}",
    ], note=f"RSI<sup>N</sup>: Wilder RSI({p['regime_rsi_n']}) of the NIFTY 50 close. Not in market: the target is "
            "100% cash, executed at the next open."))
    story.append(P(
        "The hysteresis band (on above 57, off below 53) stops the book from flickering in and out around a single "
        "threshold. The stand-aside above 80 keeps it out of an index that is already overbought; when the index "
        "RSI falls back to 80 or below while the switch is still on, the book re-enters with a different cohort "
        "(4.3)."))
    story.append(figure(chart_regime(md, S_mod, p, "2023"),
                        "Figure 4. The regime in 2023. Shaded: in market. The switch turns on above 57 and stays on "
                        "until the RSI falls below 53; above 80 the book stands aside."))
    story.append(h2("4.2  Entry: which names qualify"))
    story.append(P("On a formation day (the first day <i>in market</i> after a day out), a stock qualifies if "
                   "every one of these holds at that close:"))
    story.append(data_table(
        ["Test", "Rule", "Why"],
        [["Member, tradable", "Nifty 50 member today with a fresh bar", "point-in-time universe"],
         ["Trend", f"EMA{p['ema_fast']} > EMA{p['ema_slow']} and EMA{p['ema_fast']} < "
          f"{1 + p['ema_spread']:g} × EMA{p['ema_slow']}", "in an up-trend, not yet stretched"],
         ["VWAP", f"close > VWAP{p['vwap_n']}", "the month's buyers are in profit"],
         ["RSI level", f"smoothed RSI < {p['rsi_hi']:g}", "not overbought"],
         ["RSI peak", f"no smoothed RSI above {p['rsi_peak']:g} in the last {p['rsi_peak_n']} days", "no fresh blow-off"],
         ["Calm", f"vol{p['vol_n']}/vol{p['vol_long_n']} below the {fmt_param(p['vol_calm_q'])} quantile of members",
          "volatility not expanding"],
         ["No exit today", "none of the exit rules in 4.5 would sell it at this close", "never buy what you would sell"],
         ["Positive weight", "its weight score (4.3) is above zero", "weights need data"]],
        [30 * mm, 78 * mm, FW - 108 * mm]))
    story.append(h2("4.3  Weights: how much of each"))
    story.append(h3("Fresh cohort (the switch has just turned on)"))
    story.append(formula_card([
        "score<sub>i</sub> = β<sup>B</sup><sub>i</sub> / σ<sub>ε,i</sub><sup>2</sup>  ×  "
        f"min(1, a<sub>i</sub> / {p['vwap_n']})  ×  (C<sub>i</sub> / VWAP3<sub>i</sub>)<sup>{p['rung_power']:g}</sup>",
        "w<sub>i</sub> = g · score<sub>i</sub> / Σ score,   then capped",
    ], note=f"β<sup>B</sup>: Blume beta ({p['w_corr_n']}-day correlation, shrunk one third towards 1). "
            f"σ<sub>ε</sub>: idiosyncratic vol ({p['vol_n']}-day). a<sub>i</sub>: sessions since the name's "
            "last shock or volume spike (no record: weight not reduced). g: the cohort's gross size (4.4)."))
    story.append(P(
        "The first term is the single-index mean-variance weight for a timed bet on the market's up-leg: it buys "
        "market exposure (beta) per unit of stock-specific risk. The second term buys names with recent news "
        "small and lets them grow back over a month. The third, with a steep exponent, tilts towards the "
        "names closest to or above the bottom rung of their VWAP ladder."))
    story.append(h3("Re-entry cohort (after the stand-aside above 80)"))
    story.append(formula_card([
        f"h<sub>i</sub> = max(0, VWAP20<sub>i</sub> / VWAP50<sub>i</sub> − 1)  ×  "
        f"max(0, sRSI<sub>i</sub> − (sRSI<sup>N</sup> − {p['rsi_lag']:g}))",
        f"buy the {p['re_k']} names with the largest h (h > 0 only); w<sub>i</sub> = h<sub>i</sub> / Σ h,  "
        "gross 100%",
    ], note="sRSI: smoothed RSI(14) of the stock; sRSI<sup>N</sup>: the same on the NIFTY 50. A re-entry is "
            "momentum inside a hot market: names whose month VWAP leads their quarter VWAP and whose RSI keeps "
            "up with the index."))
    story.append(h3("Single-name cap"))
    story.append(P(f"No stock may exceed {p['w_cap'] * 100:g}% of a cohort. The share above the cap is parked in "
                   "NIFTYBEES, which holds index exposure until the regime switches off and is never trimmed."))
    story.append(h2("4.4  Size: how much of the book"))
    story.append(formula_card([
        f"g = min(1, (σ<sub>{p['vol_long_n']}</sub> / σ<sub>{p['vol_n']}</sub>)<sup>{p['size_power']:g}</sup>"
        f" of {p['size_index']})   for a fresh cohort",
        f"g ← {p['whipsaw_size']:g} · g   if the book was out for {p['whipsaw_days']} sessions or fewer (a whipsaw)",
        "g = 1   for a re-entry cohort",
    ], note="Bank Nifty is the most liquid, most leveraged sector. When it has just become more volatile than its "
            "six-month norm, a new up-leg is less trustworthy and the cohort is bought smaller."))
    story.append(h2("4.5  While the cohort rides"))
    story.append(P("Weights drift with prices; nothing is rebalanced and no name is added until the next formation "
                   "day. Each close, in this order:"))
    story.append(data_table(
        ["Step", "Rule"],
        [["1  Exits", f"sell a held stock if (a) close < {p['vwap_exit']:g} × VWAP{p['vwap_n']}; (b) the VWAP "
          f"ladder breaks: close < VWAP{p['vwap_short_n']} < VWAP{p['vwap_long_n']}; (c) an idiosyncratic shock: "
          f"r<sub>i</sub> − β<sub>i</sub> r<sub>N</sub> < −{p['shock_k']:g} · "
          f"σ<sub>ε,i</sub> / √252 (yesterday's {p['corr_n']}-day beta and vol); (d) a volume spike: "
          f"volume > {p['vspike_k']:g} × its average over the previous {p['vwap_n']} sessions; or it leaves the index"],
         ["2  Exhaustion", f"if the names still held carry less than {p['exhaust'] * 100:g}% of the cohort's "
          "formation stock weight, sell the stock remainder (NIFTYBEES rides on)"],
         ["3  Re-rank", f"once, on the close of day {p['vol_cap_delay'] + 1} after formation: if three or more stocks "
          f"are held, sell the bottom {fmt_param(p['rerank_drop'])} (rounded down) by score (fresh: β<sup>B</sup>/"
          f"σ<sub>ε</sub><sup>2</sup>; re-entry: h); a stretched name counts at {p['stretch_score']:g}× its score"],
         ["4  Vol budget", f"from day {p['vol_cap_delay'] + 1}: if the model vol of the book exceeds "
          f"{p['vol_cap'] * 100:g}%, name i keeps min(1, λ · score<sub>i</sub>) of its weight with "
          "λ set by bisection so the model vol equals the budget: the names the cohort's own weighting likes "
          "least go first. Trims never add"],
         ["5  Dip top-up", f"fresh cohorts only: if breadth was more than {p['dip_k'] * 100:g} points below its "
          f"{p['vwap_n']}-day mean yesterday and rises today, top up the riding stocks pro rata with idle cash, as "
          "far as the vol budget allows"],
         ["Regime off", "everything is sold at the next open"]],
        [24 * mm, FW - 24 * mm]))
    story.append(formula_card([
        "σ<sub>P</sub> = √( (Σ w<sub>i</sub> β<sup>B</sup><sub>i</sub>)<sup>2</sup> · "
        "σ<sub>N</sub><sup>2</sup> + Σ w<sub>i</sub><sup>2</sup> σ<sub>ε,i</sub><sup>2</sup> )",
    ], note="The single-index risk model of the vol budget. NIFTYBEES has beta 1 and no idiosyncratic risk. "
            "σ<sub>N</sub>: 20-day NIFTY 50 volatility."))
    story.append(h2("4.6  Parameter register"))
    prow = []
    for block, items in PARAM_DOC:
        for k, (name, desc) in enumerate(items):
            prow.append([esc(block) if k == 0 else "", f"<font name='PlexMono'>{esc(name)}</font>",
                         f"<font name='PlexMono'>{esc(fmt_param(p[name]))}</font>", esc(desc)])
    documented = {n for _, items in PARAM_DOC for n, _ in items}
    assert documented == set(p), f"parameter register out of date: {set(p) ^ documented}"
    story.append(data_table(["Block", "Parameter", "Value", "Meaning"], prow, [18 * mm, 30 * mm, 20 * mm, FW - 68 * mm],
                            compact=True, keep=False))
    story.append(P(f"All {len(p)} entries of PARAMS in strategy.py, checked against the code at build time.", "source"))
    story.append(CondPageBreak(70 * mm))
    story.append(h2("4.7  A cohort traced through the backtest"))
    form_rows = []
    for tk, wt, d_vw, d_ema, srsi, rung in trace["formation"]:
        if d_vw is None:
            form_rows.append([esc(tk), B.num(wt * 100, 1), "–", "–", "–", "–"])
        else:
            form_rows.append([esc(tk), B.num(wt * 100, 1), B.num(d_vw * 100, 1, sign=True),
                              B.num(d_ema * 100, 1, sign=True), B.num(srsi, 1), B.num(rung * 100, 1, sign=True)])
    story.append(P(
        f"The cohort formed on the close of <b>{trace['t0']:%d %B %Y}</b>, a fresh start of the regime, bought at "
        f"the open of the next session with gross {B.num(trace['gross0'] * 100, 0)}%. It held for "
        f"{trace['spell_days']} sessions. The weights favour the names with the most market risk per unit of "
        "stock-specific risk; the rung tilt pushed the largest weights to names trading above their three-day VWAP."))
    story.append(data_table(["Stock", "Weight, %", "Close vs VWAP20, %", "EMA20 vs EMA50, %", "Smoothed RSI",
                             "Close vs VWAP3, %"],
                            form_rows, [30 * mm, 24 * mm, 29 * mm, 29 * mm, 29 * mm, FW - 141 * mm],
                            num_cols=(1, 2, 3, 4, 5)))
    story.append(P("Values at the formation close. All six columns come from the backtest run at build time.",
                   "source"))
    ev_rows = [[f"{d:%d %b}", str(day), esc(txt), B.num(g * 100, 1), str(n)] for d, day, txt, g, n in trace["events"]]
    story.append(KeepTogether([
        data_table(["Close", "Day", "Event", "Gross, %", "Names"], ev_rows,
                   [16 * mm, 11 * mm, FW - 61 * mm, 18 * mm, 16 * mm], num_cols=(1, 3, 4), keep=False),
        P("Each event is a signal at that close, traded at the next open. Day counts sessions after the formation "
          "close. Reasons are recomputed from the strategy's own formulas (exit flags, exhaustion from the "
          "formation weights, the day-3 re-rank); the build fails if any sale is left unexplained.", "source")]))
    # ---------------------------------------------------------------- part 5: results
    story += [PageBreak()] + opener("Part 5", "Results",
                                    "Net of costs, at the next open, against buy-and-hold and against cash.")
    story.append(figure(chart_equity(eq, res["exposure"].loc["2020-01-01":]),
                        "Figure 5. Growth of 1, log scale, and the share of capital invested. The book is in stocks on "
                        f"{B.num(m['pct_days_invested_full'] * 100, 0)}% of sessions and invests "
                        f"{B.num(m['exposure_full'] * 100, 0)}% of capital on average; the rest earns the repo rate.",
                        "Source: RAAS Capital Quant Desk backtest; NIFTYBEES at its traded price."))
    hdr = ["Window", "Sharpe", "CAGR", "Max DD", "Turnover", "Exposure", "Names", "Nifty Sharpe"]
    wrows = []
    for lab, sfx in [("2020–23 selection", "is"), ("2024–26 validation", "oos"), ("2020–26 whole", "full")]:
        wrows.append([esc(lab), snum(m[f"sharpe_{sfx}"]), snum(m[f"cagr_{sfx}"], 1, pct=True),
                      snum(m[f"maxdd_{sfx}"], 1, pct=True), B.num(m[f"turnover_yr_{sfx}"], 1),
                      B.num(m[f"exposure_{sfx}"] * 100, 0) + "%", B.num(m[f"n_names_avg_{sfx}"], 1),
                      snum(m[f"bench_sharpe_{sfx}"])])
    wrows.append([esc("2017–19 sealed hold-out"), snum(final["sharpe_pre"]), snum(final["cagr_pre"], 1, pct=True),
                  snum(final["maxdd_pre"], 1, pct=True), "–", "–", "–", snum(final["bench_sharpe_pre"])])
    story.append(data_table(hdr, wrows, [40 * mm] + [(FW - 40 * mm) / 7] * 7, num_cols=tuple(range(1, 8))))
    story.append(P("Names: average number of positions on invested days. Turnover: both sides, multiples of capital.",
                   "source"))
    story.append(figure(chart_drawdown(eq), "Figure 6. Drawdowns. The strategy's worst fall over 2020–2026 is "
                        f"{B.num(m['maxdd_full'] * 100, 1)}%; the index's is several times larger, because the "
                        "strategy is in cash most of the time."))
    story.append(CondPageBreak(110 * mm))
    story.append(h2("Year by year"))
    yrows = []
    for r_ in yearly:
        yrows.append([str(r_["year"]) + ("*" if r_["year"] == 2026 else ""), snum(r_["strat_ret"], 1, pct=True),
                      snum(r_["strat_sharpe"]), snum(r_["strat_maxdd"], 1, pct=True),
                      B.num(r_["exposure"] * 100, 0) + "%", snum(r_["bees_ret"], 1, pct=True), snum(r_["nifty_sharpe"]),
                      B.num(r_["rf"] * 100, 1) + "%"])
    story.append(data_table(["Year", "Return", "Sharpe", "Max DD", "Exposure", "NIFTYBEES", "Nifty Sharpe", "Cash"],
                            yrows, [18 * mm] + [(FW - 18 * mm) / 7] * 7, num_cols=tuple(range(1, 8))))
    story.append(P("* 2026 to 9 October. Cash: the repo-rate accrual earned by idle capital that year.", "source"))
    story.append(figure(chart_yearly(yearly), "Figure 7. Calendar-year returns. Green and red mark the strategy's "
                                              "up and down years; NIFTYBEES in neela."))
    story.append(figure(chart_rolling(res["ret"].loc["2020-01-01":], md.rf),
                        "Figure 8. Rolling 126-session Sharpe ratio of the strategy. Shaded: the validation window."))

    # ---------------------------------------------------------------- part 6: robustness
    story += [PageBreak()] + opener("Part 6", "Robustness checks",
                                    "Each test asks a different question of the same result. The in-sample "
                                    "answers are reassuring; the out-of-sample answers are not.")
    story.append(h2("Costs"))
    story.append(data_table(["One-way cost, bps", "2020–23", "2024–26", "2020–26", "CAGR 2020–26",
                             "Max DD 2020–26"],
                            [[str(r_["cost_bps"]), snum(r_["sharpe_is"]), snum(r_["sharpe_oos"]), snum(r_["sharpe_full"]),
                              snum(r_["cagr_full"], 1, pct=True), snum(r_["maxdd_full"], 1, pct=True)]
                             for r_ in v["cost_sensitivity"]],
                            [34 * mm] + [(FW - 34 * mm) / 5] * 5, num_cols=(1, 2, 3, 4, 5)))
    story.append(P(f"At {B.num(m['turnover_yr_is'], 1)}× turnover a year each extra 10 bps costs about "
                   "0.2 of in-sample Sharpe. The in-sample edge survives 40 bps; 2024–2026 is negative at every "
                   "cost level, including zero.", "body"))
    story.append(h2("Sampling error"))
    story.append(data_table(["Window", "Sharpe", "95% block-bootstrap interval", "P(Sharpe > 0)"],
                            [[esc(lab), snum(bs[k]["sharpe"]),
                              f"{B.num(bs[k]['ci95'][0])} to {B.num(bs[k]['ci95'][1])}",
                              B.num(bs[k]["p_sharpe_gt_0"] * 100, 0) + "%"]
                             for k, lab in [("is", "2020–23"), ("oos", "2024–26"), ("full", "2020–26")]],
                            [40 * mm, 30 * mm, 60 * mm, FW - 130 * mm], num_cols=(1, 2, 3)))
    story.append(P("Stationary block bootstrap (Politis and Romano), mean block 20 sessions, 2,000 resamples.",
                   "source"))
    dsr = bs["is"]["deflated"]
    story.append(h2("Multiple testing"))
    story.append(P(
        f"The deflated Sharpe ratio corrects the in-sample Sharpe for the number of trials. Over "
        f"{dsr['n_trials']:,} logged trials the hurdle is {B.num(dsr['sr0_ann'])}, and the deflated probability "
        f"that the true Sharpe is above zero comes out at {B.num(dsr['dsr'], 3)}. That figure assumes independent "
        "trials. These were sequential refinements of one design, so the effective number of trials and the "
        "dispersion between them are both misstated; the statistic overstates confidence. The out-of-sample "
        "record is the better guide."))
    story.append(h2("Parameter perturbations"))
    story.append(P(
        f"Each of the {ps_sum['n_perturbations']} perturbations moves one parameter by ±10% or ±20% "
        f"(bands are moved through their gap to 1). Median in-sample Sharpe {B.num(ps_sum['median_sharpe_is'])} "
        f"against {B.num(m['sharpe_is'])} for the final strategy; {B.num(ps_sum['frac_within_0.2_of_base_is'] * 100, 0)}% "
        f"within 0.2 of it. The median 2024–2026 Sharpe across the same perturbations is "
        f"{snum(ps_sum['median_sharpe_oos'])}: no nearby parameter set rescues the validation window."))
    story.append(figure(chart_perturb(v["param_sensitivity"]),
                        "Figure 9. Distribution of Sharpe ratios across one-at-a-time parameter perturbations."))
    story.append(h2("Walk-forward parameter choice"))
    story.append(data_table(["Test year", "Chosen on prior years", "Train Sharpe", "Test Sharpe", "Test return"],
                            [[str(y["test_year"]), f"<font name='PlexMono'>{esc(y['chosen'])}</font>",
                              B.num(y["sharpe_train"]), snum(y["sharpe_test"]), snum(y["ret_test"], 1, pct=True)]
                             for y in wf["years"]],
                            [24 * mm, 50 * mm, 30 * mm, 30 * mm, FW - 134 * mm], num_cols=(2, 3, 4)))
    story.append(P(f"Each year picks the best perturbation on the years before it. Concatenated 2021–2026 Sharpe "
                   f"{B.num(wf['sharpe_concat'])}, CAGR {B.num(wf['cagr_concat'] * 100, 1)}%. This tests the choice "
                   "of parameters, not of the rules, which were chosen on 2020–2023.", "source"))
    story.append(h2("Monte Carlo and regimes"))
    story.append(P(
        f"Resampling the 2020–2026 daily returns in blocks gives a median terminal wealth of "
        f"{B.num(mc['terminal_wealth_p50'])} (5th–95th percentile {B.num(mc['terminal_wealth_p5'])} to "
        f"{B.num(mc['terminal_wealth_p95'])}) and a median maximum drawdown of {B.num(mc['maxdd_p50'] * 100, 1)}%. "
        "This describes the variability of the backtest path; it inherits the in-sample years and says nothing "
        "about whether the edge persists. Split by the Nifty against its 200-day EMA, the Sharpe is "
        f"{B.num(rg['sharpe_above'])} on {rg['days_above']:,} days above and {B.num(rg['sharpe_below'])} on "
        f"{rg['days_below']:,} days below."))

    # ---------------------------------------------------------------- part 7: out of sample
    story += [PageBreak()] + opener("Part 7", "Out of sample: what broke",
                                    "Two windows the search never touched, and a diagnosis of why the rules "
                                    "stopped working.")
    story.append(h2("The validation window, 2024 to October 2026"))
    oos_years = [r_ for r_ in yearly if r_["year"] >= 2024]
    story.append(P(
        "Year by year: " + "; ".join(f"{r_['year']}{'*' if r_['year'] == 2026 else ''} return "
                                     f"{snum(r_['strat_ret'], 1, pct=True)} at a Sharpe of {snum(r_['strat_sharpe'])}, "
                                     f"against {snum(r_['bees_ret'], 1, pct=True)} for NIFTYBEES"
                                     for r_ in oos_years) +
        f". Over the window the strategy compounds at {B.num(m['cagr_oos'] * 100, 1)}% a year while cash earned more; "
        f"its Sharpe over cash is {snum(m['sharpe_oos'])}."))
    story.append(h2("The sealed hold-out, April 2017 to December 2019"))
    story.append(P(
        f"Point-in-time Nifty 50 of those years, including the 11 names that left the index before 2020; scored "
        f"once, after the research ended, for the pre-registered lineage only. The final strategy grew "
        f"{B.num(hold_s * 100, 1)}% a year, the Nifty {B.num(hold_b * 100, 1)}%, and cash {B.num(hold_c * 100, 1)}%. "
        f"Sharpe over cash: {snum(final['sharpe_pre'])} against {snum(final['bench_sharpe_pre'])} for the index. "
        f"By year: 2017 {snum(final['sharpe_2017'])}, 2018 {snum(final['sharpe_2018'])}, "
        f"2019 {snum(final['sharpe_2019'])}."))
    story.append(figure(chart_holdout(hold), "Figure 10. The sealed hold-out. The strategy keeps pace with the index "
                                            "in 2017 and then stalls below cash."))
    story.append(h2("Diagnosis: the edge was in 2020–2023, not in the rules"))
    story.append(data_table(
        ["Window", "Spells", "Won", "Mean excess", "Median", "Nifty up", "Nifty down", "Cost drag/yr"],
        [[esc(k), str(s["spells"]), B.num(s["hit_rate"] * 100, 0) + "%", snum(s["mean_spell_excess"], 2, pct=True),
          snum(s["median_spell_excess"], 2, pct=True), snum(s["mean_excess_when_nifty_up"], 2, pct=True),
          snum(s["mean_excess_when_nifty_down"], 2, pct=True), B.num(s["cost_drag_per_yr"] * 100, 2) + "%"]
         for k, s in summ.items()],
        [24 * mm] + [(FW - 24 * mm) / 7] * 7, num_cols=tuple(range(1, 8))))
    story.append(P("A spell runs from a regime switch-on to the switch-off. Excess: the spell's return over cash. "
                   "Nifty up/down: mean excess of spells over which the index rose or fell.", "source"))
    story.append(figure(chart_spells(dg["spells"]),
                        "Figure 11. Every invested spell, in date order. In 2020–2023 the spells that made "
                        "money were large and frequent; since 2024 they are small and outnumbered."))
    story.append(P(
        f"In 2020–2023 the book won {B.num(s_is['hit_rate'] * 100, 0)}% of its spells and made money over cash "
        f"even in spells where the index fell ({snum(s_is['mean_excess_when_nifty_down'], 2, pct=True)} on average): "
        "the stock selection added value on top of the timing. Since 2024 it wins "
        f"{B.num(s_oos['hit_rate'] * 100, 0)}% of spells, loses when the index falls "
        f"({snum(s_oos['mean_excess_when_nifty_down'], 2, pct=True)}) and barely gains when it rises. The "
        "selection edge (the next-20-session return of the names a cohort bought, less the average member's on "
        "the same days) shows the same break:"))
    story.append(figure(chart_edge(edge), "Figure 12. Selection edge by year. Positive in three of the four selection "
                                          "years, negative in every year since."))
    story.append(P(
        "The loop could not have seen this. Its gates (both halves, the five best days, plateaus, leave-one-spell-out) "
        "measure stability <i>inside</i> 2020–2023. A rule that fits that regime well is stable inside it "
        "by construction. Only data from outside the regime can say whether the rule is a fact about markets or a "
        "description of those four years."))

    # ---------------------------------------------------------------- part 8: conclusions and operating rules
    story += [PageBreak()] + opener("Part 8", "Conclusions and operating rules",
                                    "Do not allocate. If the desk wants a live answer, run a paper trial with "
                                    "kill rules fixed in advance.")
    story.append(h2("Conclusions"))
    story += numbered([
        f"The strategy is a fit to 2020–2023. In-sample Sharpe {B.num(m['sharpe_is'])}; validation "
        f"{snum(m['sharpe_oos'])}; sealed hold-out {snum(final['sharpe_pre'])} against {snum(final['bench_sharpe_pre'])} "
        "for the index.",
        f"More research made it worse out of sample. The hold-out score peaked at {best_pre['stage']} and all "
        f"{n_after_best} later incumbents score lower; the validation score of every incumbent after the baseline "
        "is below the baseline's.",
        "In-sample robustness gates are necessary and not sufficient. They removed fragile one-day and one-value "
        "results; they could not remove a regime fit.",
        "The research infrastructure is sound and reusable: harness, lookahead detector, hidden validation data, "
        "sealed and logged hold-out, lineage scoring.",
    ])
    story.append(h2("If the desk runs a paper trial"))
    story.append(P("Operating loop, using <font name='PlexMono'>signals.py</font> (Appendix D):"))
    story += numbered([
        "After the NSE close, update the data (<font name='PlexMono'>python fetch_fmp.py --update</font>) and keep "
        "the index-membership file current when NSE announces changes.",
        f"Run <font name='PlexMono'>python signals.py --capital 1000000 --holdings book.csv</font>. It prints the "
        f"target weights for the next open, the rupee amounts (for example {B.inr(1000000)} of capital) and the "
        "orders against current holdings. Pass the current book: the strategy is path-dependent.",
        "Record the orders at the next open as market-on-open fills. Never price them off the previous close.",
        "Weekly: reconcile the paper book against a backtest re-run on the same data. Any difference is a data or "
        "code problem before it is a market one.",
    ])
    story.append(KeepTogether([h2("Kill rules (fixed before the first trade)"), data_table(
        ["Rule", "Stop the trial when"],
        [["Spell hit rate", f"after 15 completed spells, fewer than half have beaten cash (backtest: "
          f"{B.num(s_is['hit_rate'] * 100, 0)}% in sample, {B.num(s_oos['hit_rate'] * 100, 0)}% since 2024)"],
         ["Sharpe", "after 12 months, the paper Sharpe over cash is below zero"],
         ["Drawdown", f"the paper book falls more than 10% from its peak (1.5× the backtest's "
          f"{B.num(m['maxdd_full'] * 100, 1)}% worst)"],
         ["Selection edge", "the bought names lag the average member over two consecutive quarters"],
         ["Reconciliation", "a weekly re-run on the same data does not reproduce the paper orders"],
         ["Costs", "measured round-trip cost exceeds twice the 15 bps one-way assumption"]],
        [32 * mm, FW - 32 * mm], keep=False)]))
    story.append(P("These thresholds are proposals for the desk's register; they are not results of this study.",
                   "source"))
    story.append(h2("What would change the verdict"))
    story.append(P(
        "A forward record. Twelve months of paper trading in which the spell hit rate and the selection edge "
        "return to their 2020–2023 levels would make the in-sample result worth a second look. Absent that, "
        "the next study should target the failure directly (why the selection edge decays after 2023) rather "
        "than add rules, and should pre-register a fresh hold-out before its first experiment."))

    # ---------------------------------------------------------------- appendices: code
    code_files = [
        ("Appendix A", "The strategy: strategy.py", "strategy.py",
         "The only file the research loop edited. compute_target_weights(md) returns the target weights for every "
         "close; the harness executes them at the next open. Complete and unedited: " + prov_txt + "."),
        ("Appendix B", "The indicators: indicators.py", "indicators.py",
         "Fixed harness file. Every indicator is causal: the value at a date uses data up to that date only."),
    ]
    for label, title, fname, intro in code_files:
        story += [PageBreak()] + opener(label, title, None)
        story.append(P(intro))
        src = open(os.path.join(HERE, fname)).read()
        story.append(CodeBlock.from_source(src, FW))
    story += [PageBreak()] + opener("Appendix C", "The execution engine: prepare.py (excerpt)", None)
    story.append(P("The functions of the fixed harness that turn target weights into returns and score them. The "
                   "complete file (data loading, calendar, universe, lookahead detector) is in the repository."))
    for fn in ["apply_harness_rules", "run_backtest", "sharpe", "max_drawdown", "cagr"]:
        src, start = inspect.getsourcelines(getattr(Pp, fn))
        story.append(h3(f"prepare.{fn}  (line {start})"))
        story.append(CodeBlock.from_source("".join(src), FW, first_line=start))
        story.append(Spacer(1, 6))
    story += [PageBreak()] + opener("Appendix D", "Live signals: signals.py", None)
    story.append(P("Computes the target portfolio for the next session's open from data through the latest close, "
                   "with the same harness rules as the backtest, and the orders against a current book."))
    story.append(CodeBlock.from_source(open(os.path.join(HERE, "signals.py")).read(), FW))

    # ---------------------------------------------------------------- appendix E: reproduce, glossary, notes
    story += [PageBreak()] + opener("Appendix E", "Reproduce, glossary and important information", None)
    story.append(h2("Reproduce every number in this note"))
    cmds = ("cd nse\n"
            "python -m pytest -q tests                      # harness, indicators, calendar, hold-out, signals, PDF\n"
            "python run.py --report                         # metrics + lookahead check for strategy.py\n"
            "python validate.py --boot 2000 --out reports   # costs, bootstrap, DSR, perturbations, walk-forward\n"
            "python lineage.py --holdout                    # every kept strategy on every window (logged)\n"
            "python diagnose.py                             # spells and selection edge\n"
            "python make_pdf.py                             # this document\n")
    story.append(CodeBlock.from_source(cmds, FW))
    story.append(Spacer(1, 8))
    story.append(data_table(["File", "Role"], [
        ["strategy.py", "the strategy (Appendix A)"], ["indicators.py", "indicator library (Appendix B)"],
        ["prepare.py", "fixed harness: data, universe, calendar, engine, metrics, lookahead detector"],
        ["run.py / compare.py", "one experiment / candidate against incumbent under the gates"],
        ["validate.py, lineage.py, holdout.py, diagnose.py", "the evidence in Parts 5–7"],
        ["signals.py", "next-open targets and orders (Appendix D)"],
        ["results.tsv", f"the research log: {n_exp:,} experiments, {len(keeps)} keeps"],
        ["make_pdf.py, raas_brand.py, brand/", "this document and the RAAS Capital identity it is set in"],
    ], [62 * mm, FW - 62 * mm], mono_cols=(0,)))
    story.append(h2("Glossary"))
    story.append(data_table(["Term", "Meaning"], [
        ["Cohort", "the basket bought on one formation day; it rides until sold name by name or the regime ends"],
        ["Fresh start / re-entry", "the regime switch has just turned on / the book returns after standing aside above RSI 80"],
        ["Spell", "a run of sessions in which the book holds stocks, from switch-on to switch-off"],
        ["Selection window", "2020–2023, the only data the research loop optimised on"],
        ["Validation window", "2024 to October 2026, hidden from researchers, seen by the lead after round 1"],
        ["Sealed hold-out", "April 2017 to December 2019, never evaluated during research, scored once"],
        ["Incumbent / keep", "the current best strategy / an experiment that replaced it"],
        ["Excess return", "return over the RBI repo rate earned by cash"],
        ["Model vol", "the book's annualised volatility under the single-index model of 4.5"],
        ["Stretched", "a held name whose EMA20 is more than 6% above its EMA50"],
    ], [38 * mm, FW - 38 * mm]))
    story.append(h2("Important information"))
    story.append(P(
        "This note is internal research by the RAAS Capital Quant Desk. All performance figures are hypothetical "
        "results of a historical simulation, net of modelled costs; they are not returns of any account and do not "
        "predict future results. Stock returns are price returns without dividends, which understates long-only "
        "returns by about 1–1.5% a year against a total-return benchmark. The cost model is an estimate, not "
        "a measurement. Nothing in this note is investment advice or an offer to buy or sell any security.",
        "body"))
    story.append(P(
        "Set in Outfit, IBM Plex Sans and IBM Plex Mono (SIL Open Font License 1.1). Market data: Financial Modeling "
        "Prep; Nifty 50 membership: Historical Nifty 50 Constituent Weights (NSE Indices data, CC BY-NC-SA 4.0); "
        "repo rate: Reserve Bank of India.", "source"))

    story += [NextPageTemplate("back"), PageBreak(), Spacer(1, 1)]

    doc = NoteDoc(out, meta)
    doc.multiBuild(story)
    return dict(out=out, pages=doc.page, trace=trace, meta=meta)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--date", default=None, help="document date YYYY-MM-DD (default: today)")
    a = ap.parse_args()
    d = dt.date.fromisoformat(a.date) if a.date else None
    info = build(a.out, d)
    print(f"wrote {info['out']} ({info['pages']} pages)")


if __name__ == "__main__":
    main()
