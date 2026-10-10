"""Tests for the protocol-v4 research note (make_pdf_v4.py, RCG-RN-REV-002).

The layout helpers are tested on synthetic stories. The document itself is built once per session from the
repository's own reports (reports/v4/validation.json, lineage.json, deep_holdout.json) and strategy.py; those
tests are skipped when the brand assets (not in the public repository) or the reports are absent."""
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NSE = os.path.dirname(HERE)
sys.path.insert(0, NSE)
import raas_brand as B  # noqa: E402

R4 = os.path.join(NSE, "reports", "v4")
HAVE_ASSETS = all(os.path.exists(os.path.join(B.FONT_DIR, f)) for f in B.FONT_FILES.values()) \
    and os.path.exists(B.LOGO_SVG)
HAVE_REPORTS = all(os.path.exists(os.path.join(R4, f)) for f in
                   ["validation.json", "lineage.json", "deep_holdout.json", "equity_curves.csv"]) \
    and os.path.exists(os.path.join(NSE, "reports", "diagnostics.json"))
needs_assets = pytest.mark.skipif(not HAVE_ASSETS, reason="RAAS brand assets not present (nse/brand/)")
needs_build = pytest.mark.skipif(not (HAVE_ASSETS and HAVE_REPORTS), reason="brand assets or v4 reports missing")


# ----------------------------------------------------------------------------- layout helpers
def test_keep_flattens_nested_keeptogether():
    import make_pdf_v4 as N
    from reportlab.platypus import KeepTogether, Spacer
    a, b, c = Spacer(1, 1), Spacer(1, 2), Spacer(1, 3)
    k = N.keep(a, KeepTogether([b, c]))
    assert isinstance(k, KeepTogether)
    assert list(k._content) == [a, b, c]                      # no KeepTogether inside a KeepTogether
    assert not any(isinstance(x, KeepTogether) for x in k._content)


@needs_assets
def test_glue_binds_headings_and_openers_to_their_first_block():
    import make_pdf as M
    import make_pdf_v4 as N
    from reportlab.platypus import KeepTogether, Spacer
    B.register_reportlab_fonts()
    M.ST = M.styles()
    t1, t2, body, code = KeepTogether([Spacer(1, 5)]), KeepTogether([Spacer(1, 6)]), M.P("text"), Spacer(1, 7)
    h = M.h2("A section")
    op = N.opener("Part 9", "A part", "Its standfirst.")
    story = op + [t1, M.P("between"), h, t2, body]
    out = N.glue_headings(story)
    assert isinstance(out[0], KeepTogether)                   # opener (rule, label, title, standfirst) + its block
    assert list(out[0]._content) == op + list(t1._content)
    assert isinstance(out[2], KeepTogether) and list(out[2]._content) == [h] + list(t2._content)
    assert out[-1] is body
    # an opener whose first block is a code listing is left alone (the listing splits across pages)
    cb = M.CodeBlock.from_source("x = 1\n", 300)
    op2 = N.opener("Appendix Z", "Code")
    out2 = N.glue_headings(op2 + [cb, code])
    assert out2[:len(op2)] == op2 and out2[len(op2)] is cb


def test_parameter_register_documents_every_numeric_parameter():
    import make_pdf_v4 as N
    doc = {n for _, items in N.PARAM_DOC for n, _ in items}
    strategy = N.load_strategy(os.path.join(NSE, "strategy.py"))
    numeric = {k for k in strategy.PARAMS if not k.startswith("use_")}
    if doc != numeric:
        pytest.skip("strategy.py is not the v2 strategy in this checkout")
    assert all(desc and len(desc) > 15 for _, items in N.PARAM_DOC for _, desc in items)


# ----------------------------------------------------------------------------- the document
@pytest.fixture(scope="session")
def built(tmp_path_factory):
    import datetime as dt
    import make_pdf_v4 as N
    out = tmp_path_factory.mktemp("pdf4") / "note_v2.pdf"
    info = N.build(str(out), dt.date(2026, 10, 10))
    import pypdf
    reader = pypdf.PdfReader(str(out))
    texts = [pg.extract_text() or "" for pg in reader.pages]
    return dict(info=info, reader=reader, texts=texts, full="\n".join(texts), out=out, mod=N)


