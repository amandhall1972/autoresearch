"""
Build reports/index.html: a self-contained results page (inline SVG charts, no external data). With the
protocol-v4 reports (reports/v4: validate4.py, lineage4.py, deep4.py) it is the v2-strategy page; otherwise, or
with --v1, the v1 study page from validate.py, lineage.py --holdout, diagnose.py and results.tsv.
Usage: python make_page.py [--dir reports] [--v1]
"""
import csv, html, json, math, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "reports")
if "--dir" in sys.argv:
    R = sys.argv[sys.argv.index("--dir") + 1]


def load(p):
    p = os.path.join(R, p)
    return json.load(open(p)) if os.path.exists(p) else None


def esc(s):
    return html.escape(str(s))


def fmt(x, d=2, pct=False, sign=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    if pct:
        return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"
    return f"{x:+.{d}f}" if sign else f"{x:.{d}f}"


def cls(x):
    return "pos" if (x is not None and x > 0) else ("neg" if (x is not None and x < 0) else "")


# ----------------------------------------------------------------------------- SVG helpers
def line_chart(series, width=860, height=300, log=True, shade=None, labels=None, ylabel="Growth of ₹1"):
    """series: list of (name, pd.Series of levels, css var color, stroke width)."""
    pad_l, pad_r, pad_t, pad_b = 56, 16, 14, 30
    idx = series[0][1].index
    allv = np.concatenate([s.values for _, s, _, _ in series])
    allv = allv[np.isfinite(allv)]
    lo, hi = float(allv.min()), float(allv.max())
    f = (lambda v: math.log(v)) if log else (lambda v: v)
    flo, fhi = f(lo * 0.97), f(hi * 1.03)
    x0, x1 = idx[0].value, idx[-1].value
    X = lambda t: pad_l + (t.value - x0) / (x1 - x0) * (width - pad_l - pad_r)
    Y = lambda v: pad_t + (1 - (f(v) - flo) / (fhi - flo)) * (height - pad_t - pad_b)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart" preserveAspectRatio="xMidYMid meet">']
    if shade:
        a, b, lab = shade
        xa, xb = X(pd.Timestamp(a)), X(pd.Timestamp(b))
        out.append(f'<rect x="{xa:.1f}" y="{pad_t}" width="{xb - xa:.1f}" height="{height - pad_t - pad_b}" fill="var(--shade)"/>')
        out.append(f'<text x="{xa + 6:.1f}" y="{height - pad_b - 8}" class="ann">{esc(lab)}</text>')
    # y ticks
    ticks = []
    if log:
        for v in [0.8, 0.9, 1.0, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]:
            if lo * 0.97 <= v <= hi * 1.03:
                ticks.append(v)
    else:
        step = (hi - lo) / 4
        ticks = [lo + k * step for k in range(5)]
    for v in ticks:
        y = Y(v)
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{v:g}</text>')
    # x ticks: years
    for yr in range(idx[0].year, idx[-1].year + 1):
        t = pd.Timestamp(f"{yr}-01-01")
        if t < idx[0]:
            continue
        x = X(t)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{height - pad_b}" class="grid"/>')
        out.append(f'<text x="{x + 3:.1f}" y="{height - 10}" class="tick">{yr}</text>')
    for _name, s, col, sw in series:
        s = s.dropna()
        step = max(1, len(s) // 600)
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in list(s.items())[::step] + [list(s.items())[-1]])
        out.append(f'<polyline points="{pts}" fill="none" stroke="var({col})" stroke-width="{sw}" stroke-linejoin="round"/>')
        out.append(f'<circle cx="{X(s.index[-1]):.1f}" cy="{Y(s.iloc[-1]):.1f}" r="3" fill="var({col})"/>')
    out.append("</svg>")
    leg = "".join(f'<span class="key"><i style="background:var({col})"></i>{esc(name)}</span>' for name, _, col, _ in series)
    return f'<figure class="fig">{"".join(out)}<figcaption class="legend">{leg}</figcaption></figure>'


def bar_groups(groups, keys, width=860, height=300, ylab="Sharpe", cap=None):
    """groups: list of (label, {key: value}); keys: list of (key, name, css var). With cap, bars above it are
    drawn to the cap and labelled with their value."""
    pad_l, pad_r, pad_t, pad_b = 48, 12, 16, 46
    vals = [g[1].get(k) for g in groups for k, _, _ in keys if g[1].get(k) is not None]
    if cap is not None:
        vals = [min(v, cap) for v in vals]
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    span = hi - lo or 1.0
    lo -= 0.06 * span; hi += 0.08 * span
    Y = lambda v: pad_t + (1 - (v - lo) / (hi - lo)) * (height - pad_t - pad_b)
    gw = (width - pad_l - pad_r) / len(groups)
    bw = gw * 0.8 / len(keys)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    step = 1.0 if (hi - lo) > 3 else 0.5
    v = math.ceil(lo / step) * step
    while v <= hi:
        y = Y(v)
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}" class="{"zero" if abs(v) < 1e-9 else "grid"}"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{v:g}</text>')
        v += step
    for gi, (lab, d) in enumerate(groups):
        gx = pad_l + gi * gw + gw * 0.1
        for ki, (k, _, col) in enumerate(keys):
            val = d.get(k)
            if val is None:
                continue
            x = gx + ki * bw
            shown = min(val, cap) if cap is not None else val
            y0, y1 = Y(0), Y(shown)
            out.append(f'<rect x="{x:.1f}" y="{min(y0, y1):.1f}" width="{bw * 0.92:.1f}" height="{abs(y1 - y0):.1f}" fill="var({col})"><title>{esc(lab)}: {val:.2f}</title></rect>')
            if shown != val:
                out.append(f'<text x="{x + bw * 0.46:.1f}" y="{y1 + 12:.1f}" class="tick inbar" text-anchor="middle">{val:.2f}↑</text>')
        out.append(f'<text x="{pad_l + gi * gw + gw / 2:.1f}" y="{height - 26}" class="tick" text-anchor="middle">{esc(lab)}</text>')
    out.append(f'<text x="12" y="{pad_t + 8}" class="tick" transform="rotate(-90 12 {pad_t + 8})" text-anchor="end">{esc(ylab)}</text>')
    out.append("</svg>")
    leg = "".join(f'<span class="key"><i style="background:var({col})"></i>{esc(name)}</span>' for _, name, col in keys)
    return f'<figure class="fig">{"".join(out)}<figcaption class="legend">{leg}</figcaption></figure>'


