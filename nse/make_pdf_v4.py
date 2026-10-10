"""
make_pdf_v4.py - builds the RAAS Capital research note on the protocol-v4 strategy:

    reports/RCG-RN-REV-002_RSI-EMA-VWAP_Nifty50_v2.pdf

The question it answers: which changes make 2024-2026 positive as well and the strategy effective over the whole of
2020-2026. It documents why the v1 strategy failed, the protocol that replaced its single in-sample window (every
seen window at once, the worst window as the objective, a new sealed 2011-2016 hold-out), the five research rounds
and every amendment, the final strategy rule by rule (with a trade log rebuilt from the backtest and checked
against the rules at build time), its results, robustness and deep hold-out record, the operating rules, and the
full Python source. Every number is read from the repository (reports/v4/*.json, results_v4.tsv) or recomputed
from the data at build time.

    python validate4.py --strategy strategy.py --incumbent v4/r0_ema_timer.py && python lineage4.py
    python deep4.py --final strategy.py
    python make_pdf_v4.py                      # -> reports/RCG-RN-REV-002_RSI-EMA-VWAP_Nifty50_v2.pdf

Typeset in the RAAS Capital identity with the page furniture, styles and helpers of make_pdf.py.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_pdf as M  # noqa: E402  (page furniture, styles, helpers)
import raas_brand as B  # noqa: E402
from make_pdf import (P, esc, code, pnum, snum, h2, opener, figure, navy_card, formula_card,  # noqa: E402
                      data_table, stat_tiles, bullets, numbered, new_fig, to_drawing, date_axis, mono_ticks,
                      CodeBlock, KesariRule, NoteDoc, FW, LFW, mm, Spacer, PageBreak, NextPageTemplate,
                      KeepTogether, CondPageBreak, Paragraph, TableOfContents)

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

DOC_ID = "RCG-RN-REV-002"
DOC_VERSION = "1.0"
DOC_TITLE = "RSI + EMA + VWAP on the Nifty 50, v2"
DOC_SUBTITLE = "Changes that hold in every regime: the research, the strategy and its tests"
R4 = os.path.join(HERE, "reports", "v4")
DEFAULT_OUT = os.path.join(HERE, "reports", f"{DOC_ID}_RSI-EMA-VWAP_Nifty50_v2.pdf")
SOURCE = "Source: RAAS Capital Quant Desk backtest on Financial Modeling Prep daily bars."
WIN = {"pre": "2017–19", "is": "2020–23", "oos": "2024–26", "span": "2017–26", "full": "2020–26"}
WIN_LONG = {"pre": "Apr 2017 – Dec 2019", "is": "Jan 2020 – Dec 2023", "oos": "Jan 2024 – Oct 2026"}

# the parameter register: every PARAMS entry of the final strategy, explained (asserted against the code)
PARAM_DOC = [
    ("Trigger", [("trend_n", "EMA length of the uptrend tests: the NIFTY above its EMA, a member above its own EMA"),
                 ("breadth_cut", "trigger: share of uptrend members closing below their VWAP must exceed this"),
                 ("vwap_n", "rolling VWAP length: trigger breadth, depth ranking and the reclaim exit")]),
    ("Name filter", [("rsi_n", "Wilder RSI length of the oversold test"),
                     ("rsi_max", "a name is oversold when its RSI is below this")]),
    ("Book", [("slots", "positions; each held name weighs 1/slots of the book"),
              ("max_hold", "time stop: a name not bought back is sold after this many sessions")]),
]


def part(label, title, standfirst=None, level_text=None, min_space=125 * mm):
    """A part opener that starts on the current page when at least min_space is left (no half-empty pages);
    the Kesari rule keeps 18 pt of air above it unless it lands at the top of a page."""
    o = opener(label, title, standfirst, level_text=level_text)
    o[0].spaceBefore = 18
    return [CondPageBreak(min_space)] + o


def keep(*items):
    """KeepTogether over the items with any inner KeepTogether flattened: reportlab sizes a nested
    KeepTogether as 0xffffff points tall, which would push every outer block to a fresh page."""
    flat = []
    for it in items:
        flat += list(it._content) if isinstance(it, KeepTogether) else [it]
    return KeepTogether(flat)


def glue_headings(story):
    """Keep headings with what they introduce. A keep-with-next heading does not bind to a following
    KeepTogether (reportlab skips containers when it groups keepWithNext), so (1) each section heading is bound
    to the block after it, and (2) each opener (Kesari rule, label, title, standfirst) is bound to its first
    block, so a part never opens at the foot of a page with its content overleaf."""
    out = []
    for f in story:
        prev = out[-1] if out else None
        if (isinstance(f, KeepTogether) and isinstance(prev, M.Heading) and prev.getKeepWithNext()
                and prev.toc_level >= 1):
            out[-1] = keep(prev, f)
        else:
            out.append(f)
    glued, i = [], 0
    while i < len(out):
        f = out[i]
        if isinstance(f, KesariRule) and i + 2 < len(out) and isinstance(out[i + 2], M.Heading):
            k = i + 3
            if k < len(out) and isinstance(out[k], Paragraph) and out[k].style.name == "standfirst":
                k += 1
            nxt = out[k] if k < len(out) else None
            if nxt is not None and not isinstance(nxt, CodeBlock):
                glued.append(keep(*out[i:k + 1]))
                i = k + 1
                continue
        glued.append(f)
        i += 1
    return glued


def load_strategy(path):
    spec = importlib.util.spec_from_file_location("strategy_v4note", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def j(name, base=R4):
    p = os.path.join(base, name)
    if not os.path.exists(p):
        raise FileNotFoundError(f"missing {p}: run validate4.py, lineage4.py and deep4.py first")
    return json.load(open(p))


# ---------------------------------------------------------------------------------------------------------
# The strategy's rules, replicated independently of strategy.py, and the trade log rebuilt from the backtest
# ---------------------------------------------------------------------------------------------------------
def replicate(md, p):
    """The trigger and the name filter recomputed from the definitions in Part 4 (not by calling strategy.py)."""
    from indicators import ema, rsi, rolling_vwap
    tick = [t for t in md.tickers if t != M.P_ETF]
    c = md.close[tick]
    vw = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["vwap_n"])
    live = md.member[tick] & md.tradable[tick]
    up = c > ema(c, p["trend_n"])
    ix = md.index_close["NIFTY50"]
    ix_ema = ema(md.index_close[["NIFTY50"]], p["trend_n"])["NIFTY50"]
    base = live & up
    below = (c < vw) & base
    breadth = below.sum(axis=1) / base.sum(axis=1).replace(0, np.nan)
    trigger = (ix > ix_ema) & (breadth > p["breadth_cut"])
    r = rsi(c, p["rsi_n"])
    eligible = base & (r < p["rsi_max"])
    return dict(tick=tick, c=c, vw=vw, up=up, breadth=breadth, trigger=trigger, rsi=r, eligible=eligible,
                ix=ix, ix_ema=ix_ema, depth=c / vw - 1.0, n_base=base.sum(axis=1))


def trade_log(md, res, rep, p):
    """Every trade of the executed book: entry at the open after the decision close, exit at the open after the
    exit decision. Each entry must fall on a trigger day for an eligible name, and each exit must be explained by
    a rule (VWAP reclaim, time stop) or by the harness (the name left the index); anything else is collected in
    'unexplained' and stops the build."""
    W = res["weights"]
    tick = rep["tick"]
    cal = md.calendar
    pos = {d: i for i, d in enumerate(cal)}
    rows, unexplained = [], []
    o = md.open
    for t in tick:
        w = W[t].values
        on = w > 1e-12
        i = 1
        while i < len(cal):
            if on[i] and not on[i - 1]:
                d0 = cal[i]
                k = i
                while k + 1 < len(cal) and on[k + 1]:
                    k += 1
                d1 = cal[k + 1] if k + 1 < len(cal) else None
                if not bool(rep["trigger"].get(d0, False)) or not bool(rep["eligible"].at[d0, t]):
                    unexplained.append((str(d0.date()), t, "entry not on a trigger day for an eligible name"))
                if d1 is None:
                    reason = "open at the end of the data"
                    ret = float("nan")
                else:
                    age = pos[d1] - pos[d0]
                    if not bool(md.member.at[d1, t]):
                        reason = "left the index"
                    elif md.close.at[d1, t] > rep["vw"].at[d1, t]:
                        reason = "VWAP reclaim"
                    elif age >= p["max_hold"]:
                        reason = "time stop"
                    else:
                        reason = "?"
                        unexplained.append((str(d1.date()), t, "exit not explained"))
                    i_in, i_out = pos[d0] + 1, pos[d1] + 1
                    ret = (float(o.iloc[i_out][t] / o.iloc[i_in][t] - 1.0) if i_out < len(cal) else float("nan"))
                rows.append(dict(ticker=t, entry=d0, exit=d1, sessions=(pos[d1] - pos[d0]) if d1 is not None else None,
                                 reason=reason, ret=ret, depth=float(rep["depth"].at[d0, t]),
                                 rsi=float(rep["rsi"].at[d0, t])))
                i = k + 1
            i += 1
    tl = pd.DataFrame(rows).sort_values(["entry", "ticker"]).reset_index(drop=True)
    return tl, unexplained


def window_of(d):
    for lab, a, b in M.Pp.V4_WINDOWS:
        if pd.Timestamp(a) <= d <= pd.Timestamp(b):
            return lab
    return None


def window_bounds(lab):
    """(start, end) of a protocol-v4 window label; 'span' is the whole of 2017-04..2026-10."""
    if lab == "span":
        return M.Pp.V4_START, M.Pp.END
    return next((a, b) for k, a, b in M.Pp.V4_WINDOWS if k == lab)


# ---------------------------------------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------------------------------------
def shade_windows(ax, ymin=0, ymax=1):
    for lab, a, b in M.Pp.V4_WINDOWS:
        if lab == "is":
            ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color=B.KAGAZ[200], alpha=0.55, lw=0, zorder=0)


def chart_equity(eq, v1):
    fig, (ax, ax2) = new_fig(232, 2, [3.2, 1])
    shade_windows(ax)
    for col, colour, lw, lab in [("nifty50", B.SYAHI[40], 0.9, "NIFTY 50 index"), ("niftybees", B.NEELA[300], 1.0, "NIFTYBEES"),
                                 ("v1_final", B.NEELA[500], 0.9, "v1 strategy"), ("r0_ema_timer", B.NEELA[700], 1.0, "EMA timer (R0)"),
                                 ("strategy", B.NEELA[900], 1.6, "v2 strategy")]:
        s = eq[col] if col != "v1_final" else v1
        ax.plot(s.index, s.values, color=colour, lw=lw, label=lab, ls="--" if col == "v1_final" else "-")
    ax.set_yscale("log")
    ticks = [0.8, 1, 1.25, 1.5, 2, 2.5, 3, 4]
    ax.set_yticks(ticks); ax.set_yticklabels([f"{t:g}" for t in ticks]); ax.minorticks_off()
    ax.set_ylabel("growth of 1 (log)")
    ax.legend(loc="upper left", ncol=3, fontsize=7)
    for lab, a, _ in M.Pp.V4_WINDOWS:
        ax.text(pd.Timestamp(a) + pd.Timedelta(days=40), 0.84, WIN[lab], fontsize=7, color=B.SYAHI[60])
    ax2.fill_between(eq.index, eq["exposure"].values, 0, color=B.NEELA[500], lw=0, step="post")
    ax2.set_ylim(0, 1.02); ax2.set_ylabel("exposure")
    date_axis(ax2)
    mono_ticks(ax)
    fig.tight_layout(h_pad=0.4)
    return to_drawing(fig)


def chart_windows(rows):
    """Grouped bars: window Sharpe of each strategy."""
    fig, ax = new_fig(150)
    labs = ["pre", "is", "oos"]
    x = np.arange(len(labs))
    n = len(rows)
    wdt = 0.8 / n
    colours = [B.SYAHI[40], B.NEELA[500], B.NEELA[300], B.NEELA[900]]
    top = 2.0                                              # bars above this are clipped and labelled
    for k, (name, vals) in enumerate(rows):
        ys = [vals[l] for l in labs]
        bars = ax.bar(x + (k - (n - 1) / 2) * wdt, [min(y, top) for y in ys], wdt * 0.92, color=colours[k % len(colours)],
                      label=name, zorder=2)
        for b_, y in zip(bars, ys, strict=True):
            yy = min(y, top)
            ax.text(b_.get_x() + b_.get_width() / 2, yy + (0.08 if y >= 0 else -0.08), B.num(y, 2) + (" ↑" if y > top else ""),
                    ha="center", va="bottom" if y >= 0 else "top", fontsize=6.4, color=B.SYAHI[80], fontfamily="IBM Plex Mono")
    ax.axhline(0, color=B.SYAHI[60], lw=0.6)
    ax.set_xticks(x); ax.set_xticklabels([f"{WIN[l]}\n{WIN_LONG[l]}" for l in labs])
    ax.set_ylabel("Sharpe over the repo rate")
    lo = min(min(v[l] for l in labs) for _, v in rows)
    ax.set_ylim(min(-1.4, lo - 0.3), top + 0.45)
    ax.legend(loc="upper center", ncol=n, fontsize=7, bbox_to_anchor=(0.5, 1.13))
    mono_ticks(ax)
    fig.tight_layout()
    return to_drawing(fig)


def chart_research(tsv, steps):
    """Logged objectives by round (strip) and the incumbent after each round (step line)."""
    fig, ax = new_fig(160)
    rounds = ["v4-1", "v4-2", "v4-3", "v4-4", "v4-5"]
    rng = np.random.default_rng(0)
    for k, r_ in enumerate(rounds):
        y = pd.to_numeric(tsv.loc[tsv["round"] == r_, "objective"], errors="coerce").dropna().clip(-1.5, 1.5)
        x = k + 1 + rng.uniform(-0.28, 0.28, len(y))
        ax.scatter(x, y, s=3, color=B.NEELA[300], alpha=0.45, lw=0, zorder=2)
    xs = [0] + [k + 1 for k in range(len(rounds))]
    ax.step(xs, steps, where="post", color=B.NEELA[900], lw=1.6, zorder=3, label="incumbent after the round")
    ax.axhline(0, color=B.SYAHI[60], lw=0.6)
    ax.set_xticks(range(0, len(rounds) + 1))
    ax.set_xticklabels(["start"] + [f"round {k + 1}" for k in range(len(rounds))])
    ax.set_ylabel("worst-window Sharpe")
    ax.set_ylim(-1.55, 1.55)
    ax.legend(loc="lower right", fontsize=7)
    mono_ticks(ax)
    fig.tight_layout()
    return to_drawing(fig)


def chart_trigger(rep, year, tl):
    a, b = f"{year}-01-01", f"{year}-12-31"
    fig, (ax, ax2) = new_fig(200, 2, [1.6, 1])
    ix = rep["ix"].loc[a:b]; ie = rep["ix_ema"].loc[a:b]
    ax.plot(ix.index, ix.values, color=B.NEELA[900], lw=1.1, label="NIFTY 50")
    ax.plot(ie.index, ie.values, color=B.NEELA[300], lw=1.0, label="its EMA(100)")
    trig = rep["trigger"].loc[a:b]
    for d in trig.index[trig.values]:
        ax.axvline(d, color=B.NEELA[100], lw=1.6, zorder=0)
        ax2.axvline(d, color=B.NEELA[100], lw=1.6, zorder=0)
    ax.legend(loc="upper left", fontsize=7)
    M.indian_ticks(ax.yaxis)
    br = rep["breadth"].loc[a:b]
    ax2.plot(br.index, br.values, color=B.NEELA[700], lw=0.9)
    ax2.axhline(float(PARAMS_FOR_CHART["breadth_cut"]), color=B.SYAHI[60], lw=0.8, ls="--")
    ax2.set_ylim(0, 1); ax2.set_ylabel("share below VWAP")
    date_axis(ax2, "%b", mdates.MonthLocator(bymonth=[1, 3, 5, 7, 9, 11]))
    mono_ticks(ax)
    fig.tight_layout(h_pad=0.4)
    return to_drawing(fig)


def chart_trade(md, rep, t, d0, d1):
    cal = md.calendar
    i0 = max(0, cal.get_loc(d0) - 30); i1 = min(len(cal) - 1, cal.get_loc(d1) + 15)
    span = cal[i0:i1 + 1]
    fig, (ax, ax2) = new_fig(200, 2, [1.8, 1])
    c = md.close.loc[span, t]; vw = rep["vw"].loc[span, t]
    from indicators import ema
    e = ema(md.close[[t]], PARAMS_FOR_CHART["trend_n"])[t].loc[span]
    ax.plot(span, c.values, color=B.NEELA[900], lw=1.2, label=f"{t} close")
    ax.plot(span, vw.values, color=B.NEELA[400], lw=1.0, label=f"VWAP({PARAMS_FOR_CHART['vwap_n']})")
    ax.plot(span, e.values, color=B.SYAHI[40], lw=0.9, ls="--", label=f"EMA({PARAMS_FOR_CHART['trend_n']})")
    ax.axvline(d0, color=B.NEELA[100], lw=6, zorder=0)
    ax.axvline(d1, color=B.KAGAZ[300], lw=6, zorder=0)
    ax.annotate("buy at next open", (d0, c.loc[d0]), xytext=(-62, -24), textcoords="offset points", fontsize=7,
                color=B.SYAHI[80], arrowprops=dict(arrowstyle="-", color=B.SYAHI[60], lw=0.6))
    ax.annotate("sell at next open", (d1, c.loc[d1]), xytext=(8, 18), textcoords="offset points", fontsize=7,
                color=B.SYAHI[80], arrowprops=dict(arrowstyle="-", color=B.SYAHI[60], lw=0.6))
    ax.legend(loc="lower left", fontsize=7, ncol=3)
    r = rep["rsi"].loc[span, t]
    ax2.plot(span, r.values, color=B.NEELA[700], lw=0.9)
    ax2.axhline(PARAMS_FOR_CHART["rsi_max"], color=B.SYAHI[60], lw=0.8, ls="--")
    ax2.set_ylim(0, 100); ax2.set_ylabel(f"RSI({PARAMS_FOR_CHART['rsi_n']})")
    date_axis(ax2, "%d %b", mdates.WeekdayLocator(byweekday=0, interval=2))
    mono_ticks(ax)
    fig.tight_layout(h_pad=0.4)
    return to_drawing(fig)


def chart_yearly(yearly):
    fig, ax = new_fig(150)
    yrs = [r["year"] for r in yearly]
    x = np.arange(len(yrs)); wdt = 0.27
    s = [r["strat_ret"] for r in yearly]; e = [r["bees_ret"] for r in yearly]; c = [r["rf"] for r in yearly]
    ax.bar(x - wdt, s, wdt, color=[B.GAIN if v >= 0 else B.LOSS for v in s], label="v2 strategy", zorder=2)
    ax.bar(x, e, wdt, color=B.NEELA[300], label="NIFTYBEES", zorder=2)
    ax.bar(x + wdt, c, wdt, color=B.SYAHI[20], label="cash (repo)", zorder=2)
    for xi, v in zip(x, s, strict=True):
        ax.text(xi - wdt, v + (0.006 if v >= 0 else -0.006), f"{v * 100:.0f}", ha="center", va="bottom" if v >= 0 else "top",
                fontsize=6.4, color=B.SYAHI[80], fontfamily="IBM Plex Mono")
    ax.axhline(0, color=B.SYAHI[60], lw=0.6)
    ax.set_xticks(x); ax.set_xticklabels([f"{y}*" if y in (2017, 2026) else str(y) for y in yrs])
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0, decimals=0))
    ax.legend(loc="upper right", fontsize=7, ncol=3)
    mono_ticks(ax)
    fig.tight_layout()
    return to_drawing(fig)


def chart_costs(cs):
    fig, ax = new_fig(140)
    bps = [r["cost_bps"] for r in cs]
    for key, colour, lab in [("sharpe_pre", B.NEELA[300], WIN["pre"]), ("sharpe_is", B.NEELA[500], WIN["is"]),
                             ("sharpe_oos", B.NEELA[700], WIN["oos"]), ("objective", B.NEELA[900], "worst window")]:
        ax.plot(bps, [r[key] for r in cs], marker="o", ms=3, color=colour, lw=1.6 if key == "objective" else 1.0, label=lab)
    ax.axvline(15, color=B.SYAHI[40], lw=0.8, ls="--")
    ax.axhline(0, color=B.SYAHI[60], lw=0.6)
    ax.set_xlabel("one-way cost, bps"); ax.set_ylabel("Sharpe")
    ax.legend(loc="lower left", fontsize=7, ncol=4)
    mono_ticks(ax)
    fig.tight_layout()
    return to_drawing(fig)


def chart_joint(jp, point, r0):
    fig, ax = new_fig(130)
    ax.hist(jp["values"], bins=20, color=B.NEELA[300], edgecolor="white", lw=0.4, zorder=2)
    ax.axvline(point, color=B.NEELA[900], lw=1.6, label=f"the strategy {B.num(point)}")
    ax.axvline(jp["mean"], color=B.NEELA[600], lw=1.0, ls="--", label=f"mean of the draws {B.num(jp['mean'])}")
    ax.axvline(r0, color=B.SYAHI[60], lw=1.0, ls=":", label=f"EMA timer R0 {B.num(r0)}")
    ax.set_xlabel("worst-window Sharpe with every parameter moved at random by up to ±20 %")
    ax.set_ylabel("draws")
    ax.legend(loc="upper left", fontsize=7)
    mono_ticks(ax)
    fig.tight_layout()
    return to_drawing(fig)


def chart_deep(deep_ret):
    fig, ax = new_fig(170)
    for col, colour, lw, lab in [("cash", B.SYAHI[20], 1.0, "cash (repo)"), ("nifty", B.SYAHI[40], 0.9, "NIFTY 50 index"),
                                 ("r0", B.NEELA[600], 1.0, "EMA timer (R0)"), ("v1", B.NEELA[400], 0.9, "v1 strategy"),
                                 ("final", B.NEELA[900], 1.6, "v2 strategy")]:
        if col in deep_ret:
            s = (1 + deep_ret[col].fillna(0.0)).cumprod()
            ax.plot(s.index, s.values, color=colour, lw=lw, label=lab, ls="--" if col == "v1" else "-")
    ax.set_ylabel("growth of 1")
    ax.legend(loc="upper left", fontsize=7, ncol=3)
    date_axis(ax)
    fig.tight_layout()
    return to_drawing(fig)


PARAMS_FOR_CHART = {}


# ---------------------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------------------
def build(out=DEFAULT_OUT, date=None, strategy_path=None, reports=R4):
    global PARAMS_FOR_CHART
    B.check_assets()
    B.register_reportlab_fonts()
    B.register_matplotlib_fonts()
    from reportlab import rl_config
    from reportlab.graphics import shapes as rl_shapes
    rl_config.canvas_basefontname = "Plex"
    rl_shapes.STATE_DEFAULTS["fontName"] = "Plex"
    from reportlab.platypus import tables as rl_tables
    rl_tables.CellStyle.fontname = "Plex"
    plt.rcParams.update(B.mpl_rc())
    M.ST = M.styles()
    M.FIG["n"] = 0
    for k, v in dict(DOC_ID=DOC_ID, DOC_VERSION=DOC_VERSION, DOC_TITLE=DOC_TITLE, DOC_SUBTITLE=DOC_SUBTITLE).items():
        setattr(M, k, v)
    import prepare as Pp
    M.Pp = Pp
    M.P_ETF = Pp.ETF_TICKER

    strategy_path = strategy_path or os.path.join(HERE, "strategy.py")
    S = load_strategy(strategy_path)
    p = dict(S.PARAMS)
    PARAMS_FOR_CHART = p
    documented = {n for _, items in PARAM_DOC for n, _ in items}
    numeric = {k for k, v in p.items() if not k.startswith("use_")}
    assert documented == numeric, f"parameter register out of date: {documented ^ numeric}"

    v = j("validation.json", reports)
    lin = {r["file"]: r for r in j("lineage.json", reports)}
    deep = j("deep_holdout.json", reports)
    drows = {r["file"]: r for r in deep["rows"]}
    tsv = pd.read_csv(os.path.join(HERE, "results_v4.tsv"), sep="\t")
    eq = pd.read_csv(os.path.join(reports, "equity_curves.csv"), index_col=0, parse_dates=True)
    m = v["metrics"]; bm = v["benchmarks"]; cs = v["cost_sensitivity"]; yearly = v["yearly"]; bs = v["bootstrap"]
    wf = v["walk_forward"]; mc = v["monte_carlo"]; rg = v["regime"]
    pl = v["plateau"]; jp = v["joint_perturbation"]; evd = v["event_days"]; la = v["lookahead"]
    v1 = lin["v4/v1_final.py"]; r0 = lin["v4/r0_ema_timer.py"]; inc1 = lin["v4/inc1_pullback.py"]
    final_file = os.path.relpath(strategy_path, HERE)
    fin = lin.get(final_file) or lin["strategy.py"]
    assert abs(fin["objective"] - m["objective"]) < 1e-9, "lineage and validation disagree on the final strategy"
    dfin = next(r for r in deep["rows"] if "final" in r["roles"])
    fin_is_new = abs(m["objective"] - inc1["objective"]) > 1e-9      # a round-5 keep replaced incumbent 1
    if not la["ok"]:
        raise RuntimeError("validation.json: the look-ahead detector failed for this strategy")
    d_r0, d_v1 = drows["v4/r0_ema_timer.py"], drows["v4/v1_final.py"]
    d_info = [r for r in deep["rows"] if r["roles"] == ["information"]]

    # data, weights, trade log
    md = Pp.load_data(presample=True, end=Pp.END)
    W = S.compute_target_weights(md)
    res = Pp.run_backtest(W, md)
    ev = Pp.evaluate_v4(W, md); ev.pop("_res")
    assert abs(ev["objective"] - m["objective"]) < 1e-9, "validation.json does not describe this strategy file"
    rep = replicate(md, p)
    tl, unexplained = trade_log(md, res, rep, p)
    if unexplained:
        raise RuntimeError(f"trade log: entries or exits not explained by the rules: {unexplained[:5]}")
    tl["window"] = [window_of(d) for d in tl["entry"]]
    tl = tl[tl["window"].notna()].reset_index(drop=True)
    if "v1_final" in eq:                                      # validate4.py writes it; older reports lack it
        v1_eq = eq["v1_final"]
    else:
        v1_mod = load_strategy(os.path.join(HERE, "v4", "v1_final.py"))
        v1_eq = (1 + Pp.run_backtest(v1_mod.compute_target_weights(md), md)["ret"].loc[eq.index]).cumprod()

    date = date or dt.date.today()
    data_end = str(md.calendar[-1].date())
    import subprocess
    try:
        commit = subprocess.run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                check=True).stdout.strip()
    except Exception:
        commit = "working tree"
    meta = dict(date_long=date.strftime("%d %B %Y").lstrip("0"), commit=commit, data_end=data_end,
                cover_title=["RSI + EMA + VWAP", "on the Nifty 50, v2"],
                cover_sub=["Changes that hold in every regime:", "the research, the strategy and its tests"],
                cover_figs=[("2024–26 (was −1.06)", m["sharpe_oos"]), ("2020–26", m["sharpe_full"]),
                            ("Worst window 2017–26", m["objective"]), ("Deep hold-out 2011–16", dfin["sharpe_deep"])],
                creator="make_pdf_v4.py (RAAS Capital brand)")

    # numbers used in the text
    n_rows = len(tsv)
    by_round = tsv.groupby("round").size().to_dict()
    n_trials = bs["span"]["deflated"]["n_trials"]
    dsr = bs["span"]["deflated"]
    yrs_pos = m["years_positive"]
    tw = {lab: tl[tl.window == lab] for lab, _, _ in Pp.V4_WINDOWS}
    reasons = tl.reason.value_counts().to_dict()
    cost_tab = {r["cost_bps"]: r for r in cs}
    bees = bm["niftybees"]; nifty = bm["nifty50"]
    ev5 = evd["strategy"]["5"]
    r0ev5 = evd["r0_ema_timer"]["5"]
    conc = evd["concentration"]
    n_lenses = tsv[tsv["round"].str.match(r"v4-[1-9]")].groupby("round")["lens"].nunique().to_dict()
    n_audits = sum(n_lenses.values())                      # one independent audit per lens and round
    v1_hist = json.load(open(os.path.join(HERE, "reports", "lineage.json")))      # the v1 loop's own record
    v1_diag = json.load(open(os.path.join(HERE, "reports", "diagnostics.json")))["summary"]
    r0_mod = load_strategy(os.path.join(HERE, "v4", "r0_ema_timer.py"))
    r0_40 = Pp.evaluate_v4(r0_mod.compute_target_weights(md), md, cost_bps=40)["objective"]
    # the worked example: the trigger day in 2024-26 with the most new names
    oos_tl = tl[tl.window == "oos"]
    ex_day = oos_tl.groupby("entry").size().sort_values(ascending=False, kind="stable").index[0]
    ex = oos_tl[oos_tl.entry == ex_day].sort_values("depth").reset_index(drop=True)
    ex_name = ex.iloc[0]
    deep_ret = {}
    for f, key in [(final_file if os.path.exists(os.path.join(reports, "deep", os.path.basename(final_file)[:-3] + ".csv"))
                    else dfin["file"], "final"), ("v4/r0_ema_timer.py", "r0"), ("v4/v1_final.py", "v1")]:
        pth = os.path.join(reports, "deep", os.path.basename(f)[:-3] + ".csv")
        if os.path.exists(pth):
            d = pd.read_csv(pth, index_col=0, parse_dates=True)
            deep_ret[key] = d["strategy"]
            deep_ret.setdefault("nifty", d["nifty"]); deep_ret.setdefault("cash", d["cash"])
    deep_ret = pd.DataFrame(deep_ret)
    dc = deep_ret["cash"].loc[Pp.DEEP_START:Pp.DEEP_END]
    cash_deep = float((1 + dc).prod() ** (252.0 / len(dc)) - 1)    # the repo rate earned by idle capital

    story = [NextPageTemplate("body"), PageBreak()]
    ST = M.ST

    # ------------------------------------------------------------ contents and document control
    story += [KesariRule(), P("Contents", "part"), Paragraph("Document control and contents", ST["h1"])]
    ctl = [
        ("Document", f"{DOC_ID}, version {DOC_VERSION}"), ("Title", f"{DOC_TITLE}: {DOC_SUBTITLE.lower()}"),
        ("Date", meta["date_long"]), ("Desk", f"RAAS Capital · {M.DESK}"),
        ("Status", "Final research record of protocol v4. Paper trial before any allocation (Part 8)"),
        ("Classification", "Internal. Hypothetical backtest results; not investment advice"),
        ("Supersedes", "Nothing: RCG-RN-REV-001 remains the record of the v1 strategy; this note records v2"),
        ("Strategy code", f"{code(final_file)} at commit {code(commit)}; branch {code('autoresearch/oct9-nse')}"),
        ("Data", f"NSE daily bars 2016-06-01 to {data_end} (research) and 2007-09-17 to 2016-05-31 (deep hold-out), "
                 "Financial Modeling Prep; point-in-time Nifty 50"),
        ("Build", f"{code('python make_pdf_v4.py')} (every figure is read from the repository at build time)"),
    ]
    story.append(data_table(["Field", "Value"], [[esc(a), b_ if "<font" in b_ else esc(b_)] for a, b_ in ctl],
                            [32 * mm, FW - 32 * mm], zebra=False))
    story.append(Spacer(1, 14))
    toc = TableOfContents()
    toc.levelStyles = [ST["toc0"], ST["toc1"]]
    toc.dotsMinLevel = -1
    story.append(toc)

    # ------------------------------------------------------------ executive summary
    story += [PageBreak()] + opener("Summary", "Executive summary",
                                    "The question: which changes make 2024–2026 positive as well, and the strategy "
                                    "effective over the whole of 2020–2026. The answer: stop timing concentrated "
                                    "cohorts, and buy broad pullbacks inside uptrends instead.", level_text="Executive summary")
    story.append(stat_tiles([
        ("2024–26 Sharpe", WIN_LONG["oos"], m["sharpe_oos"], f"v1: {B.num(v1['sharpe_oos'])}"),
        ("2020–26 Sharpe", "Jan 2020 – Oct 2026", m["sharpe_full"], f"NIFTY 50: {B.num(nifty['full'])}"),
        ("Worst window", "2017–19, 2020–23, 2024–26", m["objective"], f"v1: {B.num(v1['objective'])}"),
        ("Deep hold-out", "Apr 2011 – May 2016, never seen", dfin["sharpe_deep"], f"NIFTY 50: {B.num(dfin['bench_sharpe_deep'])}"),
    ]))
    story.append(Spacer(1, 6))
    story.append(P("Sharpe ratios of daily returns over the RBI repo rate, net of 15 bps one-way costs; trades at the next "
                   "open after each decision close.", "note"))
    story.append(h2("What changed"))
    story.append(P(
        f"The v1 strategy (RCG-RN-REV-001) timed concentrated baskets of Nifty 50 stocks on the index's RSI. It was "
        f"selected on 2020–2023 alone, earned a Sharpe of {pnum(v1['sharpe_is'])} there, and lost money everywhere "
        f"else: {pnum(v1['sharpe_pre'])} on 2017–2019 and {pnum(v1['sharpe_oos'])} on 2024–2026. The v2 strategy keeps "
        "the brief - RSI, EMA and VWAP - but uses them for a different trade:"))
    story += bullets([
        "<b>When</b> (EMA and VWAP): only on days when the NIFTY 50 is above its 100-day EMA <i>and</i> most of the "
        f"members in their own uptrend have closed below their {p['vwap_n']}-day VWAP: a broad pullback inside a "
        "long uptrend.",
        f"<b>What</b> (EMA and RSI): members in their own uptrend that were sold hard, with RSI({p['rsi_n']}) below "
        f"{p['rsi_max']:g}; the deepest below their VWAP first, up to {p['slots']} names of 1/{p['slots']} each.",
        f"<b>Exit</b> (VWAP): as soon as a name closes back above its {p['vwap_n']}-day VWAP, or after "
        f"{p['max_hold']} sessions. Capital not in a trade earns the repo rate.",
    ])
    story.append(P(
        f"Over every window the research has seen it is positive: {pnum(m['sharpe_pre'])} on 2017–2019, "
        f"{pnum(m['sharpe_is'])} on 2020–2023 and {pnum(m['sharpe_oos'])} on 2024–2026, when the Nifty itself "
        f"returned less than cash ({pnum(nifty['oos'])}). Over 2020–2026 the Sharpe is {pnum(m['sharpe_full'])} "
        f"(Nifty {pnum(nifty['full'])}); {yrs_pos} of 10 calendar years have a positive excess return; the worst "
        f"drawdown over 2017–2026 is {pnum(m['maxdd_span'] * 100, 1)} %."))
    story.append(navy_card("Read the evidence before the numbers", [
        "2024–2026 was used to choose these rules, so it is no longer out of sample: the v2 research optimised the "
        "worst of the three windows at once. The test that no strategy here had ever seen is the deep hold-out, "
        f"April 2011 to May 2016, built for this purpose and scored once at the end: {pnum(dfin['sharpe_deep'])} for "
        f"the v2 strategy against {pnum(dfin['bench_sharpe_deep'])} for the NIFTY 50 (Part 7).",
        f"The strategy is in the market {pnum(m['exposure_span'] * 100, 0)} % of the capital on average and earns much "
        f"of its return on a few market-wide rebound days ({pnum(conc['top10_share_of_span_excess'] * 100, 0)} % of the "
        f"2017–2026 excess return in its 10 best days). Costs matter: the worst-window Sharpe falls to "
        f"{pnum(cost_tab[25]['objective'])} at 25 bps and {pnum(cost_tab[40]['objective'])} at 40 bps one-way.",
    ], label="Caveats"))
    story.append(keep(h2("At a glance"), data_table(
        ["Sharpe ratio", "2017–19", "2020–23", "2024–26", "2020–26", "Deep 2011–16"], [
            [esc("v1 strategy (RCG-RN-REV-001)"), *[snum(v1[f"sharpe_{k}"]) for k in ("pre", "is", "oos", "full")],
             snum(d_v1["sharpe_deep"])],
            [esc("EMA timer R0 (the v4 bar)"), *[snum(r0[f"sharpe_{k}"]) for k in ("pre", "is", "oos", "full")],
             snum(d_r0["sharpe_deep"])],
            [esc("NIFTY 50 index"), *[snum(nifty[k]) for k in ("pre", "is", "oos", "full")], snum(dfin["bench_sharpe_deep"])],
            [esc("v2 strategy (this note)"), *[snum(m[f"sharpe_{k}"]) for k in ("pre", "is", "oos", "full")],
             snum(dfin["sharpe_deep"])],
        ], [52 * mm] + [(FW - 52 * mm) / 5] * 5, num_cols=(1, 2, 3, 4, 5))))
    story.append(P("Price index, no dividends. The deep hold-out column is the only one no rule here was chosen on.",
                   "note_source"))
    story.append(h2("How to read this note"))
    story.append(data_table(["Part", "What it answers"], [
        [esc("1  Why v1 failed"), esc("what went wrong with the strategy of RCG-RN-REV-001, and why its tests missed it")],
        [esc("2  Protocol v4"), esc("the objective, the keep gates and the sealed 2011–2016 test that replaced one window")],
        [esc("3  Research"), esc("five rounds, every lens, every amendment and merge, with the numbers")],
        [esc("4  The strategy"), esc("every rule and parameter, how often it trades, one trigger day traced to the exits")],
        [esc("5–6  Evidence"), esc("results by window and year; costs, bootstrap, deflated Sharpe, plateaus, event days")],
        [esc("7  Deep hold-out"), esc("the one look at 2011–2016, for the final and every registered strategy")],
        [esc("8  Operating"), esc("conclusions, the paper-trial rules and the kill rules")],
        [esc("A–C  Appendices"), esc("the complete strategy code, the evaluation code, how to reproduce every number")],
    ], [36 * mm, FW - 36 * mm], zebra=False))

    # ------------------------------------------------------------ part 1: why v1 failed
    story += part("Part 1", "Why the v1 strategy failed",
                                    "A 40-parameter fit to one window; every test outside it said so.")
    story.append(P(
        f"The v1 loop (RCG-RN-REV-001) ran eight rounds of parallel researchers on one objective, the 2020–2023 "
        f"Sharpe. It lifted that number from {pnum(v1_hist[0]['sharpe_is'])} to {pnum(v1_hist[-1]['sharpe_is'])} "
        f"through {len(v1_hist) - 1} merged changes ({pnum(v1['sharpe_is'])} on the protocol-v4 data set). The "
        f"rules it found - a timed cohort of names bought on the day the index's RSI turned up, weighted by beta over "
        f"idiosyncratic variance, trimmed by a volatility budget - described the post-Covid up-legs of 2020–2023 "
        f"very well and nothing else: {pnum(v1['sharpe_pre'])} on 2017–2019 and {pnum(v1['sharpe_oos'])} on 2024–2026."))
    story.append(data_table(["Strategy", "2017–19", "2020–23", "2024–26", "2017–26", "2020–26"], [
        [esc("v1 baseline (3659aab)"), *[snum(lin["v4/v1_baseline.py"][f"sharpe_{k}"]) for k in ("pre", "is", "oos", "span", "full")]],
        [esc("v1 final (c44574d)"), *[snum(v1[f"sharpe_{k}"]) for k in ("pre", "is", "oos", "span", "full")]],
        [esc("NIFTYBEES, buy and hold"), *[snum(bees[k]) for k in ("pre", "is", "oos", "span", "full")]],
        [esc("NIFTY 50 index"), *[snum(nifty[k]) for k in ("pre", "is", "oos", "span", "full")]],
    ], [50 * mm] + [(FW - 50 * mm) / 5] * 5, num_cols=(1, 2, 3, 4, 5)))
    story.append(P(
        f"The v1 diagnosis found two causes. Its invested spells beat cash {v1_diag['2020-23']['hit_rate'] * 100:.0f} % "
        f"of the time in 2020–2023 and {v1_diag['2024-26']['hit_rate'] * 100:.0f} % in "
        "2024–2026, and the names it bought stopped beating the average member after 2023: the selection edge was a "
        "property of one regime, not of the rules. The in-sample robustness gates of v1 (both halves, best days "
        "removed, plateaus, one spell left out) could not see this, because they all tested stability inside the "
        "same four years."))
    story.append(P(
        "The lesson for v2: a window that the research is allowed to choose on cannot also be its test. Asking for "
        "2024–2026 to be positive means choosing on 2024–2026, so the protocol had to change, and a new test had "
        "to be built."))

    # ------------------------------------------------------------ part 2: protocol v4
    story += part("Part 2", "Protocol v4: every regime at once",
                                    "Three seen windows in the objective, its worst one counting; a new sealed test "
                                    "that nothing had been run on.")
    story.append(h2("The objective"))
    story.append(formula_card([
        "objective = min( Sharpe<sub>2017–19</sub>, Sharpe<sub>2020–23</sub>, Sharpe<sub>2024–26</sub> )",
    ], note="Each Sharpe is of daily returns in excess of the RBI repo rate, net of 15 bps one-way costs, on the "
            "point-in-time Nifty 50 of 2016–2026. A rule that buys one regime at the cost of another does not raise it."))
    story.append(P(
        "Maximising the worst of three windows has its own failure mode, and the round-1 audits found it: rules that "
        "move Sharpe out of the strong window into the weak ones raise the minimum without adding edge. The keep "
        "gates therefore compare every window, not just the minimum, with the incumbent."))
    story.append(h2("Keep gates"))
    story.append(data_table(["Gate", "A candidate is kept only if"], [
        ["improves", "its objective is above the incumbent's"],
        ["windows", "no window Sharpe is more than 0.10 below the incumbent's (no trading one regime for another)"],
        ["drop10", "the 2017–26 Sharpe without its 10 best days is above the incumbent's"],
        ["years", "with any single calendar year removed, its objective is still above the incumbent's"],
        ["consistency", "it has at least as many calendar years with a positive excess return"],
        ["parsimony", "each added parameter buys at least 0.05 of objective"],
        ["plateau", "every parameter's ±20 % neighbours also beat the incumbent and keep 60 % of the margin"],
        ["compliance", "RSI, EMA and VWAP each move at least 5 % of the target weights when switched off"],
        ["sanity", "invested ≥ 30 % of days, diversified, turnover ≤ 40x a year, drawdown above −30 %, no lookahead"],
    ], [26 * mm, FW - 26 * mm], mono_cols=(0,)))
    story.append(P(
        "Every candidate was then audited by an independent agent that re-ran it, re-checked every gate, moved all "
        "parameters at random by up to ±20 % together (60 draws), removed each window's best days, tested 25 and "
        "40 bps costs, ablated every rule and read the code for look-ahead, dead rules and gate dressing.", "body"))
    story.append(h2("A new sealed test: the deep hold-out"))
    dr = deep["data"]
    story.append(P(
        f"Both earlier tests were spent: 2024–2026 by the question itself, 2017–2019 by the v1 lineage. So the research "
        f"data were extended backwards to a window no strategy in this repository had ever been run on: "
        f"<b>April 2011 to May 2016</b>, {dr['sessions_scored']:,} sessions, on the point-in-time Nifty 50 of "
        f"2008–2016 with {dr['members_with_bars_min']} to {dr['members_with_bars_max']} members tradable on any day. It "
        "was built before round 1, kept outside the repository until the end, and scored once on a set of strategies "
        "registered before any look; its score could not change which strategy is final."))
    story += bullets([
        "Corrections, all documented in prepare.py: the 2010 Samruddhi Cement demerger of Grasim; Bajaj Auto's rows "
        "before its 2008 demerger; the vendor's 2013–2015 gap in HDFC (the name leaves the universe rather than book a "
        "fake 50 % jump); one exchange holiday with flat vendor bars; the Diwali Muhurat sessions.",
        f"Index gaps: {dr['NIFTY50_rebuilt_sessions']} NIFTY 50 and {dr['BANKNIFTY_rebuilt_sessions']} BANK NIFTY sessions "
        f"rebuilt from constituents (BANK NIFTY rebuild tracking error {dr['banknifty_proxy_rms_bps']} bps a day, "
        f"correlation {dr['banknifty_proxy_corr']}).",
        "Known limit: the vendor has no bars for three members later absorbed by mergers (Cairn India, Ranbaxy, "
        "Sterlite Industries).",
    ])

    # ------------------------------------------------------------ part 3: the research
    story += part("Part 3", "Five rounds of research",
                                    f"{n_rows:,} logged variants, {n_audits} independent audits, two amendments "
                                    "made in the open" + (", two merges." if fin_is_new else ", one merge."))
    steps = [v1["objective"], r0["objective"], r0["objective"], inc1["objective"], inc1["objective"], m["objective"]]
    story.append(figure(chart_research(tsv, steps),
                        "Figure 1. Every logged variant's worst-window Sharpe by round (dots, clipped to ±1.5) and the "
                        "incumbent after each round (line).", source=None))
    round_rows = [
        ["1", f"{n_lenses.get('v4-1', 0)}", f"{by_round.get('v4-1', 0):,}", "index timing, trend basket, relative strength, pullback, repair of v1, exposure overlay",
         "every lens beat v1 (0.25–0.69); the one effect present in every window is the NIFTY's EMA20 > EMA50 trend"],
        ["2", f"{n_lenses.get('v4-2', 0)}", f"{by_round.get('v4-2', 0):,}", "RSI/VWAP confirmation, timer + pullback sleeve, stock sleeve, breadth, whipsaw exits",
         "the pullback sleeve beats the EMA timer in every window (0.75); all rejected in audit, two rule flaws found"],
        ["3", f"{n_lenses.get('v4-3', 0)}", f"{by_round.get('v4-3', 0):,}", "clean the sleeve, combine parts, robust trend core",
         "merge: the pullback strategy (0.79) - the timer core lowered the objective and was deleted"],
        ["4", f"{n_lenses.get('v4-4', 0)}", f"{by_round.get('v4-4', 0):,}", "event-day robustness, capital efficiency, entries and exits",
         "dry: each lever raised the objective but failed the year-by-year or plateau gates"],
        ["5", f"{n_lenses.get('v4-5', 0)}", f"{by_round.get('v4-5', 0):,}", "simplify, reconcile the round-4 levers, a more robust trigger", ROUND5_TEXT],
    ]
    story.append(data_table(["Round", "Lenses", "Rows", "What was tried", "Outcome"], round_rows,
                            [13 * mm, 13 * mm, 14 * mm, 58 * mm, FW - 98 * mm], num_cols=(1, 2), compact=True))
    higher = sorted([r for f, r in lin.items() if f.startswith(("v4/r4_", "v4/r5_")) and r["objective"] > m["objective"]],
                    key=lambda r: -r["objective"])
    if higher:
        story.append(P(
            "<b>Why the final is not the highest number in the log.</b> Rounds 4 and 5 produced "
            + ", ".join(f"{code(os.path.basename(r['file']))} ({pnum(r['objective'])})" for r in higher)
            + ". Each raised the worst-window Sharpe and each failed a gate against the incumbent: the gain vanished "
            "when one calendar year was left out, a neighbouring parameter value fell below the incumbent, or the book "
            "was invested on fewer than 30 % of days. The audits traced the gains to a few episodes (the September 2018 "
            "IL&FS sell-off, October 2023 and October 2024). Those gates exist to stop exactly this kind of gain."))
    story.append(h2("The amendments, and why they were needed"))
    story += numbered([
        "<b>v4.1, after round 1.</b> The gates first compared candidates with the v1 strategy, whose 2020–2023 Sharpe "
        "of 4.97 was the very overfit v4 corrects: no strategy that repaired the other windows could pass. The gates "
        "were re-anchored on the plain EMA timer R0 (NIFTYBEES while the NIFTY's EMA20 is above its EMA50; "
        f"objective {pnum(r0['objective'])}), named in the round-1 brief as the bar to beat, and brief compliance "
        "became a measured gate.",
        "<b>v4.2, after round 2.</b> The \"at least five names\" sanity rule counted NIFTYBEES, which holds all fifty, "
        "as one name, so candidates added token baskets to pass it; and R0's 20/50 is itself a narrow peak, so any "
        "candidate built on it failed an absolute plateau. Diversification now counts NIFTYBEES as diversified, and "
        "parameters inherited unchanged from the incumbent are tested relative to the incumbent at the same setting.",
        "<b>The merge, after round 3.</b> Three lenses converged on R0 at 20 % plus an 80 % pullback sleeve "
        f"(0.745). The audits found that deleting the R0 core raised the objective to {pnum(inc1['objective'])} "
        "with every gate still passing, so the simplification rule made the deletion the keep.",
    ])
    story.append(P("Every amendment was written into program.md before the next round and before any look at the "
                   "deep hold-out.", "note"))

    # ------------------------------------------------------------ part 4: the strategy
    story += part("Part 4", "The v2 strategy, rule by rule",
                                    "Buy the leaders' broad pullbacks inside a long uptrend; sell when the dip has "
                                    "been bought back.")
    story.append(P(
        "The whole book is a dip-buying sleeve. Decisions use data up to the close of day t; the harness trades at the "
        "open of t+1 at 15 bps one-way. Capital not in a trade stays in cash at the repo rate."))
    story.append(data_table(["Step", "Rule", "Indicator"], [
        ["1  Trigger", f"the NIFTY 50 closes above its EMA({p['trend_n']}), and of the members in their own uptrend "
                       f"(close above their EMA({p['trend_n']})) more than {p['breadth_cut'] * 100:.0f} % close below their "
                       f"{p['vwap_n']}-day VWAP", "EMA, VWAP"],
        ["2  Name filter", f"on a trigger day, a member in its own uptrend with Wilder RSI({p['rsi_n']}) below {p['rsi_max']:g}",
         "EMA, RSI"],
        ["3  Selection", f"free slots ({p['slots']}, each 1/{p['slots']} of the book) go to the eligible names deepest below "
                         f"their VWAP({p['vwap_n']})", "VWAP"],
        ["4  Exit", f"sell when the name closes back above its VWAP({p['vwap_n']}), or after {p['max_hold']} sessions; "
                    "names leaving the index are sold by the harness", "VWAP"],
    ], [26 * mm, FW - 26 * mm - 22 * mm, 22 * mm], mono_cols=(0,)))
    story.append(formula_card([
        f"breadth<sub>t</sub> = #{{ i : C<sub>i,t</sub> &lt; VWAP<sub>{p['vwap_n']}</sub>(i,t), C<sub>i,t</sub> &gt; "
        f"EMA<sub>{p['trend_n']}</sub>(i,t) }} / #{{ i : C<sub>i,t</sub> &gt; EMA<sub>{p['trend_n']}</sub>(i,t) }}",
        f"trigger<sub>t</sub> = [ NIFTY<sub>t</sub> &gt; EMA<sub>{p['trend_n']}</sub>(NIFTY)<sub>t</sub> ] and "
        f"[ breadth<sub>t</sub> &gt; {p['breadth_cut']:g} ]",
        f"depth<sub>i,t</sub> = C<sub>i,t</sub> / VWAP<sub>{p['vwap_n']}</sub>(i,t) − 1   (buy the most negative first)",
    ], note="VWAP<sub>n</sub>: rolling volume-weighted typical price (H+L+C)/3 over n sessions; members only; "
            "C: close. The RSI is Wilder's with smoothing 1/n."))
    story.append(h2("Why it should hold across regimes"))
    story += bullets([
        "It is paid for supplying liquidity, not for predicting direction: after market-wide selling, impatient "
        "sellers push names that are in their own uptrend below the price their recent buyers paid (the VWAP), and "
        "the bounce back to it pays whoever bought from them.",
        "Names are chosen by how far they fell, not by momentum or sector, so a change of market leadership - what "
        "broke v1 after 2023 - does not remove the trade.",
        "Breadth is counted over uptrend names only, so it fires when the leaders themselves pull back, not in "
        f"narrow tapes; the index filter keeps it out of crashes in progress; the {p['max_hold']}-session stop cuts "
        "dips that are not bought back.",
    ])
    prow = []
    for blk, items in PARAM_DOC:
        for k_, (nm, desc) in enumerate(items):
            prow.append([esc(blk) if k_ == 0 else "", code(nm), code(M.fmt_param(p[nm])), esc(desc)])
    story.append(keep(
        h2("Parameter register"),
        data_table(["Block", "Parameter", "Value", "Meaning"], prow, [22 * mm, 24 * mm, 16 * mm, FW - 62 * mm]),
        P(f"All {len(numeric)} numeric entries of PARAMS, checked against the code at build time; the "
          "use_rsi / use_ema / use_vwap switches exist only for the compliance test.", "source")))
    story.append(h2("How often it trades"))
    trow = []
    for lab in ("pre", "is", "oos", "span"):
        g = tw[lab] if lab != "span" else tl
        a_, b_ = window_bounds(lab)
        ndays = int(rep["trigger"].loc[a_:b_].sum())
        done = g[g.ret.notna()]
        trow.append([esc(WIN[lab]), f"{ndays}", f"{len(g)}", B.num(float((done.ret > 0).mean()) * 100, 0) + " %",
                     snum(float(done.ret.mean()) * 100, 2), B.num(float(g.sessions.dropna().mean()), 1),
                     B.num(float((g.reason == "VWAP reclaim").mean()) * 100, 0) + " %"])
    story.append(data_table(["Window", "Trigger days", "Trades", "Won", "Mean trade %", "Mean sessions", "VWAP exits"],
                            trow, [24 * mm] + [(FW - 24 * mm) / 6] * 6, num_cols=(1, 2, 3, 4, 5, 6), mono_cols=(0,)))
    story.append(P(f"Trades rebuilt from the executed book at build time: each entry is checked to fall on a trigger day "
                   f"for an eligible name and each exit to have a rule behind it ({', '.join(f'{k} {n}' for k, n in reasons.items())}). "
                   "Trade returns are open to open, before costs.", "note_source"))

    # worked example
    story.append(CondPageBreak(80 * mm))
    story.append(h2(f"A trigger day traced: {ex_day:%d %B %Y}"))
    story.append(P(
        f"On {ex_day:%d %B %Y} the NIFTY closed above its EMA({p['trend_n']}) and "
        f"{rep['breadth'].loc[ex_day] * 100:.0f} % of the {int(rep['n_base'].loc[ex_day])} members in their own uptrend "
        f"closed below their VWAP({p['vwap_n']}). {len(ex)} names qualified for free slots and were bought at the next "
        "open; the table follows each one to its exit."))
    erows = []
    for _, r_ in ex.iterrows():
        erows.append([code(r_.ticker), snum(r_.depth * 100, 1), B.num(r_.rsi, 1),
                      code(f"{r_.exit:%d %b}") if r_.exit is not None else "–", str(r_.sessions), esc(r_.reason),
                      snum(r_.ret * 100, 1)])
    story.append(data_table(["Name", "Depth %", f"RSI({p['rsi_n']})", "Exit", "Sessions", "Reason", "Return %"], erows,
                            [24 * mm, 20 * mm, 18 * mm, 18 * mm, 18 * mm, FW - 116 * mm, 18 * mm], num_cols=(1, 2, 4, 6)))
    story.append(figure(chart_trade(md, rep, ex_name.ticker, ex_name.entry, ex_name.exit),
                        f"Figure 2. The deepest name of the day, {ex_name.ticker}: close, VWAP({p['vwap_n']}) and "
                        f"EMA({p['trend_n']}) (top), RSI({p['rsi_n']}) (bottom). Shaded: the decision closes of the "
                        "entry and the exit."))
    story.append(figure(chart_trigger(rep, ex_day.year, tl),
                        f"Figure 3. {ex_day.year}: the NIFTY and its EMA({p['trend_n']}) (top); the share of uptrend "
                        f"members closing below their VWAP({p['vwap_n']}) with the {p['breadth_cut']:g} cut (bottom). "
                        "Shaded: trigger days."))

    # ------------------------------------------------------------ part 5: results
    story += part("Part 5", "Results", "Positive in every window the research has seen, with a "
                                    "fraction of the market's risk.")
    story.append(figure(chart_equity(eq, v1_eq), "Figure 4. Growth of 1 over April 2017 – October 2026, net of costs, "
                        "against the v1 strategy, the EMA timer R0, NIFTYBEES and the NIFTY 50 price index (top); the "
                        "v2 strategy's gross exposure (bottom). Shaded: 2020–2023, the window v1 was fitted to and the only one in "
                        "which it beat cash, which is why its line ends highest."))
    story.append(figure(chart_windows([("v1 strategy", {k: v1[f"sharpe_{k}"] for k in ("pre", "is", "oos")}),
                                       ("NIFTYBEES", bees), ("EMA timer R0", {k: r0[f"sharpe_{k}"] for k in ("pre", "is", "oos")}),
                                       ("v2 strategy", {k: m[f"sharpe_{k}"] for k in ("pre", "is", "oos")})]),
                        "Figure 5. Sharpe ratio in each window."))
    hdr = ["Window", "Sharpe", "CAGR", "Max DD", "Vol", "Exposure", "NIFTY Sharpe"]
    rrows = []
    for k in ("pre", "is", "oos", "span", "full"):
        rrows.append([esc(WIN[k]), snum(m[f"sharpe_{k}"]), snum(m[f"cagr_{k}"], 1, pct=True),
                      snum(m[f"maxdd_{k}"], 1, pct=True), B.num(m[f"vol_{k}"], 1, pct=True),
                      B.num(m[f"exposure_{k}"], 0, pct=True), snum(nifty[k])])
    story.append(data_table(hdr, rrows, [26 * mm] + [(FW - 26 * mm) / 6] * 6, num_cols=(1, 2, 3, 4, 5, 6), mono_cols=(0,)))
    story.append(P("CAGR includes the repo rate earned on idle capital. Price returns without dividends, so the "
                   "long-only figures are understated by about 1–1.5 % a year against a total-return index. "
                   f"Look-ahead detector: {la['n_checks']} truncation and corruption checks ({la['n_checks_with_holdings']} "
                   f"with positions open), largest weight difference {la['max_abs_diff']:g}.", "note_source"))
    story.append(figure(chart_yearly(yearly), "Figure 6. Calendar-year returns: the v2 strategy (green up, red down), "
                        "NIFTYBEES and cash. * 2017 from April, 2026 to October."))
    yrows = [[esc(str(r["year"])), snum(r["strat_ret"], 1, pct=True), snum(r["strat_sharpe"]), snum(r["strat_maxdd"], 1, pct=True),
              B.num(r["exposure"], 0, pct=True), snum(r["bees_ret"], 1, pct=True), snum(r["nifty_sharpe"]), B.num(r["rf"], 1, pct=True)]
             for r in yearly]
    story.append(data_table(["Year", "Return", "Sharpe", "Max DD", "Exposure", "NIFTYBEES", "NIFTY Sharpe", "Cash"], yrows,
                            [18 * mm] + [(FW - 18 * mm) / 7] * 7, num_cols=tuple(range(1, 8)), mono_cols=(0,)))

    # ------------------------------------------------------------ part 6: robustness
    story += part("Part 6", "Robustness", "What survives costs, sampling error, parameter moves, "
                                    "walk-forward re-selection and the removal of its best days.")
    story.append(h2("Costs"))
    story.append(figure(chart_costs(cs), "Figure 7. Window Sharpe ratios against the one-way cost; dashed: the 15 bps "
                        "used throughout.", source=None))
    story.append(P(
        f"Turnover is {B.num(m['turnover_yr_span'], 1)}x a year. Each extra 10 bps one-way costs about "
        f"{pnum(cost_tab[15]['objective'] - cost_tab[25]['objective'], 2)} of worst-window Sharpe; the strategy stays "
        f"positive in every window up to 40 bps ({pnum(cost_tab[40]['objective'])} in the worst), where the EMA timer "
        f"R0 is at {pnum(r0_40)}. Executing at the open after a close signal is what the backtest assumes; slippage beyond the "
        "modelled 4 bps would come straight off these numbers."))
    story.append(h2("Sampling error and multiple testing"))
    story.append(data_table(["Window", "Sharpe", "t-stat", "95 % bootstrap interval", "P(Sharpe > 0)"], [
        [esc(WIN[k]), snum(bs[k]["sharpe"]), B.num(bs[k]["tstat"], 2),
         f"{B.num(bs[k]['ci95'][0])} to {B.num(bs[k]['ci95'][1])}", B.num(bs[k]["p_sharpe_gt_0"] * 100, 0) + " %"]
        for k in ("pre", "is", "oos", "span", "full")], [26 * mm, 22 * mm, 20 * mm, 60 * mm, FW - 128 * mm],
        num_cols=(1, 2, 4), mono_cols=(0,)))
    story.append(P(
        f"Stationary block bootstrap, mean block 20 sessions. Each window alone is short: 2017–2019 and 2024–2026 have "
        f"t-statistics of {B.num(bs['pre']['tstat'], 2)} and {B.num(bs['oos']['tstat'], 2)}. Over 2017–2026 the deflated "
        f"Sharpe ratio, which charges for all {n_trials:,} protocol-v4 trials (a threshold Sharpe of {pnum(dsr['sr0_ann'])} "
        f"for the best of that many tries), is {pnum(dsr['dsr'])}: the probability that the true Sharpe exceeds what "
        "the search alone would produce."))
    story.append(h2("Parameters: plateau, joint moves, walk-forward"))
    import plateau4
    floor = pl["incumbent_objective"] + plateau4.KEEP * (pl["objective"] - pl["incumbent_objective"])
    plrows = [[code(r["param"]), code(M.fmt_param(r["value"])), snum(r["objective"]), esc(r["rule"]),
               B.num(floor if r["rule"] == "absolute" else r["reference"]), esc("yes" if r["ok"] else "no")]
              for r in pl["rows"]]
    story.append(data_table(["Parameter", "Neighbour", "Objective", "Rule", "Must reach", "Passes"], plrows,
                            [30 * mm, 24 * mm, 24 * mm, 24 * mm, 24 * mm, FW - 126 * mm], num_cols=(2, 4), compact=True))
    inc_name = "the EMA timer R0" if pl["incumbent"].endswith("r0_ema_timer.py") else code(pl["incumbent"])
    story.append(P(f"Each parameter moved to its ±20 % neighbours with everything else fixed. Absolute rule (a new or "
                   f"moved parameter): beat {inc_name} ({B.num(pl['incumbent_objective'])}) and keep "
                   f"{plateau4.KEEP * 100:.0f} % of the margin, i.e. reach {B.num(floor)}. Relative rule (a parameter "
                   "inherited unchanged): beat the incumbent run at the same neighbour.", "note_source"))
    story.append(figure(chart_joint(jp, m["objective"], min(r0["sharpe_pre"], r0["sharpe_is"], r0["sharpe_oos"])),
                        f"Figure 8. Worst-window Sharpe in {jp['draws']} draws that move every parameter at once by a "
                        "random factor between 0.8 and 1.2.", source=None))
    story.append(P(
        f"Moving all parameters together costs about {pnum(m['objective'] - jp['mean'])} of objective on average "
        f"(mean {pnum(jp['mean'])}, 10th percentile {pnum(jp['p10'])}); {jp['share_above_r0'] * 100:.0f} % of the draws "
        f"still beat the EMA timer. Read the point estimate as {pnum(jp['mean'])} rather than {pnum(m['objective'])}. "
        f"Walk-forward re-selection - each year from 2019 using the perturbation-grid point with the best Sharpe on "
        f"the years before - gives {pnum(wf['sharpe_concat'])} over 2019–2026 and {pnum(wf['sharpe_concat_2024_26'])} on "
        "2024–2026."))
    story.append(h2("Event days"))
    story.append(data_table(["Best days removed", "2017–19", "2020–23", "2024–26", "Worst", "R0 worst"], [
        [esc(f"{k} per window"), snum(evd["strategy"][k]["pre"]), snum(evd["strategy"][k]["is"]),
         snum(evd["strategy"][k]["oos"]), snum(evd["strategy"][k]["worst"]), snum(evd["r0_ema_timer"][k]["worst"])]
        for k in ("0", "5", "10")], [36 * mm] + [(FW - 36 * mm) / 5] * 5, num_cols=(1, 2, 3, 4, 5)))
    story.append(P(
        f"A dip buyer is paid on rebound days, so it leans on a few of them: the 10 best days carry "
        f"{conc['top10_share_of_span_excess'] * 100:.0f} % of the 2017–2026 excess return, and without each window's "
        f"own 5 best days 2017–2019 falls to {pnum(ev5['pre'])} (R0 {pnum(r0ev5['pre'])}). The worst window still "
        f"beats R0's with those days removed ({pnum(ev5['worst'])} against {pnum(r0ev5['worst'])}). This is the "
        "strategy's main structural weakness."))
    story.append(h2("Monte Carlo and regimes"))
    story.append(P(
        f"Block-bootstrapped 2017–2026 paths: terminal wealth {B.num(mc['terminal_wealth_p5'], 2)} / "
        f"{B.num(mc['terminal_wealth_p50'], 2)} / {B.num(mc['terminal_wealth_p95'], 2)} (5th / 50th / 95th percentile), "
        f"probability of a loss {pnum(mc['p_loss'] * 100, 1)} %, median maximum drawdown {pnum(mc['maxdd_p50'] * 100, 1)} %. "
        f"With the NIFTY above its 200-day EMA the strategy's Sharpe is {pnum(rg['sharpe_above'])} "
        f"({rg['days_above']:,} days; NIFTY {pnum(rg['nifty_sharpe_above'])}); below it {pnum(rg['sharpe_below'])} "
        f"({rg['days_below']:,} days; NIFTY {pnum(rg['nifty_sharpe_below'])})."))

    # ------------------------------------------------------------ part 7: deep hold-out
    story += part("Part 7", "The deep hold-out, 2011–2016",
                                    "One look, at the end, on a set registered before it.")
    story.append(P(
        f"The window: {Pp.DEEP_START} to {Pp.DEEP_END}, a falling market in 2011, the taper tantrum of 2013, the 2014 "
        "election rally and the 2015–2016 decline. The registered set: the v1 baseline and final, the EMA timer R0, "
        "the six round-1 candidates and the round-3 variant with the timer core (for information), every v4 incumbent "
        f"and the final strategy. Each was scored once; reports/deep_holdout_log.tsv holds every look."))
    if len(deep_ret):
        story.append(figure(chart_deep(deep_ret.loc[Pp.DEEP_START:Pp.DEEP_END]),
                            "Figure 9. Growth of 1 on the deep hold-out, net of costs."))
    drow = []
    order = ([dfin] + [drows[f] for f in ("v4/r0_ema_timer.py", "v4/v1_final.py", "v4/v1_baseline.py")]
             + [r for r in d_info if r["file"] not in ("v4/r0_ema_timer.py", "v4/v1_final.py", "v4/v1_baseline.py")])
    for r in order:
        drow.append([code(r["file"]), esc("/".join(r["roles"])), snum(r["sharpe_deep"]), snum(r["cagr_deep"], 1, pct=True),
                     snum(r["maxdd_deep"], 1, pct=True), B.num(r["exposure_deep"], 0, pct=True)])
    story.append(data_table(["Strategy", "Role", "Sharpe", "CAGR", "Max DD", "Exposure"], drow,
                            [52 * mm, 34 * mm, 20 * mm, 20 * mm, 22 * mm, FW - 148 * mm], num_cols=(2, 3, 4, 5), keep=False))
    story.append(P(f"NIFTY 50 over the same window: Sharpe {pnum(dfin['bench_sharpe_deep'])}, CAGR "
                   f"{pnum(dfin['bench_cagr_deep'] * 100, 1)} %. Strategy CAGRs include the repo rate earned on idle "
                   f"capital ({pnum(cash_deep * 100, 1)} % a year over the window).", "note_source"))
    story.append(P(DEEP_VERDICT(dfin, d_r0, d_v1, d_info, m)))
    yrs_deep = [y for y in (2011, 2012, 2013, 2014, 2015, 2016) if dfin.get(f"sharpe_{y}") not in (None, "None")]
    story.append(data_table(["Year"] + [str(y) for y in yrs_deep],
                            [[esc("v2 strategy")] + [snum(float(dfin[f"sharpe_{y}"])) for y in yrs_deep],
                             [esc("EMA timer R0")] + [snum(float(d_r0[f"sharpe_{y}"])) for y in yrs_deep]],
                            [34 * mm] + [(FW - 34 * mm) / len(yrs_deep)] * len(yrs_deep), num_cols=tuple(range(1, len(yrs_deep) + 1))))

    # ------------------------------------------------------------ part 8: conclusions
    story += part("Part 8", "Conclusions and operating rules", "What changed, what it buys, and "
                                    "how to run it without fooling ourselves.")
    story.append(h2("What changed from v1 to v2"))
    story.append(data_table(["", "v1 (RCG-RN-REV-001)", "v2 (this note)"], [
        [esc("Selection"), esc("one window, 2020–2023"), esc("the worst of 2017–19, 2020–23, 2024–26")],
        [esc("Trade"), esc("timed cohorts on the index RSI, beta/idio-variance weights"), esc("broad pullbacks in uptrending names, bought back to VWAP")],
        [esc("Parameters"), esc("40"), esc(str(len(numeric)))],
        [esc("RSI"), esc("index regime switch, cohort filters"), esc(f"RSI({p['rsi_n']}) < {p['rsi_max']:g}: the name was sold hard")],
        [esc("EMA"), esc("cohort trend filters"), esc(f"EMA({p['trend_n']}): index and name in a long uptrend")],
        [esc("VWAP"), esc("exits, ladder, ranking"), esc(f"VWAP({p['vwap_n']}): breadth trigger, depth ranking, exit")],
        [esc("2024–26 Sharpe"), snum(v1["sharpe_oos"]), snum(m["sharpe_oos"])],
        [esc("2020–26 Sharpe"), snum(v1["sharpe_full"]), snum(m["sharpe_full"])],
        [esc("Deep hold-out Sharpe"), snum(d_v1["sharpe_deep"]), snum(dfin["sharpe_deep"])],
    ], [34 * mm, (FW - 34 * mm) / 2, (FW - 34 * mm) / 2]))
    story.append(h2("Conclusions"))
    story += numbered(CONCLUSIONS(m, v1, r0, dfin, d_r0, jp, cost_tab, evd, conc, dsr, n_trials))
    story.append(h2("Operating rules for a paper trial"))
    story += numbered([
        f"Every session after the close: update the data ({code('python fetch_fmp.py --update')}), run "
        f"{code('python signals.py --capital ...')} and place the orders at the next open.",
        "Track the paper book's daily excess return against the repo rate, the trigger days and every trade's exit "
        "reason; compare each with the backtest's own trade statistics (Part 4).",
        "Review after 12 months or 40 trades, whichever comes later: the trial is passed if the trade win rate and "
        "mean trade return sit inside the backtest's per-window ranges and the Sharpe is positive.",
    ])
    kill_dd = float(np.ceil(1.5 * abs(min(m["maxdd_span"], dfin["maxdd_deep"])) * 100) / 100)
    story.append(keep(h2("Kill rules (fixed before the first trade)"), data_table(["Trigger", "Action"], [
        [esc(f"drawdown deeper than {kill_dd * 100:.0f} %"), esc("stop; 1.5x the worst drawdown of 2011–2026")],
        [esc("12 months of negative excess return"), esc("stop and re-examine")],
        [esc("trade win rate below 50 % over 30+ trades"), esc("stop: the reversal edge is not there")],
        [esc("one-way costs above 25 bps measured"), esc("re-run the cost table; stop if the worst window is below 0.3")],
    ], [70 * mm, FW - 70 * mm])))

    # ------------------------------------------------------------ appendices
    def appendix(label, title, toc, lede, first=False):
        head = [] if first else [CondPageBreak(95 * mm), Spacer(1, 20)]
        return head + opener(label, title, None, level_text=toc) + [P(lede, "body_land")]
    story += [NextPageTemplate("code"), PageBreak()]
    story += appendix("Appendix A", "The v2 strategy", "Appendix A  ·  The v2 strategy",
                      f"{code(final_file)}: complete and unedited. {code('compute_target_weights(md)')} returns the "
                      "target weights for every close; the harness executes them at the next open.", first=True)
    story.append(CodeBlock.from_source(open(strategy_path).read(), LFW))
    story += appendix("Appendix B", "The protocol-v4 evaluation", "Appendix B  ·  The protocol-v4 evaluation",
                      f"The objective and the windows ({code('prepare.py')}), the keep gates ({code('compare4.py')}) and "
                      f"the plateau gate ({code('plateau4.py')}).")
    import inspect
    story.append(CodeBlock.from_source(inspect.getsource(Pp.v4_objective) + "\n\n" + inspect.getsource(Pp.evaluate_v4), LFW))
    import compare4, plateau4
    story.append(Spacer(1, 8))
    story.append(CodeBlock.from_source(inspect.getsource(compare4.gates) + "\n\n" + inspect.getsource(compare4.compliance), LFW))
    story.append(Spacer(1, 8))
    story.append(CodeBlock.from_source(inspect.getsource(plateau4.neighbours) + "\n\n" + inspect.getsource(plateau4.plateau), LFW))
    story += [NextPageTemplate("body")]
    story += [PageBreak()] + opener("Appendix C", "Reproduce, glossary, notices", None,
                                    level_text="Appendix C  ·  Reproduce, glossary, notices")
    story.append(h2("Reproduce every number in this note"))
    cmds = ("cd nse\n"
            "python -m pytest -q tests                # harness, protocol v4, deep loader, PDFs\n"
            "python run4.py --strategy strategy.py    # the three windows + look-ahead check\n"
            "python validate4.py --strategy strategy.py   # costs, bootstrap, DSR, plateau, ...\n"
            "python lineage4.py                       # every v4 strategy on every window\n"
            "python deep_holdout.py --data-report     # deep data checks (runs no strategy)\n"
            "python deep4.py --final strategy.py      # the one deep look (refuses a second)\n"
            "python make_pdf_v4.py                    # this document\n")
    story.append(CodeBlock.from_source(cmds, FW))
    story.append(Spacer(1, 8))
    story.append(data_table(["File", "Role"], [
        [code("strategy.py"), esc("the v2 strategy (Appendix A); the v1 strategy is v4/v1_final.py")],
        [code("prepare.py"), esc("fixed harness: data, universe, engine, metrics, lookahead detector, v4 objective")],
        [code("run4.py / compare4.py / plateau4.py"), esc("one experiment / the keep gates / the plateau gate")],
        [code("validate4.py / lineage4.py"), esc("the evidence in Parts 5 and 6")],
        [code("build_deep_holdout.py / deep_holdout.py / deep4.py"), esc("the deep hold-out: data, scorer, one-time look")],
        [code("results_v4.tsv"), esc(f"the protocol-v4 log: {n_rows:,} rows")],
        [code("v4/"), esc("R0, every round's candidates, the incumbents, frozen v1 files")],
        [code("program.md"), esc("the protocol, every amendment and every merge decision, in order")],
    ], [62 * mm, FW - 62 * mm], compact=True))
    story.append(h2("Glossary"))
    story.append(data_table(["Term", "Meaning"], [
        ["Window", "2017–19 (Apr 2017 – Dec 2019), 2020–23, 2024–26 (Jan 2024 – Oct 2026)"],
        ["Objective", "the lowest of the three window Sharpe ratios"],
        ["Incumbent", "the strategy every candidate is gated against"],
        ["R0", "the plain EMA timer: NIFTYBEES while the NIFTY's EMA20 > EMA50"],
        ["Trigger day", "NIFTY above its EMA100 and a broad pullback of uptrend members below their VWAP"],
        ["Depth", "close / VWAP − 1 on the trigger day"],
        ["Deep hold-out", "April 2011 – May 2016, scored once at the end"],
        ["Excess return", "return over the RBI repo rate earned by cash"],
    ], [30 * mm, FW - 30 * mm], mono_cols=(), compact=True))
    story.append(h2("Important information"))
    story.append(P(
        "This note is internal research by the RAAS Capital Quant Desk. All performance figures are hypothetical results "
        "of historical simulations, net of modelled costs; they are not returns of any account and do not predict future "
        "results. Stock returns are price returns without dividends. The cost model is an estimate, not a measurement. "
        "Nothing in this note is investment advice or an offer to buy or sell any security.", "small"))
    story.append(P(
        "Set in Outfit, IBM Plex Sans and IBM Plex Mono (SIL Open Font License 1.1). Market data: Financial Modeling Prep; "
        "Nifty 50 membership: Historical Nifty 50 Constituent Weights (Bera, 2025, CC BY-NC-SA 4.0; sourced from NSE "
        "Indices); repo rate: Reserve Bank of India.", "source"))
    story += [NextPageTemplate("back"), PageBreak(), Spacer(1, 1)]

    doc = NoteDoc(out, meta)
    doc.multiBuild(glue_headings(story))
    return dict(out=out, pages=doc.page, meta=meta, trades=tl, example_day=ex_day, unexplained=unexplained)


# text blocks that depend on the results (kept as functions so the build fails loudly if a number is missing)
ROUND5_TEXT = ("dry: tying the time stop to the VWAP length, a market-conditional early exit (0.93) and about thirty "
               "trigger definitions each failed the year-by-year or plateau gates; the stopping rule ended the research")


def DEEP_VERDICT(dfin, d_r0, d_v1, d_info, m):
    s, b = dfin["sharpe_deep"], dfin["bench_sharpe_deep"]
    if s > 0 and s > b:
        head = (f"<b>The v2 strategy held up on data it had never seen:</b> a Sharpe of {pnum(s)} against "
                f"{pnum(b)} for the NIFTY 50 and {pnum(d_r0['sharpe_deep'])} for the EMA timer R0.")
    elif s > 0:
        head = (f"<b>The v2 strategy stayed positive on data it had never seen</b> ({pnum(s)}), but did not beat the "
                f"NIFTY 50 there ({pnum(b)}).")
    else:
        head = (f"<b>The v2 strategy did not hold up on data it had never seen:</b> a Sharpe of {pnum(s)} against "
                f"{pnum(b)} for the NIFTY 50.")
    pos = [r for r in d_info if r["sharpe_deep"] > 0]
    neg = [r for r in d_info if r["sharpe_deep"] <= 0]
    fam = ""
    if pos and all("pullback" in r["file"] or "r3_clean" in r["file"] for r in pos):
        listed = ", ".join(f"{code(os.path.basename(r['file']))} {pnum(r['sharpe_deep'])}" for r in pos)
        fam = (f" The registered strategies split by family: the only positive ones are the earlier pullback "
               f"versions ({listed}), while the other {len(neg)}, R0 and both v1 versions included, are all negative. "
               "The deep window confirms the structural choice, not only the parameters.")
    rest = (f" On the research windows the worst-window Sharpe was {pnum(m['objective'])}; the deep figure comes from "
            f"a different market and is the only one free of selection. The v1 strategy scores "
            f"{pnum(d_v1['sharpe_deep'])} on the same window.")
    return head + fam + rest


def CONCLUSIONS(m, v1, r0, dfin, d_r0, jp, cost_tab, evd, conc, dsr, n_trials):
    return [
        f"<b>The change that matters is the trade, not a filter.</b> No index-level RSI or VWAP rule made the v1 "
        f"architecture or the EMA timer positive in every window without trading one regime for another; buying broad "
        f"pullbacks in uptrending names did: {pnum(m['sharpe_pre'])} / {pnum(m['sharpe_is'])} / {pnum(m['sharpe_oos'])} "
        f"against v1's {pnum(v1['sharpe_pre'])} / {pnum(v1['sharpe_is'])} / {pnum(v1['sharpe_oos'])}. On 2011–2016, "
        f"which no rule was chosen on, the pullback family is the only one with a positive Sharpe.",
        f"<b>2024–2026 is positive ({pnum(m['sharpe_oos'])}) and 2020–2026 is effective ({pnum(m['sharpe_full'])})</b>, "
        "but both were chosen on; the evidence that is not is the deep hold-out "
        f"({pnum(dfin['sharpe_deep'])}) and the walk-forward and perturbation tests (Part 6).",
        f"<b>Expect less than the point estimate.</b> Moving every parameter by up to ±20 % gives a mean worst-window "
        f"Sharpe of {pnum(jp['mean'])}; the deflated Sharpe over {n_trials:,} trials is {pnum(dsr['dsr'])}; costs of "
        f"25 bps leave {pnum(cost_tab[25]['objective'])}.",
        f"<b>Its weakness is event days and low exposure.</b> {conc['top10_share_of_span_excess'] * 100:.0f} % of the "
        f"2017–2026 excess return came on its 10 best days, and the book averages {m['exposure_span'] * 100:.0f} % "
        "invested: it is a liquidity-provision sleeve, not a replacement for index exposure.",
        "<b>Next step: a paper trial, with the kill rules below fixed before the first trade.</b> Forward data are the "
        "only test left that no one has looked at.",
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--date", default=None, help="document date YYYY-MM-DD (default: today)")
    ap.add_argument("--strategy", default=None, help="strategy file (default: strategy.py)")
    ap.add_argument("--reports", default=R4)
    a = ap.parse_args()
    d = dt.date.fromisoformat(a.date) if a.date else None
    info = build(a.out, d, a.strategy, a.reports)
    print(f"wrote {info['out']} ({info['pages']} pages; {len(info['trades'])} trades, worked example {info['example_day']:%Y-%m-%d})")


if __name__ == "__main__":
    main()