@needs_build
def test_pdf_builds_with_metadata_and_outline(built):
    r = built["reader"]
    assert 20 <= len(r.pages) <= 45
    meta = r.metadata
    assert meta["/Title"].startswith("RCG-RN-REV-002")
    assert "RAAS Capital" in meta["/Author"]
    outline = [o.title for o in r.outline if not isinstance(o, list)]
    for part in ["Executive summary", "Part 1", "Part 4", "Part 7", "Part 8", "Appendix A", "Appendix B", "Appendix C"]:
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
    ops = [fp_str(*HexColor(h).rgb()) + " rg" for h in (B.KESARI[100], B.KESARI[60])]
    seen = 0
    for i, pg in enumerate(built["reader"].pages, 1):
        c = pg.get_contents().get_data().decode("latin-1")
        n = sum(c.count(op) for op in ops)
        assert n <= 1, f"page {i}: Kesari used {n} times"
        seen += n
    assert seen >= 12                     # contents, summary, eight parts and three appendices open with it


@needs_build
def test_headline_numbers_match_the_reports(built):
    v = json.load(open(os.path.join(R4, "validation.json")))
    m, nifty = v["metrics"], v["benchmarks"]["nifty50"]
    lin = {r["file"]: r for r in json.load(open(os.path.join(R4, "lineage.json")))}
    deep = json.load(open(os.path.join(R4, "deep_holdout.json")))
    dfin = next(r for r in deep["rows"] if "final" in r["roles"])
    full = built["full"]
    for x in [m["sharpe_pre"], m["sharpe_is"], m["sharpe_oos"], m["sharpe_full"], m["objective"], nifty["oos"],
              nifty["full"], dfin["sharpe_deep"], dfin["bench_sharpe_deep"]]:
        assert B.num(x) in full, B.num(x)
    v1 = lin["v4/v1_final.py"]
    for x in [v1["sharpe_pre"], v1["sharpe_oos"], v1["sharpe_full"], lin["v4/r0_ema_timer.py"]["objective"]]:
        assert B.num(x) in full, B.num(x)
    for r in deep["rows"]:                                     # every registered deep look is reported
        assert r["file"] in full.replace("\n", ""), r["file"]
    n_rows = sum(1 for _ in open(os.path.join(NSE, "results_v4.tsv"))) - 1
    assert f"{n_rows:,}" in full                               # the log size quoted is the log's


@needs_build
def test_full_strategy_source_is_included(built):
    squash = lambda s: re.sub(r"\s+", "", s)                  # noqa: E731
    furniture = ["RAAS Capital  ·  Quant Desk", "RCG-RN-REV-002  ·  RSI + EMA + VWAP on the Nifty 50, v2",
                 "Internal research · hypothetical backtest results · not investment advice"]
    pages = []
    for t in built["texts"]:
        for f in furniture:
            t = t.replace(f, "")
        pages.append("\n".join(ln for ln in t.splitlines() if not ln.strip().isdigit()).replace("↪", ""))
    text = squash("\n".join(pages))
    lines = [ln for ln in open(os.path.join(NSE, "strategy.py")).read().splitlines() if ln.strip()]
    missing = [ln for ln in lines if squash(ln) not in text]
    assert not missing, f"strategy.py: {len(missing)} of {len(lines)} lines missing, e.g. {missing[:3]}"
    for fn in ["v4_objective", "evaluate_v4", "gates", "compliance", "neighbours", "plateau"]:   # Appendix B
        assert squash(f"def {fn}(") in text, fn


@needs_build
def test_every_trade_is_explained_by_a_rule(built):
    info = built["info"]
    tl = info["trades"]
    assert info["unexplained"] == []
    assert len(tl) > 100
    assert set(tl["reason"]) <= {"VWAP reclaim", "time stop", "left the index", "open at the end of the data"}
    assert (tl["sessions"].dropna() >= 1).all()
    assert tl["window"].isin(["pre", "is", "oos"]).all()
    assert str(info["example_day"].date()) >= "2024-01-01"     # the traced trigger day is in 2024-2026


@needs_build
def test_no_placeholder_text(built):
    texts = built["texts"]
    first_code = next(i for i, t in enumerate(texts) if "complete and unedited" in t)
    prose = "\n".join(texts[:first_code])                    # the code appendices legitimately hold None
    for bad in ["see program.md", "TODO", "TBD", "DEV_PLACEHOLDER", "nan", "None", "inf"]:
        assert re.search(rf"\b{re.escape(bad)}\b", prose) is None, bad
