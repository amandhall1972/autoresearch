"""Tests for the RAAS Capital brand layer and the research-note PDF (make_pdf.py).

The brand assets (fonts, logo) are not part of the public repository; the tests that need them are skipped
when nse/brand/ is absent. The PDF is built once per session into a temporary directory."""
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NSE = os.path.dirname(HERE)
sys.path.insert(0, NSE)
import raas_brand as B  # noqa: E402

HAVE_ASSETS = all(os.path.exists(os.path.join(B.FONT_DIR, f)) for f in B.FONT_FILES.values()) \
    and os.path.exists(B.LOGO_SVG)
HAVE_REPORTS = all(os.path.exists(os.path.join(NSE, "reports", f)) for f in
                   ["validation.json", "lineage.json", "diagnostics.json", "equity_curves.csv",
                    "holdout_final_returns.csv"])
needs_assets = pytest.mark.skipif(not HAVE_ASSETS, reason="RAAS brand assets not present (nse/brand/)")
needs_build = pytest.mark.skipif(not (HAVE_ASSETS and HAVE_REPORTS), reason="brand assets or reports missing")


# ----------------------------------------------------------------------------- numbers
def test_num_uses_true_minus_and_signs():
    assert B.num(-1.0632) == "−1.06"
    assert B.num(0.4154) == "0.42"
    assert B.num(0.021, 1, pct=True) == "2.1%"
    assert B.num(-0.0001) == "0.00"                     # no "minus zero"
    assert B.num(0.029, 2, pct=True, sign=True) == "+2.90%"
    assert B.num(float("nan")) == "–"


def test_inr_uses_indian_grouping():
    assert B.inr(1000000) == "₹10,00,000"
    assert B.inr(1234567) == "₹12,34,567"
    assert B.inr(999) == "₹999"
    assert B.inr(-125000) == "−₹1,25,000"
    assert B.inr(12345678.5, 2) == "₹1,23,45,678.50"


def test_palette_matches_brand_tokens():
    assert B.NEELA[900] == "#101F58" and B.KAGAZ[100] == "#F4F1E9" and B.SYAHI[100] == "#101B29"
    assert B.KESARI[100] == "#D07018" and B.GAIN == "#1F7A4D" and B.LOSS == "#B3261E"
    assert len(B.RIBBON_FULL) == len(B.RIBBON_LIFTED) == 18
    assert B.RIBBON_FULL[0] == "#101F58" and B.RIBBON_FULL[-1] == B.RIBBON_LIFTED[-1] == "#A8CCF0"
    assert B.RIBBON_LIFTED[0] == B.NEELA[600]


# ----------------------------------------------------------------------------- logo
@needs_assets
def test_logo_colourways_recolour_only_never_reshape():
    strip = lambda s: re.sub(r'(stroke|fill)="#[0-9A-Fa-f]{6}"', "", s)  # noqa: E731
    master = B.logo_svg("full-colour-light")
    for cw in B.COLOURWAYS:
        svg = B.logo_svg(cw)
        assert strip(svg) == strip(master), f"{cw}: geometry changed"
    navy = B.logo_svg("full-colour")
    assert [s.upper() for s in re.findall(r'stroke="(#[0-9A-Fa-f]{6})"', navy)] == B.RIBBON_LIFTED
    assert f'fill="{B.SAFED}"' in navy and f'fill="{B.WORDMARK_INK}"' not in navy
    assert B.KESARI[100] not in navy.upper() and B.KESARI[100] not in master.upper()   # never Kesari in the logo
    with pytest.raises(ValueError):
        B.logo_svg("rainbow")


@needs_assets
def test_logo_drawing_scales_to_width_and_keeps_aspect():
    d = B.logo_drawing("full-colour", 120.0)
    assert abs(d.width - 120.0) < 1e-6
    assert 0.95 < d.height / d.width < 1.05             # the primary lockup is square
    assert B.min_logo_width_pt() == pytest.approx(62.36, abs=0.01)


def test_check_assets_reports_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "FONT_DIR", str(tmp_path / "fonts"))
    monkeypatch.setattr(B, "LOGO_SVG", str(tmp_path / "logo" / "x.svg"))
    with pytest.raises(FileNotFoundError, match="brand assets missing"):
        B.check_assets()


# ----------------------------------------------------------------------------- the document
@pytest.fixture(scope="session")
def built(tmp_path_factory):
    import datetime as dt
    import make_pdf
    out = tmp_path_factory.mktemp("pdf") / "note.pdf"
    info = make_pdf.build(str(out), dt.date(2026, 10, 10))
    import pypdf
    reader = pypdf.PdfReader(str(out))
    texts = [pg.extract_text() or "" for pg in reader.pages]
    return dict(info=info, reader=reader, texts=texts, full="\n".join(texts), out=out, mod=make_pdf)


@needs_build
def test_pdf_builds_with_metadata_and_outline(built):
    r = built["reader"]
    assert 30 <= len(r.pages) <= 60
    meta = r.metadata
    assert meta["/Title"].startswith("RCG-RN-REV-001")
    assert "RAAS Capital" in meta["/Author"]
    outline = [o.title for o in r.outline if not isinstance(o, list)]
    for part in ["Part 1", "Part 4", "Part 8", "Appendix A"]:
        assert any(t.startswith(part) for t in outline), part