def table(head, rows, num_from=1):
    h = "".join(f"<th>{esc(x)}</th>" for x in head)
    body = []
    for r in rows:
        tds = []
        for j, x in enumerate(r):
            c = "num" if j >= num_from else ""
            if isinstance(x, tuple):
                x, extra = x
                c = (c + " " + extra).strip()
            tds.append(f'<td class="{c}">{x}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


# ----------------------------------------------------------------------------- page
# ----------------------------------------------------------------------------- the v2 (protocol v4) page
CSS_V4 = """
/* Layout: one reading column, figures full width; RAAS identity (Neela ink-blue, Kagaz paper, Syahi ink),
   Outfit for display, IBM Plex for text and numbers; the Kesari saffron accent appears once, on the eyebrow rule. */
:root {
  --bg:#F4F1E9; --surface:#FAF8F3; --fg:#101B29; --muted:#5F6979; --line:#D8D3C5; --shade:rgba(16,31,88,.06);
  --brand:#101F58; --accent:#D07018; --pos:#1F7A4D; --neg:#B3261E; --chip:#E9E5DA; --on-bar:#FAF8F3;
  --c-v2:#101F58; --c-r0:#6381B0; --c-v1:#B3261E; --c-bees:#8A93A1; --c-nifty:#C3C9D2;
  --f-display:"Outfit","Avenir Next","Segoe UI",sans-serif; --f-body:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#0B1230; --surface:#111A3D; --fg:#ECE8DD; --muted:#A3ABBA; --line:#26305A; --shade:rgba(168,204,240,.07);
  --brand:#A8CCF0; --accent:#E9A15A; --pos:#5CC58E; --neg:#F07A62; --chip:#1A2450; --on-bar:#0B1230;
  --c-v2:#A8CCF0; --c-r0:#6381B0; --c-v1:#F07A62; --c-bees:#8A93A1; --c-nifty:#3B4453; color-scheme:dark } }
:root[data-theme="dark"] {
  --bg:#0B1230; --surface:#111A3D; --fg:#ECE8DD; --muted:#A3ABBA; --line:#26305A; --shade:rgba(168,204,240,.07);
  --brand:#A8CCF0; --accent:#E9A15A; --pos:#5CC58E; --neg:#F07A62; --chip:#1A2450; --on-bar:#0B1230;
  --c-v2:#A8CCF0; --c-r0:#6381B0; --c-v1:#F07A62; --c-bees:#8A93A1; --c-nifty:#3B4453; color-scheme:dark }
body { background:var(--bg); color:var(--fg); font:16px/1.6 var(--f-body); }
.wrap { max-width:920px; margin:0 auto; padding-inline:16px; padding-block:40px 64px; display:flex; flex-direction:column; gap:44px }
h1,h2,h3 { font-family:var(--f-display); text-wrap:balance; margin:0; color:var(--brand) }
h1 { font-size:clamp(30px,5.2vw,48px); font-weight:600; line-height:1.06; letter-spacing:-.015em }
h2 { font-size:25px; font-weight:600; margin-bottom:12px; letter-spacing:-.01em }
h3 { font-size:18px; font-weight:600; margin:18px 0 8px }
p { margin:0 0 12px; max-width:68ch }
.eyebrow { font:500 12px/1.3 var(--f-mono); letter-spacing:.08em; text-transform:uppercase; color:var(--muted) }
.rule { width:56px; height:3px; background:var(--accent); border-radius:2px }
.lede { font-size:18px; max-width:68ch }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px }
.card { background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:16px 16px 14px; display:flex; flex-direction:column; gap:4px; min-width:0 }
.card .sub { color:var(--muted); font-size:13px }
.big { font:500 38px/1.1 var(--f-mono); font-variant-numeric:tabular-nums }
.note { font-size:13px; color:var(--muted) }
.pos { color:var(--pos) } .neg { color:var(--neg) }
.fig { margin:0; background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:12px 12px 8px }
.chart { width:100%; height:auto; display:block }
.fig { overflow-x:auto }
@media (max-width:560px) { .chart { min-width:620px } }
.chart .grid { stroke:var(--line); stroke-width:1 } .chart .zero { stroke:var(--muted); stroke-width:1 }
.chart .tick { fill:var(--muted); font:11px var(--f-mono) } .chart .ann { fill:var(--muted); font:12px var(--f-body) }
.chart .inbar { fill:var(--on-bar) }
.legend { display:flex; flex-wrap:wrap; gap:16px; font-size:13px; color:var(--muted); padding:6px 4px 2px }
.key { display:inline-flex; align-items:center; gap:6px } .key i { width:14px; height:4px; border-radius:2px; display:inline-block }
.tw { overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:var(--surface) }
table { border-collapse:collapse; width:100%; font-size:14px }
th { text-align:left; font:600 12px var(--f-body); letter-spacing:.04em; text-transform:uppercase; color:var(--muted); padding:10px 12px; border-bottom:1px solid var(--line); white-space:nowrap }
td { padding:8px 12px; border-bottom:1px solid var(--line); white-space:nowrap }

tr:last-child td { border-bottom:0 }
td.num { font-family:var(--f-mono); font-variant-numeric:tabular-nums; text-align:right }
td.tx { white-space:normal; text-align:left; font-family:var(--f-body); min-width:240px }
code { font-family:var(--f-mono); font-size:.92em; background:var(--chip); padding:1px 5px; border-radius:4px }
dl.rules { margin:0; border:1px solid var(--line); border-radius:8px; background:var(--surface) }
.rulerow { display:grid; grid-template-columns:150px 1fr; gap:12px; padding:12px 16px; border-bottom:1px solid var(--line) }
.rulerow:last-child { border-bottom:0 }
.rulerow dt { font-weight:600 } .rulerow dd { margin:0; min-width:0 }
.tag { font:500 11px var(--f-mono); color:var(--muted); display:block; margin-top:2px; letter-spacing:.04em }
@media (max-width:560px) { .rulerow { grid-template-columns:1fr; gap:2px } }
.callout { border:1px solid var(--line); border-left:4px solid var(--brand); background:var(--surface); padding:14px 16px; border-radius:0 8px 8px 0 }
ul { margin:0; padding-left:20px; max-width:70ch } li { margin-bottom:6px }
pre { background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:12px 14px; overflow-x:auto; font:13px/1.5 var(--f-mono) }
footer { color:var(--muted); font-size:13px }
a { color:var(--brand) }
:focus-visible { outline:2px solid var(--brand); outline-offset:2px }
"""


def main_v4(R4):
    """The results page for the v2 strategy (protocol v4); False when its reports are missing."""
    def ld(name):
        p = os.path.join(R4, name)
        return json.load(open(p)) if os.path.exists(p) else None
    v, lin, deep = ld("validation.json"), ld("lineage.json"), ld("deep_holdout.json")
    if v is None or lin is None or deep is None:
        return False
    m, bm = v["metrics"], v["benchmarks"]
    by = {r["file"]: r for r in lin}
    v1, r0 = by["v4/v1_final.py"], by["v4/r0_ema_timer.py"]
    drow = {r["file"]: r for r in deep["rows"]}
    dfin = next(r for r in deep["rows"] if "final" in r["roles"])
    eq = pd.read_csv(os.path.join(R4, "equity_curves.csv"), index_col=0, parse_dates=True)
    tsv = pd.read_csv(os.path.join(HERE, "results_v4.tsv"), sep="\t")
    by_round = tsv.groupby("round").size().to_dict()
    lenses = tsv[tsv["round"].str.match(r"v4-[1-9]")].groupby("round")["lens"].nunique().to_dict()
    import importlib.util
    spec = importlib.util.spec_from_file_location("strategy_page_v4", os.path.join(HERE, v["strategy"]))
    S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
    p = S.PARAMS
    nifty = bm["nifty50"]
    cs = {r["cost_bps"]: r for r in v["cost_sensitivity"]}
    bs, jp, evd, wf = v["bootstrap"], v["joint_perturbation"], v["event_days"], v["walk_forward"]
    dsr = bs["span"]["deflated"]
    n_rows = len(tsv)
    smp = deep.get("sampling")
    if smp is None:
        raise SystemExit("reports/v4/deep_holdout.json has no sampling error: run python deep4.py --rebuild-report")
    fs = smp["strategies"][dfin["file"]]
    dr0 = smp["differences"][f"{dfin['file']} - v4/r0_ema_timer.py"]
    dnx = smp["differences"][f"{dfin['file']} - NIFTY50"]
    W = {"pre": "2017–19", "is": "2020–23", "oos": "2024–26", "span": "2017–26", "full": "2020–26"}

    cards = "".join(
        f'<div class="card"><div class="eyebrow">{esc(a)}</div><div class="sub">{esc(c)}</div>'
        f'<div class="big {cls(x)}">{fmt(x)}</div><div class="note">{esc(n)}</div></div>' for a, c, x, n in [
            ("2024–2026", "Jan 2024 – Oct 2026", m["sharpe_oos"], f"v1 strategy: {fmt(v1['sharpe_oos'])}"),
            ("2020–2026", "Jan 2020 – Oct 2026", m["sharpe_full"], f"NIFTY 50: {fmt(nifty['full'])}"),
            ("Worst window", "2017–19, 2020–23, 2024–26", m["objective"], f"v1 strategy: {fmt(v1['objective'])}"),
            ("Deep hold-out", "Apr 2011 – May 2016, scored once", dfin["sharpe_deep"], f"NIFTY 50: {fmt(dfin['bench_sharpe_deep'])}"),
        ])
    series = [("NIFTY 50 index", eq["nifty50"], "--c-nifty", 1.3), ("NIFTYBEES", eq["niftybees"], "--c-bees", 1.3)]
    if "v1_final" in eq:
        series.append(("v1 strategy", eq["v1_final"], "--c-v1", 1.3))
    series += [("EMA timer R0", eq["r0_ema_timer"], "--c-r0", 1.5), ("v2 strategy", eq["strategy"], "--c-v2", 2.4)]
    eq_chart = line_chart(series, shade=("2020-01-01", "2023-12-31", "2020–23"))
    groups = [(f"{W[k]}", {"v1": v1[f"sharpe_{k}"], "r0": r0[f"sharpe_{k}"], "nifty": nifty[k], "v2": m[f"sharpe_{k}"]})
              for k in ("pre", "is", "oos")]
    groups.append(("2011–16 (deep)", {"v1": drow["v4/v1_final.py"]["sharpe_deep"], "r0": drow["v4/r0_ema_timer.py"]["sharpe_deep"],
                                      "nifty": dfin["bench_sharpe_deep"], "v2": dfin["sharpe_deep"]}))
    win_chart = bar_groups(groups, [("v1", "v1 strategy", "--c-v1"), ("r0", "EMA timer R0", "--c-r0"),
                                    ("nifty", "NIFTY 50", "--c-bees"), ("v2", "v2 strategy", "--c-v2")], cap=2.0)
    yearly = table(["Year", "Return", "Sharpe", "Max DD", "Exposure", "NIFTYBEES", "NIFTY Sharpe", "Cash"],
                   [[r["year"], (fmt(r["strat_ret"], 1, pct=True), "" if abs(r["strat_ret"] - r["rf"]) <= 0.0025 else cls(r["strat_ret"] - r["rf"])),
                     fmt(r["strat_sharpe"]),
                     fmt(r["strat_maxdd"], 1, pct=True), fmt(r["exposure"], 0, pct=True), fmt(r["bees_ret"], 1, pct=True),
                     fmt(r["nifty_sharpe"]), fmt(r["rf"], 1, pct=True)] for r in v["yearly"]])
    costs = table(["One-way cost (bps)", "2017–19", "2020–23", "2024–26", "Worst window", "CAGR 2017–26"],
                  [[k, fmt(r["sharpe_pre"]), fmt(r["sharpe_is"]), fmt(r["sharpe_oos"]), (fmt(r["objective"]), cls(r["objective"])),
                    fmt(r["cagr_span"], 1, pct=True)] for k, r in sorted(cs.items())])
    boot = table(["Window", "Sharpe", "t-stat", "95% bootstrap interval", "Resamples > 0"],
                 [[W[k], fmt(bs[k]["sharpe"]), fmt(bs[k]["tstat"]), f'{fmt(bs[k]["ci95"][0])} to {fmt(bs[k]["ci95"][1])}',
                   (fmt(bs[k]["p_sharpe_gt_0"], 1, pct=True) if bs[k]["p_sharpe_gt_0"] < 0.9995 else "> 99.9%")]
                  for k in ("pre", "is", "oos", "span", "full")])
    events = table(["Best days removed", "2017–19", "2020–23", "2024–26", "Worst", "R0 worst"],
                   [[f"{k} per window", fmt(evd["strategy"][k]["pre"]), fmt(evd["strategy"][k]["is"]), fmt(evd["strategy"][k]["oos"]),
                     (fmt(evd["strategy"][k]["worst"]), cls(evd["strategy"][k]["worst"])), fmt(evd["r0_ema_timer"][k]["worst"])]
                    for k in ("0", "5", "10")])
    order = [dfin] + [drow[f] for f in ("v4/r0_ema_timer.py", "v4/v1_final.py", "v4/v1_baseline.py") if f in drow] + \
        [r for r in deep["rows"] if r is not dfin and r["file"] not in ("v4/r0_ema_timer.py", "v4/v1_final.py", "v4/v1_baseline.py")]
    deep_tab = table(["Strategy", "Role", "Sharpe", "CAGR", "Max DD", "Exposure"],
                     [[f"<code>{esc(r['file'])}</code>", esc("/".join(r["roles"])), (fmt(r["sharpe_deep"]), cls(r["sharpe_deep"])),
                       fmt(r["cagr_deep"], 1, pct=True), fmt(r["maxdd_deep"], 1, pct=True), fmt(r["exposure_deep"], 0, pct=True)]
                      for r in order], num_from=2)
    outcomes = {
        "v4-1": "every lens beat v1; the one effect present in every window was the NIFTY's EMA trend, so the plain EMA timer R0 became the bar (v4.1)",
        "v4-2": f"a pullback sleeve on top of R0 lifted its worst window from {fmt(r0['objective'])} to {fmt(by['v4/r2_pullbackcombo.py']['objective'])} "
                "(2020–23 slightly lower, inside the 0.10 tolerance); all rejected in audit, which exposed two rule flaws (v4.2)",
        "v4-3": "merge: the pullback strategy alone. Deleting the R0 core raised the worst window and lowered the other two; "
                "the with-core version was registered for the deep hold-out",
        "v4-4": "dry: each lever raised the objective but failed the year-by-year or plateau gates",
        "v4-5": "dry: no change passed every gate (a market-conditional exit reached 0.93 but failed the year-by-year and plateau "
                "gates); the strategy was fixed and the deep hold-out scored",
    }
    rounds_tab = table(["Round", "Lenses", "Logged rows", "Outcome"],
                       [[k[-1], lenses.get(k, 0), f"{by_round.get(k, 0):,}", (esc(outcomes[k]), "tx")]
                        for k in ("v4-1", "v4-2", "v4-3", "v4-4", "v4-5") if k in by_round], num_from=1)
    rules = [
        ("Trigger", "EMA, VWAP", f"Trade only on days when the NIFTY 50 closes above its {p['trend_n']}-day EMA and more than "
                                 f"{p['breadth_cut'] * 100:.0f}% of the members in their own uptrend (close above their {p['trend_n']}-day EMA) "
                                 f"close below their {p['vwap_n']}-day VWAP: a broad pullback inside a long uptrend."),
        ("Name filter", "EMA, RSI", f"On a trigger day, a member in its own uptrend whose Wilder RSI({p['rsi_n']}) is below {p['rsi_max']:g}: it was sold hard."),
        ("Selection", "VWAP", f"Free slots ({p['slots']}, each 1/{p['slots']} of the book) go to the eligible names deepest below their {p['vwap_n']}-day VWAP. "
                              f"Held names are traded back to 1/{p['slots']} at every open, so positions do not drift."),
        ("Exit", "VWAP", f"Sell as soon as the name closes back above its {p['vwap_n']}-day VWAP, or after {p['max_hold']} sessions; "
                         "names leaving the index are sold by the harness."),
        ("Execution", "", "Signals on the close, orders at the next open, 15 bps one-way cost (STT, fees, slippage); idle capital earns the RBI repo rate."),
    ]
    rules_html = "".join(f"<div class='rulerow'><dt>{esc(a)}<span class='tag'>{esc(t)}</span></dt><dd>{esc(b_)}</dd></div>" for a, t, b_ in rules)
    changed = table(["", "v1 (RCG-RN-REV-001)", "v2 (this page)"], [
        ["Chosen on", ("one window, 2020–2023", "tx"), ("the worst of 2017–19, 2020–23 and 2024–26", "tx")],
        ["The trade", ("timed baskets bought when the index RSI turned up", "tx"), ("broad pullbacks in uptrending names, sold when bought back to VWAP", "tx")],
        ["Parameters", "40", str(sum(1 for k in p if not k.startswith("use_")))],
        ["2024–26 Sharpe", (fmt(v1["sharpe_oos"]), "num neg"), (fmt(m["sharpe_oos"]), "num " + cls(m["sharpe_oos"]))],
        ["2020–26 Sharpe", (fmt(v1["sharpe_full"]), "num"), (fmt(m["sharpe_full"]), "num")],
        ["Deep hold-out", (fmt(drow["v4/v1_final.py"]["sharpe_deep"]), "num " + cls(drow["v4/v1_final.py"]["sharpe_deep"])),
         (fmt(dfin["sharpe_deep"]), "num " + cls(dfin["sharpe_deep"]))],
    ], num_from=9)
    s_ = dfin["sharpe_deep"]; b_ = dfin["bench_sharpe_deep"]
    deep_line = (f"On data no rule was chosen on it scores {fmt(s_)}, against {fmt(b_)} for the NIFTY 50 and "
                 f"{fmt(drow['v4/r0_ema_timer.py']['sharpe_deep'])} for the EMA timer.")
    page = f"""<title>Nifty RSI·EMA·VWAP Study</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS_V4}</style>
<div class="wrap">
<header style="display:flex;flex-direction:column;gap:14px">
  <div class="rule" aria-hidden="true"></div>
  <div class="eyebrow">RAAS Capital · Quant Desk · RCG-RN-REV-002 · NSE Nifty 50 · daily bars</div>
  <h1>RSI + EMA + VWAP on the Nifty 50, v2</h1>
  <p class="lede">The v1 strategy was chosen on 2020–2023 alone and lagged cash in every other window (Sharpe over cash {fmt(v1['sharpe_pre'])} on
  2017–2019 and {fmt(v1['sharpe_oos'])} on 2024–2026). The v2 strategy keeps RSI, EMA and VWAP but uses them for a different trade, buying broad pullbacks
  inside uptrends, and was chosen on the worst of all three windows: <b class="{cls(m['sharpe_pre'])}">{fmt(m['sharpe_pre'])}</b>,
  <b class="{cls(m['sharpe_is'])}">{fmt(m['sharpe_is'])}</b> and <b class="{cls(m['sharpe_oos'])}">{fmt(m['sharpe_oos'])}</b>. {deep_line}</p>
</header>
<section class="cards" aria-label="Headline Sharpe ratios">{cards}</section>
<section><h2>Equity, net of costs</h2>
<p>Growth of ₹1 from April 2017, log scale; shaded: 2020–2023, the only window v1 was chosen on. The v2 book holds stocks on
{fmt(m['pct_days_invested_span'], 0, pct=True)} of sessions with {fmt(m['exposure_span'], 0, pct=True)} of the capital on average; the rest earns the repo rate.
Maximum drawdown {fmt(m['maxdd_span'], 1, pct=True)}, CAGR {fmt(m['cagr_span'], 1, pct=True)} over 2017–2026.
The v1 line ends higher only because of 2020–2023, the window it was fitted to; it lost to cash before and after it.</p>
{eq_chart}</section>
<section><h2>Every window, and the one nobody saw</h2>
<p>Sharpe ratio of daily returns over the RBI repo rate, net of 15 bps one-way. The first three windows were all used to choose the v2 rules;
2011–2016 was built for this study, kept sealed, and scored once at the end. Bars above 2 are cut and labelled.</p>
{win_chart}</section>
<section><h2>What changed</h2>{changed}</section>
<section><h2>The v2 strategy</h2><dl class="rules">{rules_html}</dl>
<p style="margin-top:12px" class="note">Parameters: <code>{esc(json.dumps({k: x for k, x in p.items() if not k.startswith('use_')}))}</code>.
The source is <code>nse/strategy.py</code>; the research note RCG-RN-REV-002 explains each rule and traces a trigger day to its exits.</p></section>
<section><h2>Year by year</h2>
<p>Return: green when the year's compounded return beat cash, red when it trailed it, plain when level within 0.25 points
({", ".join(str(r["year"]) for r in v["yearly"] if abs(r["strat_ret"] - r["rf"]) <= 0.0025)}). 2017 runs from April, 2026 to October.</p>{yearly}</section>
<section><h2>Robustness</h2>
<p><b>Costs.</b> Turnover is {fmt(m['turnover_yr_span'], 1)}x a year, so costs matter: the worst window falls from {fmt(cs[15]['objective'])} at 15 bps to
{fmt(cs[25]['objective'])} at 25 bps and {fmt(cs[40]['objective'])} at 40 bps.</p>{costs}
<p style="margin-top:16px"><b>Sampling error.</b> Stationary block bootstrap, mean block 20 sessions; the last column is the share of resamples with a
positive Sharpe, which is not the probability that the true Sharpe is positive.</p>{boot}
<p style="margin-top:16px"><b>Multiple testing.</b> The deflated Sharpe ratio for 2017–2026 is {fmt(dsr['dsr'])}. It is a probability, not a Sharpe: the
chance that the true Sharpe exceeds {fmt(dsr['sr0_ann'])}, the Sharpe the best of {dsr['n_trials']:,} logged tries would reach by luck alone. It is below the
conventional 0.95, so once the search is charged for, the 2017–2026 Sharpe of {fmt(bs['span']['sharpe'])} is not statistically distinguishable from the
{fmt(dsr['sr0_ann'])} that luck alone would produce. The tries are the {dsr['n_trials']:,} rows of the log with a 2017–2026 Sharpe (of {n_rows:,}); many
re-evaluate existing candidates, which narrows their spread and lowers the hurdle, so the figure is if anything generous.</p>
<p style="margin-top:16px"><b>Parameters.</b> Moving every parameter at once by a random factor between 0.8 and 1.2 ({jp['draws']} draws) gives a mean
worst-window Sharpe of {fmt(jp['mean'])} (10th percentile {fmt(jp['p10'])}); {fmt(m['objective'])} is the top of that in-sample range. Re-selecting the
parameters each year from 2019 on the years before gives {fmt(wf['sharpe_concat'])} over 2019–2026 and {fmt(wf['sharpe_concat_2024_26'])} on 2024–2026.
Both checks run on the years the rules were chosen on, so they show how fragile the fit is, not how it will do on new data.</p>
<p><b>Event days.</b> A dip buyer is paid on rebound days: {fmt(evd['concentration']['top10_share_of_span_excess'], 0, pct=True)} of the
2017–2026 excess return came on its 10 best days. Without each window's own best days:</p>{events}</section>
<section><h2>Deep hold-out, April 2011 – May 2016</h2>
<p>The point-in-time Nifty 50 of 2008–2016 ({deep['data']['members_with_bars_min']}–{deep['data']['members_with_bars_max']} members with bars on any day),
{deep['data']['sessions_scored']:,} sessions, scored once for a set of strategies registered before the look. NIFTY 50 over the same window:
Sharpe {fmt(dfin['bench_sharpe_deep'])}, CAGR {fmt(dfin['bench_cagr_deep'], 1, pct=True)}. Strategy CAGRs include the repo rate earned on idle capital.</p>{deep_tab}
<p style="margin-top:12px">Five years is a short sample. The v2 strategy's 95% bootstrap interval runs from {fmt(fs['ci95'][0])} to {fmt(fs['ci95'][1])}
(stationary bootstrap, mean block 20 sessions), and its lead over R0 from {fmt(dr0['ci95'][0])} to {fmt(dr0['ci95'][1])}; its lead over the NIFTY 50
({fmt(dnx['ci95'][0])} to {fmt(dnx['ci95'][1])}) is not established on this window alone. With 5-session blocks the lower bound is
{fmt(fs['ci95_block5'][0])}. The daily t-statistic ({fmt(fs['tstat_daily'], 1)}) treats days as independent; a dip buyer's days are negatively
autocorrelated (the variance of its 20-day sums is {fmt(fs['variance_ratio_20d'], 0, pct=True)} of what independent days would give), so on monthly
sums the t-statistic is {fmt(fs['tstat_monthly'], 1)}.</p></section>
<section><h2>How the research ran</h2>
<ul>
<li>One objective for every regime: the lowest of the three window Sharpe ratios, so a rule that buys one regime at the cost of another does not raise it.</li>
<li>Keep gates against the incumbent: no window more than 0.10 worse, the 10 best days removed, every single calendar year removed, positive years,
0.05 of objective per added parameter, ±20% parameter plateaus; and, for the final strategy, switching off RSI, EMA or VWAP must change the
target weights on at least 5% of sessions.</li>
<li>Researchers worked in parallel with one lens each; each lens's best candidate was re-run and audited by an independent agent before the lead decided.</li>
<li>Two amendments and one merge were written into <code>program.md</code> before the next round, and before any look at 2011–2016.</li>
</ul>
<div style="margin-top:14px">{rounds_tab}</div></section>
<section class="callout"><h2>Bottom line</h2>
<p>2024–2026 is now positive ({fmt(m['sharpe_oos'])}) and 2020–2026 strong ({fmt(m['sharpe_full'])}), but both windows were used to choose the rules.
The only evidence that was not is the deep hold-out ({fmt(dfin['sharpe_deep'])}, 95% interval {fmt(fs['ci95'][0])} to {fmt(fs['ci95'][1])}), where the
pullback strategies are the only positive ones. After charging for the {dsr['n_trials']:,} logged tries with a 2017–2026 Sharpe, the 2017–2026
Sharpe is not statistically significant (deflated Sharpe ratio {fmt(dsr['dsr'])}).
The strategy is a low-exposure liquidity-provision sleeve, sensitive to costs and to a few rebound days. Paper-trade it with the kill rules
of RCG-RN-REV-002 fixed before the first trade.</p></section>
<section><h2>Reproduce</h2>
<pre>cd nse
python -m pytest -q tests                      # harness, protocol v4, deep loader, PDFs
python run4.py --strategy strategy.py          # the three windows + look-ahead check
python validate4.py --strategy strategy.py     # costs, bootstrap, DSR, plateau, walk-forward
python lineage4.py                             # every v4 strategy on every window
python deep4.py --final strategy.py            # the one deep look (refuses a second)
python deep4.py --rebuild-report               # deep report + sampling error (no look)
python make_pdf_v4.py                          # research note RCG-RN-REV-002</pre></section>
<footer>Data: Financial Modeling Prep daily bars; Nifty 50 membership from NSE index data via the Historical Nifty 50 Constituent Weights dataset;
repo rate from the Reserve Bank of India. Price returns (no dividends). Hypothetical backtest results, not investment advice. Branch <code>autoresearch/oct9-nse</code>.</footer>
</div>"""
    open(os.path.join(R, "index.html"), "w").write(page)
    print("wrote", os.path.join(R, "index.html"), len(page), "bytes")
    return True


def main():
    if "--v1" not in sys.argv and main_v4(os.path.join(R, "v4")):
        return
    main_v1()


def main_v1():
    """The v1 study page (protocol v1-v3); built when the protocol-v4 reports are absent, or with --v1."""
    v = load("validation.json"); b = load("baseline/validation.json"); lin = load("lineage.json") or []
    dg = load("diagnostics.json")
    m = v["metrics"]
    tsv = list(csv.DictReader(open(os.path.join(HERE, "results.tsv")), delimiter="\t"))
    n_exp = len(tsv)
    rounds = {}
    for r in tsv:
        d = r["description"]
        if d.startswith("[r") and ":" in d[:6]:
            k = d[2:d.index(":")]
            if k.isdigit():
                rounds[int(k)] = rounds.get(int(k), 0) + 1
    final = lin[-1] if lin else {}
    pre = final.get("sharpe_pre")
    eq = pd.read_csv(os.path.join(R, "equity_curves.csv"), index_col=0, parse_dates=True)
    hf = os.path.join(R, "holdout_final_returns.csv")
    hold = pd.read_csv(hf, index_col=0, parse_dates=True) if os.path.exists(hf) else None
    import importlib.util
    spec = importlib.util.spec_from_file_location("strategy_page", os.path.join(HERE, "v4", "v1_final.py"))
    S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)

    hero_cards = [
        ("Selection window", "2020–2023", m["sharpe_is"], "In-sample Sharpe the loop maximised"),
        ("Validation window", "2024 – Oct 2026", m["sharpe_oos"], "Not used by the keep rule"),
        ("Sealed hold-out", "Apr 2017–2019", pre, "Scored once, at the end"),
        ("Whole period", "2020 – Oct 2026", m["sharpe_full"], "Selection + validation"),
    ]
    cards = "".join(f'<div class="card"><div class="eyebrow">{esc(a)}</div><div class="sub">{esc(c)}</div>'
                    f'<div class="big {cls(x)}">{fmt(x)}</div><div class="note">{esc(n)}</div></div>' for a, c, x, n in hero_cards)

    eq_chart = line_chart([("Strategy (net of costs)", eq["strategy"], "--accent", 2.2),
                           ("NIFTYBEES buy & hold", eq["niftybees"], "--bench", 1.4)],
                          shade=("2024-01-01", str(eq.index[-1].date()), "validation window (not used by the loop)"))
    hold_chart = ""; hold_note = ""
    if hold is not None:
        hs = (1 + hold["strategy"]).cumprod(); hb = (1 + hold["niftybees"].fillna(0)).cumprod()
        hold_chart = line_chart([("Strategy (net of costs)", hs, "--accent", 2.2), ("NIFTYBEES buy & hold", hb, "--bench", 1.4)], height=260)
        yrs = len(hold) / 252.0
        hold_cash = (1 + hold["cash"]).prod() ** (1 / yrs) - 1
        hold_note = (f" The strategy grew {fmt(hs.iloc[-1] ** (1 / yrs) - 1, 1, pct=True)} a year and NIFTYBEES {fmt(hb.iloc[-1] ** (1 / yrs) - 1, 1, pct=True)},"
                     f" while cash earned {fmt(hold_cash, 1, pct=True)}; Sharpe is measured over cash, so the strategy's is negative.")
    lin_groups = [(o["stage"].replace("keep ", "#"), {"is": o["sharpe_is"], "oos": o["sharpe_oos"], "pre": o.get("sharpe_pre")}) for o in lin]
    lin_keys = [("is", "2020–23 (selected on)", "--accent"), ("oos", "2024–26 (validation)", "--neg")]
    if lin and "sharpe_pre" in lin[0]:
        lin_keys.append(("pre", "2017–19 (sealed hold-out)", "--bench"))
    lin_chart = bar_groups(lin_groups, lin_keys) if lin else ""

    yearly = table(["Year", "Strategy return", "Strategy Sharpe", "Exposure", "Nifty 50 return", "Nifty Sharpe", "Cash"],
                   [[r["year"], (fmt(r["strat_ret"], 1, pct=True), cls(r["strat_ret"])), (fmt(r["strat_sharpe"]), cls(r["strat_sharpe"])),
                     fmt(r["exposure"], 0, pct=True), fmt(r["nifty_ret"], 1, pct=True), fmt(r["nifty_sharpe"]), fmt(r["rf"], 1, pct=True)] for r in v["yearly"]])
    costs = table(["One-way cost (bps)", "Sharpe 2020–23", "Sharpe 2024–26", "Sharpe 2020–26", "CAGR 2020–26"],
                  [[r["cost_bps"], fmt(r["sharpe_is"]), (fmt(r["sharpe_oos"]), cls(r["sharpe_oos"])), fmt(r["sharpe_full"]), fmt(r["cagr_full"], 1, pct=True)] for r in v["cost_sensitivity"]])
    bs = v["bootstrap"]
    boot = table(["Window", "Sharpe", "95% block-bootstrap CI", "P(Sharpe > 0)"],
                 [[lab, fmt(bs[k]["sharpe"]), f'{fmt(bs[k]["ci95"][0])} to {fmt(bs[k]["ci95"][1])}', fmt(bs[k]["p_sharpe_gt_0"], 0, pct=True)]
                  for k, lab in [("is", "2020–23"), ("oos", "2024–26"), ("full", "2020–26")]], num_from=1)
    lin_tab = table(["Stage", "Commit", "2020–23", "2020–21", "2022–23", "2024–26", "2020–26"] + (["2017–19 hold-out", "Nifty 2017–19"] if lin and "sharpe_pre" in lin[0] else []),
                    [[o["stage"], f'<code>{esc(o["commit"])}</code>', fmt(o["sharpe_is"]), fmt(o["sharpe_is_h1"]), fmt(o["sharpe_is_h2"]),
                      (fmt(o["sharpe_oos"]), cls(o["sharpe_oos"])), fmt(o["sharpe_full"])]
                     + ([(fmt(o.get("sharpe_pre")), cls(o.get("sharpe_pre"))), fmt(o.get("bench_sharpe_pre"))] if "sharpe_pre" in o else []) for o in lin], num_from=2)
    ps = v["param_sensitivity_summary"]; wf = v["walk_forward"]; dsr = bs["is"].get("deflated", {})
    diag_html = ""
    if dg:
        srow = [[per, s.get("spells"), fmt(s.get("hit_rate"), 0, pct=True), (fmt(s.get("mean_spell_excess"), 2, pct=True), cls(s.get("mean_spell_excess"))),
                 fmt(s.get("mean_excess_when_nifty_up"), 2, pct=True), fmt(s.get("mean_excess_when_nifty_down"), 2, pct=True), fmt(s.get("cost_drag_per_yr"), 2, pct=True)]
                for per, s in dg["summary"].items()]
        diag_html = ("<p>An invested spell is a maximal run of invested sessions: it starts at a switch-on or re-entry and ends at the switch-off, at a stand-aside above RSI 80, or when the exits empty the book. Excess is the spell's return over cash; "
                     "the two middle columns split the spells by whether the Nifty rose or fell over the same days.</p>"
                     + table(["Window", "Spells", "Won", "Mean excess", "Nifty up", "Nifty down", "Cost drag / yr"], srow))
        edge = table(["Year", "Formation days", "Bought: next 20 sessions", "All members", "Selection edge"],
                     [[r["year"], r["formation_days"], fmt(r["fwd20_bought"], 2, pct=True), fmt(r["fwd20_all_members"], 2, pct=True),
                       (fmt(r["edge"], 2, pct=True, sign=True), cls(r["edge"]))] for r in dg["selection_edge_by_year"]])
        diag_html += "<p style='margin-top:20px'>Does the stock selection itself still work? The table compares the next-20-session return of the names a cohort bought with the average member on the same days.</p>" + edge
    rounds_line = ", ".join(f"round {k}: {n}" for k, n in sorted(rounds.items()))
    p = S.PARAMS
    rules = [
        ("Regime", f"Trade only while the NIFTY 50's RSI({p['regime_rsi_n']}) is bullish: switch on above {p['regime_rsi_mid'] + p['regime_rsi_band']:g}, go fully to cash below {p['regime_rsi_mid'] - p['regime_rsi_band']:g}, and stand aside while it is above {p['regime_rsi_max']:g}."),
        ("Entry (EMA)", f"EMA{p['ema_fast']} above EMA{p['ema_slow']}, but not more than {p['ema_spread'] * 100:g}% above it."),
        ("Entry (VWAP)", f"Close above the {p['vwap_n']}-day VWAP."),
        ("Entry (RSI)", f"EMA{p['rsi_smooth']}-smoothed RSI({p['rsi_n']}) below {p['rsi_hi']:g}, with no reading above {p['rsi_peak']:g} in the last {p['rsi_peak_n']} days; the name's volatility expansion (vol20/vol120) is not in the members' top third."),
        ("Cohort", f"One basket is bought on the day the regime switches on and then left to ride: no rebalancing, no replacement buys. Weights are single-index mean-variance (shrunk beta over stock-specific variance), tilted to the strongest {p['vwap_short_n']}-day VWAP position and away from names with a recent shock or volume spike; no name above {p['w_cap'] * 100:g}%, the excess parked in NIFTYBEES."),
        ("Re-entry", f"After a stand-aside at RSI {p['regime_rsi_max']:g}, the book re-enters with the {p['re_k']} names that score highest on VWAP{p['vwap_n']}/VWAP{p['vwap_long_n']} momentum times RSI strength relative to the index."),
        ("Sizing", f"A fresh basket is scaled down when the Bank Nifty has just become more volatile than usual ({p['vol_n']}- vs {p['vol_long_n']}-day vol), and bought at {p['whipsaw_size'] * 100:g}% after a whipsaw of {p['whipsaw_days']} days or fewer."),
        ("While held", f"From day {p['vol_cap_delay'] + 1} the bottom {p['rerank_drop'] * 100:.0f}% of the book by its own score is sold once, and the book is trimmed to a {p['vol_cap'] * 100:g}% volatility budget, least-liked names first. When market breadth (share of members above VWAP{p['vwap_n']}) dips more than {p['dip_k'] * 100:g} points below its average and turns up, the surviving names are topped up with idle cash within that budget."),
        ("Exits", f"A name is sold on a close below {p['vwap_exit']:g} × its {p['vwap_n']}-day VWAP, when its {p['vwap_short_n']}-day VWAP drops under its {p['vwap_long_n']}-day VWAP, on a stock-specific shock ({p['shock_k']:g} residual sigmas) or a {p['vspike_k']:g}× volume spike. Once {(1 - p['exhaust']) * 100:g}% of the basket has been sold the rest goes too, and everything is sold when the regime switches off."),
        ("Execution", "Signals on the close, orders at the next open, 15 bps one-way cost (STT, fees, slippage), idle cash earns the RBI repo rate."),
    ]
    rules_html = "".join(f"<div class='rule'><dt>{esc(a)}</dt><dd>{esc(b_)}</dd></div>" for a, b_ in rules)
    verdict_pre = ""
    if pre is not None:
        verdict_pre = (f" On the sealed 2017–2019 hold-out, which no research decision ever touched, it scores <b class='{cls(pre)}'>{fmt(pre)}</b>"
                       f" against <b>{fmt(final.get('bench_sharpe_pre'))}</b> for the Nifty.")
    page = f"""<title>Nifty RSI·EMA·VWAP Study</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700;800&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* Layout: a single reading column with full-width figures; numbers in mono, headings in a condensed grotesk. */
:root {{
  --bg:#f5f6f8; --surface:#ffffff; --fg:#141922; --muted:#5b6474; --line:#dde1e8; --shade:rgba(20,25,34,.05);
  --accent:#c96a12; --bench:#2f6f8f; --pos:#1f7a4d; --neg:#b4402a; --chip:#eef0f4;
  --f-display:"Archivo","Helvetica Neue",Arial,sans-serif; --f-body:"IBM Plex Sans",system-ui,-apple-system,Segoe UI,sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#0f1218; --surface:#161b23; --fg:#e8ebf0; --muted:#9aa3b2; --line:#2a313d; --shade:rgba(232,235,240,.05);
  --accent:#f0913a; --bench:#6fb3d6; --pos:#5cc58e; --neg:#f07a62; --chip:#1d232d; color-scheme:dark }} }}
:root[data-theme="dark"] {{
  --bg:#0f1218; --surface:#161b23; --fg:#e8ebf0; --muted:#9aa3b2; --line:#2a313d; --shade:rgba(232,235,240,.05);
  --accent:#f0913a; --bench:#6fb3d6; --pos:#5cc58e; --neg:#f07a62; --chip:#1d232d; color-scheme:dark }}
body {{ background:var(--bg); color:var(--fg); font:16px/1.6 var(--f-body); }}
.wrap {{ max-width:920px; margin:0 auto; padding-inline:16px; padding-block:40px 64px; display:flex; flex-direction:column; gap:40px }}
h1,h2,h3 {{ font-family:var(--f-display); letter-spacing:-.01em; text-wrap:balance; margin:0 }}
h1 {{ font-size:clamp(30px,5vw,46px); font-weight:800; line-height:1.08 }}
h2 {{ font-size:24px; font-weight:700; margin-bottom:12px }}
p {{ margin:0 0 12px; max-width:68ch }}
.eyebrow {{ font:600 12px/1.2 var(--f-body); letter-spacing:.08em; text-transform:uppercase; color:var(--muted) }}
.lede {{ font-size:18px; color:var(--fg); max-width:70ch }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:16px 16px 14px; display:flex; flex-direction:column; gap:4px; min-width:0 }}
.card .sub {{ color:var(--muted); font-size:13px }}
.big {{ font:500 38px/1.1 var(--f-mono); font-variant-numeric:tabular-nums }}
.note {{ font-size:13px; color:var(--muted) }}
.pos {{ color:var(--pos) }} .neg {{ color:var(--neg) }}
.fig {{ margin:0; background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 12px 8px }}
.chart {{ width:100%; height:auto; display:block }}
.chart .grid {{ stroke:var(--line); stroke-width:1 }} .chart .zero {{ stroke:var(--muted); stroke-width:1 }}
.chart .tick {{ fill:var(--muted); font:11px var(--f-mono) }} .chart .ann {{ fill:var(--muted); font:12px var(--f-body) }}
.legend {{ display:flex; flex-wrap:wrap; gap:16px; font-size:13px; color:var(--muted); padding:6px 4px 2px }}
.key {{ display:inline-flex; align-items:center; gap:6px }} .key i {{ width:14px; height:4px; border-radius:2px; display:inline-block }}
.tw {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--surface) }}
table {{ border-collapse:collapse; width:100%; font-size:14px }}
th {{ text-align:left; font:600 12px var(--f-body); letter-spacing:.04em; text-transform:uppercase; color:var(--muted); padding:10px 12px; border-bottom:1px solid var(--line); white-space:nowrap }}
td {{ padding:8px 12px; border-bottom:1px solid var(--line); white-space:nowrap }}
tr:last-child td {{ border-bottom:0 }}
td.num {{ font-family:var(--f-mono); font-variant-numeric:tabular-nums; text-align:right }}
code {{ font-family:var(--f-mono); font-size:.92em; background:var(--chip); padding:1px 5px; border-radius:4px }}
dl.rules {{ margin:0; display:grid; gap:0; border:1px solid var(--line); border-radius:10px; background:var(--surface) }}
.rule {{ display:grid; grid-template-columns:140px 1fr; gap:12px; padding:12px 16px; border-bottom:1px solid var(--line) }}
.rule:last-child {{ border-bottom:0 }}
.rule dt {{ font-weight:600 }} .rule dd {{ margin:0; min-width:0 }}
@media (max-width:560px) {{ .rule {{ grid-template-columns:1fr; gap:2px }} }}
.callout {{ border-left:4px solid var(--neg); background:var(--surface); padding:14px 16px; border-radius:0 10px 10px 0 }}
ul {{ margin:0; padding-left:20px; max-width:70ch }} li {{ margin-bottom:6px }}
pre {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; overflow-x:auto; font:13px/1.5 var(--f-mono) }}
footer {{ color:var(--muted); font-size:13px }}
</style>
<div class="wrap">
<header style="display:flex;flex-direction:column;gap:14px">
  <div class="eyebrow">NSE · Nifty 50 universe · daily bars · Jan 2020 – Oct 2026</div>
  <h1>RSI + EMA + VWAP on the Nifty 50, refined by an autonomous research loop</h1>
  <p class="lede">The loop pushed the in-sample Sharpe ratio from <b>{fmt(b['metrics']['sharpe_is']) if b else '–'}</b> to <b>{fmt(m['sharpe_is'])}</b> over {len(rounds)} research rounds and {n_exp:,} logged experiments.
  On the 2024–2026 data the keep rule never used, the same strategy has a Sharpe of <b class="{cls(m['sharpe_oos'])}">{fmt(m['sharpe_oos'])}</b>.{verdict_pre}</p>
</header>
<section class="cards">{cards}</section>
<section><h2>Equity, net of costs</h2>
<p>Growth of ₹1, log scale. The book holds stocks on {fmt(m['pct_days_invested_full'], 0, pct=True)} of trading days, and on average {fmt(m['exposure_full'], 0, pct=True)} of the capital is invested; the rest earns the RBI repo rate.</p>
{eq_chart}</section>
<section><h2>What the in-sample gains bought out of sample</h2>
<p>Every strategy the loop kept, re-scored on the final harness. In-sample Sharpe rises at each stage; the 2024–2026 column shows how much of that carried over{', and the sealed 2017–2019 column is the first and only look at those years' if lin and 'sharpe_pre' in lin[0] else ''}.</p>
{lin_chart}
{lin_tab}</section>
{"<section><h2>Sealed hold-out, April 2017 – December 2019</h2><p>Point-in-time Nifty 50 of those years, including 11 names that left the index before 2020. Scored once, after the research ended." + hold_note + "</p>" + hold_chart + "</section>" if hold_chart else ""}
<section><h2>The strategy</h2><dl class="rules">{rules_html}</dl></section>
<section><h2>Year by year</h2>{yearly}</section>
<section><h2>Robustness checks</h2>
<p><b>Costs.</b> The edge in 2020–2023 survives far higher costs than assumed; 2024–2026 is negative at every cost level.</p>{costs}
<p style="margin-top:16px"><b>Sampling error.</b> Stationary block bootstrap (mean block 20 sessions).</p>{boot}
<p style="margin-top:16px"><b>Multiple testing.</b> The deflated Sharpe ratio over {dsr.get('n_trials', '–'):,} logged trials is {fmt(dsr.get('dsr'), 3)} (hurdle {fmt(dsr.get('sr0_ann'))}). That calculation treats the trials as independent draws; a search that keeps building on its own best result on the same four years is not that, so read it as a check on the arithmetic of selection, not as evidence of an edge. The out-of-sample columns above are the better guide.</p>
<p><b>Parameters.</b> {ps['n_perturbations']} one-at-a-time ±10/20% perturbations: median in-sample Sharpe {fmt(ps['median_sharpe_is'])} (base {fmt(m['sharpe_is'])}), {fmt(ps['frac_within_0.2_of_base_is'], 0, pct=True)} within 0.2 of the base; median 2024–26 Sharpe {fmt(ps['median_sharpe_oos'])}.
Walk-forward re-selection of those parameters year by year gives a Sharpe of {fmt(wf['sharpe_concat'])} over 2021–2026, mostly from 2021–2023, which the grid's centre was tuned on; the 2024–2026 test years are negative.</p></section>
{"<section><h2>Why it fails after 2023</h2>" + diag_html + "</section>" if diag_html else ""}
<section><h2>How the research ran</h2>
<ul>
<li>One editable file (<code>strategy.py</code>), a fixed harness, one metric: the 2020–2023 Sharpe. Six researchers launched per round (the log holds experiments from five in round 1, and from four in round 8, where two failed to start), each in its own git worktree, each with one lens; the lead verified, merged and combined their winners.</li>
<li>Experiments per round: {esc(rounds_line)}.</li>
<li>A keep had to raise in-sample Sharpe and pass five gates: both halves of 2020–2023, the five best days removed, every new parameter on a plateau, simplifications allowed, and no single trading episode carrying the gain.</li>
<li>After round 1 the lead looked at the 2024–2026 numbers, so that window became a validation window and a fresh, sealed 2017–2019 hold-out was built before round 2. From round 2 researchers saw neither.</li>
<li>Every run passes a dense lookahead check: weights are recomputed on truncated and future-scrambled data and must not change.</li>
</ul></section>
<section class="callout"><h2>Bottom line</h2>
<p>The loop did what it was asked: it found the rules that best explain 2020–2023. Those years held a crash and a strong recovery that a regime-timed, high-conviction book could exploit. After 2023 the same rules lose to cash: Sharpe {fmt(m['sharpe_oos'])} over 2024–2026, while the Nifty itself managed {fmt(m['bench_sharpe_oos'])}. Treat the in-sample figure as a measure of fit, not of expected return. Before trading any version, paper-trade it and judge it on data the loop has not seen.</p></section>
<section><h2>Reproduce</h2>
<pre>cd nse
python run.py                    # in-sample metrics + lookahead check (~10 s)
python run.py --report           # adds 2024–26 and full-period metrics
python validate.py --boot 2000   # costs, bootstrap, deflated Sharpe, perturbations, walk-forward
python lineage.py --holdout      # every kept strategy on all windows (logs each hold-out look)
python signals.py --capital 1000000 --holdings my_holdings.csv   # tomorrow's orders</pre></section>
<footer>Data: Financial Modeling Prep daily bars; Nifty 50 membership from NSE index data via the Historical Nifty 50 Constituent Weights dataset. Price returns (no dividends). Branch <code>autoresearch/oct9-nse</code>.</footer>
</div>"""
    open(os.path.join(R, "index.html"), "w").write(page)
    print("wrote", os.path.join(R, "index.html"), len(page), "bytes")


if __name__ == "__main__":
    main()