@needs_build
def test_pdf_embeds_only_brand_typefaces(built):
    fonts = set()
    for pg in built["reader"].pages:
        for _, f in pg.get("/Resources", {}).get("/Font", {}).items():
            fonts.add(re.sub(r"^/[A-Z]{6}\+", "", str(f.get_object()["/BaseFont"])))
    assert fonts, "no fonts found"
    assert all(f.startswith(("Outfit-", "IBMPlexSans-", "IBMPlexMono-")) for f in fonts), fonts


@needs_build
def test_kesari_appears_at_most_once_per_page(built):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.rl_accel import fp_str
    ops = [fp_str(*HexColor(h).rgb()) + " rg" for h in (B.KESARI[100], B.KESARI[60])]   # as reportlab writes them
    seen = 0
    for i, pg in enumerate(built["reader"].pages, 1):
        c = pg.get_contents().get_data().decode("latin-1")
        n = sum(c.count(op) for op in ops)
        assert n <= 1, f"page {i}: Kesari used {n} times"
        seen += n
    assert seen >= 8                                       # cover + every section opener carries the accent


@needs_build
def test_headline_numbers_match_the_reports(built):
    v = json.load(open(os.path.join(NSE, "reports", "validation.json")))["metrics"]
    lin = json.load(open(os.path.join(NSE, "reports", "lineage.json")))
    full = built["full"]
    for x in [v["sharpe_is"], v["sharpe_oos"], v["sharpe_full"], lin[-1]["sharpe_pre"], lin[-1]["bench_sharpe_pre"]]:
        assert B.num(x) in full, B.num(x)
    for o in lin:                                        # every kept strategy appears in the lineage table
        assert o["commit"] in full


@needs_build
def test_full_strategy_source_and_parameter_register_are_included(built):
    import strategy
    full = built["full"]
    for name in strategy.PARAMS:
        assert name in full, name
    squash = lambda s: re.sub(r"\s+", "", s)               # noqa: E731
    furniture = ["RAAS Capital  ·  Quant Desk", "RCG-RN-REV-001  ·  RSI + EMA + VWAP on the Nifty 50",
                 "Internal research · hypothetical backtest results · not investment advice"]
    pages = []
    for t in built["texts"]:                                  # drop running heads, page and line numbers,
        for f in furniture:                                   # and the wrap marker: what is left is the code
            t = t.replace(f, "")
        pages.append("\n".join(ln for ln in t.splitlines() if not ln.strip().isdigit()).replace("↪", ""))
    text = squash("\n".join(pages))
    for fname in ["strategy.py", "indicators.py", "signals.py"]:      # Appendices A, B, D: complete listings
        lines = [ln for ln in open(os.path.join(NSE, fname)).read().splitlines() if ln.strip()]
        missing = [ln for ln in lines if squash(ln) not in text]
        assert not missing, f"{fname}: {len(missing)} of {len(lines)} lines missing, e.g. {missing[:3]}"
    for fn in ["apply_harness_rules", "run_backtest", "sharpe", "max_drawdown", "cagr"]:   # Appendix C excerpts
        assert squash(f"def {fn}(") in text


@needs_build
def test_worked_example_explains_every_exit(built):
    tr = built["info"]["trace"]
    assert tr["unexplained"] == []
    assert tr["replica_ok"]                                   # the three weight terms reproduce the strategy
    assert tr["events"][-1][2].startswith("Regime off")
    assert abs(sum(f["w"] for f in tr["formation"]) - tr["gross0"]) < 1e-9
    assert any("re-rank" in e[2] for e in tr["events"])        # the day-3 re-rank is replicated, not assumed


@needs_build
def test_code_block_wraps_and_splits_without_losing_text(built):
    mk = built["mod"]
    src = "x = 1\n" + "y = " + " + ".join(f"value_{i}" for i in range(60)) + "\n"
    cb = mk.CodeBlock.from_source(src, 300)
    text = "".join(t for _, frags in cb.vlines for t, _, _ in frags).replace(" ", "")
    assert text == src.replace("\n", "").replace(" ", "")
    assert sum(1 for no, _ in cb.vlines if no is not None) == 2

    long = mk.CodeBlock.from_source("".join(f"a_{i} = {i}\n" for i in range(30)), 300)
    fits = lambda k: long.PAD * 2 + long.LEAD * k + 1e-6     # noqa: E731  height that holds k lines
    head, tail = long.split(300, fits(12))                     # an ordinary split: nothing lost or repeated
    assert (len(head.vlines), len(tail.vlines)) == (12, 18)
    assert head.vlines + tail.vlines == long.vlines
    assert long.split(300, fits(4)) == []                      # never open a listing with a stub: move it whole
    head, tail = long.split(300, fits(27))                     # never strand a tail of fewer than six lines
    assert (len(head.vlines), len(tail.vlines)) == (24, 6)
    assert long.split(300, fits(30)) == [long]                 # fits whole: no split


@needs_build
def test_mono_falls_back_to_sans_for_greek(built):
    mk = built["mod"]
    assert mk.para("plain ascii", "caption").style.name == "caption"
    assert mk.para("σ and β", "caption").style.name == "note"
