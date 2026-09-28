"""screener.py: the readings, their ranking, the universe, the CSV source and the CLI. Offline throughout."""
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import engine
import screener
from conftest import ist, make_bars

BASE = [100 + (i % 5) for i in range(150)]          # closes 100..104 -> base high 104.5 (test_alpha's base)
SESSION_BARS = 75                                   # 09:15 .. 15:25


def session_bars(closes_by_day, volumes_by_day=None, first_day=1):
    """Contiguous 5-minute sessions on consecutive September 2026 days, 09:15 onwards (at most 75 bars a day)."""
    frames = []
    for i, closes in enumerate(closes_by_day):
        assert len(closes) <= SESSION_BARS
        volumes = None if volumes_by_day is None else volumes_by_day[i]
        frames.append(make_bars(closes, volumes, start=ist(2026, 9, first_day + i, 9, 15)))
    df = pd.concat(frames)
    # make_bars opens each frame at its own first close; open the later sessions at the previous close instead.
    for k in range(1, len(frames)):
        row = sum(len(f) for f in frames[:k])
        df.iloc[row, df.columns.get_loc("Open")] = df["Close"].iloc[row - 1]
    return df


def breakout_frame(breakout_close=106.0, breakout_volume=500_000, pre=20, after=()):
    """test_alpha's frame: the base, 20 quiet bars, one breakout bar, then ``after`` (close, volume) bars."""
    closes = BASE + [102.0] * pre + [breakout_close] + [c for c, _ in after]
    volumes = [100_000] * (len(BASE) + pre) + [breakout_volume] + [v for _, v in after]
    return make_bars(closes, volumes)


def write_bars(path: Path, df: pd.DataFrame) -> Path:
    out = df.rename(columns=str.lower)
    out.insert(0, "datetime_ist", [f"{t:%Y-%m-%d %H:%M:%S}" for t in df.index])
    out.to_csv(path, index=False)
    return path


def cli(*args, timeout=90):
    return subprocess.run([sys.executable, screener.__file__, *args], capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------------------------------- the readings
def test_a_breakout_on_the_last_bar_carries_the_engines_stop_and_target():
    df = breakout_frame()
    alpha = engine.AlphaEngine()
    row = screener.classify("IPO", df, alpha)
    sig = alpha.evaluate("IPO", df)
    assert row.status == "BREAKOUT"
    assert row.stop == sig.stop_loss and row.target == sig.target
    assert row.note == f"stop {sig.stop_loss:.2f} target {sig.target:.2f}"
    assert row.last_bar == df.index[-1] and row.last_breakout == df.index[-1]
    assert row.breakouts == 1 and row.bars == 171 and row.sessions == 1
    assert row.close == 106.0 and row.base_high == 104.5
    assert row.gap_pct == pytest.approx((106.0 / 104.5 - 1) * 100)
    assert row.rvol == pytest.approx(5.0)


def test_a_recent_breakout_still_above_the_base_is_holding():
    df = breakout_frame(after=[(106.5, 100_000), (107.0, 100_000)])
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "HOLDING"
    assert row.last_breakout == df.index[-3] and row.breakouts == 1
    assert row.stop is None and row.target is None
    assert "still above the base" in row.note and "breakout 2026-09-01 23:25" in row.note


def test_a_recent_breakout_that_gave_back_the_base_is_failed():
    df = breakout_frame(after=[(104.5, 100_000), (103.0, 100_000)])       # at the base high, then below it
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "FAILED"
    assert row.gap_pct < 0 and "back at or below the base" in row.note


def test_the_recent_window_counts_sessions_not_bars():
    # The breakout is the last bar of day 3 (the base spans days 1-2); the close then holds above the base.
    day1 = BASE[:SESSION_BARS]
    day2 = BASE[SESSION_BARS:] + [102.0] * (SESSION_BARS - len(BASE[SESSION_BARS:]))
    day3 = [102.0] * (SESSION_BARS - 1) + [106.0]
    quiet = [106.5] * 10
    vols = [[100_000] * SESSION_BARS, [100_000] * SESSION_BARS, [100_000] * (SESSION_BARS - 1) + [500_000]] + [[100_000] * 10] * 3
    df = session_bars([day1, day2, day3, quiet, quiet, quiet], vols)
    assert screener.classify("IPO", df, engine.AlphaEngine()).breakouts == 1
    # Days 4, 5 and 6 are the last three sessions: the day-3 breakout is outside a 3-session window ...
    assert screener.classify("IPO", df, engine.AlphaEngine(), recent_sessions=3).status == "ABOVE"
    # ... and inside a 4-session one.
    assert screener.classify("IPO", df, engine.AlphaEngine(), recent_sessions=4).status == "HOLDING"


def test_a_close_near_the_base_and_above_the_avwap_is_a_setup():
    df = make_bars(BASE + [102.0] * 19 + [103.0])                          # 1.44% below the base high
    ind = engine.AlphaEngine().indicators(df).iloc[-1]
    assert ind["Close"] > ind["AVWAP"]                                     # precondition
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "SETUP" and row.gap_pct == pytest.approx((103.0 / 104.5 - 1) * 100)
    assert "1.44% below the base, above the AVWAP" == row.note
    assert screener.classify("IPO", df, engine.AlphaEngine(), near_pct=1.0).status == "BELOW"
    assert screener.classify("IPO", df, engine.AlphaEngine(), near_pct=1.43).status == "BELOW"     # 1.4354% below
    assert screener.classify("IPO", df, engine.AlphaEngine(), near_pct=1.44).status == "SETUP"


def test_a_close_at_the_base_high_is_a_setup_not_above():
    df = make_bars(BASE + [102.0] * 19 + [104.5])
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "SETUP" and row.gap_pct == 0


def test_a_close_below_the_avwap_is_below_even_when_near_the_base():
    df = make_bars(BASE + [102.0] * 19 + [101.6])                          # 2.78% below the base
    ind = engine.AlphaEngine().indicators(df).iloc[-1]
    assert ind["Close"] < ind["AVWAP"]                                     # precondition
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "BELOW"
    assert row.note == f"2.78% below the base, at or below the AVWAP {ind['AVWAP']:.2f}"


def test_a_close_far_below_the_base_is_below():
    df = make_bars(BASE + [102.0] * 19 + [95.0])
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "BELOW" and row.note == "9.09% below the base, at or below the AVWAP " + f"{row.avwap:.2f}"


def test_above_the_base_on_thin_volume_is_above_not_a_breakout():
    df = make_bars(BASE + [102.0] * 19 + [106.0, 107.0])                   # both crossings at 1x volume
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "ABOVE" and row.breakouts == 0 and row.last_breakout is None
    assert "no signal in the window" in row.note


def test_an_incomplete_base_is_reported_with_what_it_needs():
    alpha = engine.AlphaEngine()
    for n in (1, 100, 150):                                                # evaluation needs more than 150 bars
        row = screener.classify("IPO", make_bars([100.0] * n), alpha)
        assert row.status == "BASE" and row.bars == n
        assert row.note == (f"base incomplete: {n} bars, evaluation starts after 150 (the base is 150 bars, "
                            "the volume baseline 20 bars)")
        assert row.close == 100.0 and row.base_high is None and row.gap_pct is None
    assert screener.classify("IPO", make_bars([100.0] * 151), alpha).status == "BELOW"


def test_a_session_defined_base_reports_its_sessions():
    alpha = engine.AlphaEngine(base_sessions=2)
    df = session_bars([[100.0] * 10, [101.0] * 10])                         # the base is every bar so far
    row = screener.classify("IPO", df, alpha)
    assert row.status == "BASE" and row.sessions == 2 and row.bars == 20
    assert "evaluation starts after 20 (the base is the first 2 sessions, the volume baseline 20 bars)" in row.note
    df = session_bars([[100.0] * 10, [101.0] * 10, [102.0] * 25])
    assert screener.classify("IPO", df, alpha).status == "ABOVE"          # 101.5 base high; 102 above, 1x volume


def test_an_empty_frame_is_no_data():
    row = screener.classify("IPO", engine.empty_bars(), engine.AlphaEngine())
    assert row.status == "NO_DATA" and row.note == "no bars" and row.last_bar is None


def test_a_bar_with_no_volume_baseline_has_no_rvol():
    df = make_bars(BASE + [102.0] * 19 + [103.0], [0.0] * 170)             # every bar a vendor gap
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.rvol is None and row.status == "BELOW"                      # no AVWAP either: SETUP needs one
    assert row.avwap is None


def test_the_bundled_swiggy_data_reads_as_the_failed_real_breakout(real_bars):
    row = screener.classify("SWIGGY", real_bars, engine.AlphaEngine())
    assert row.status == "FAILED"
    assert row.last_breakout == ist(2026, 9, 23, 9, 15) and row.breakouts == 1     # README, "Validation on real data"
    assert row.base_high == 285.55 and row.close == 264.85 and row.sessions == 13 and row.bars == 944
    assert row.gap_pct == pytest.approx(-7.25, abs=0.005)
    assert row.avwap == pytest.approx(277.04, abs=0.005)
    assert row.rvol == 0.0                                                  # the vendor's zero-volume last bar
    # Outside a window that reaches 2026-09-23 the same data is BELOW (a 2-session window is 24 and 25 September).
    assert screener.classify("SWIGGY", real_bars, engine.AlphaEngine(), recent_sessions=2).status == "BELOW"


# --------------------------------------------------------------------------------------------------- ranking
def test_rank_puts_the_best_status_first_then_the_closest_to_the_base():
    rows = [screener.Row("D", "BELOW", gap_pct=-9.0), screener.Row("C", "SETUP", gap_pct=-2.0),
            screener.Row("B", "SETUP", gap_pct=-1.0), screener.Row("Z", "NO_DATA"), screener.Row("A", "BASE"),
            screener.Row("E", "BREAKOUT", gap_pct=1.0), screener.Row("F", "HOLDING", gap_pct=4.0),
            screener.Row("G", "ABOVE", gap_pct=0.5), screener.Row("H", "FAILED", gap_pct=-0.5),
            screener.Row("Y", "NO_DATA")]
    assert [r.symbol for r in screener.rank(rows)] == ["E", "F", "B", "C", "G", "H", "D", "A", "Y", "Z"]


def test_rank_breaks_gap_ties_by_symbol_and_puts_unread_gaps_last():
    rows = [screener.Row("B", "SETUP", gap_pct=-1.0), screener.Row("A", "SETUP", gap_pct=-1.0),
            screener.Row("C", "SETUP", gap_pct=None)]
    assert [r.symbol for r in screener.rank(rows)] == ["A", "B", "C"]


# --------------------------------------------------------------------------------------------------- the universe
def test_universe_lines_in_every_accepted_form():
    lines = ["symbol,listing_date", "", "# recent listings", "swiggy,2024-11-13", "ATHERENERG=2025-05-06",
             "AEGISVOPAK  # no date: demo semantics", "hyundai,2024-10-22,Hyundai Motor India,extra columns"]
    out = screener.parse_universe(lines)
    assert list(out) == ["SWIGGY", "ATHERENERG", "AEGISVOPAK", "HYUNDAI"]
    assert out["SWIGGY"] == ist(2024, 11, 13) and out["ATHERENERG"] == ist(2025, 5, 6)
    assert out["AEGISVOPAK"] is None and out["HYUNDAI"] == ist(2024, 10, 22)


def test_universe_errors_name_the_line():
    with pytest.raises(ValueError, match="line 2: SWIGGY is listed twice"):
        screener.parse_universe(["SWIGGY", "swiggy=2024-11-13"])
    with pytest.raises(ValueError, match="line 1: listing date '13/11/2024' is not YYYY-MM-DD"):
        screener.parse_universe(["SWIGGY,13/11/2024"])
    with pytest.raises(ValueError, match="line 1: 'SWIG GY' is not SYMBOL"):
        screener.parse_universe(["SWIG GY"])
    with pytest.raises(ValueError, match="line 1: '=2024-11-13' is not SYMBOL"):
        screener.parse_universe(["=2024-11-13"])
    assert screener.parse_universe([]) == {}


def test_the_header_is_skipped_only_at_the_top_and_only_in_files():
    assert list(screener.parse_universe(["symbol", "A"])) == ["A"]
    assert list(screener.parse_universe(["A", "symbol"])) == ["A", "SYMBOL"]
    assert list(screener.parse_universe(["symbol", "A"], header=False)) == ["SYMBOL", "A"]


def test_csv_files_pick_the_newest_file_of_a_symbol_and_flag_missing_ones(tmp_path, caplog):
    for name in ("abc_5m_2026-01-01_2026-02-01.csv", "ABC_5m_2026-03-01_2026-04-01.csv", "XYZ.csv", "ABCD_5m.csv"):
        (tmp_path / name).write_text("datetime_ist,open,high,low,close,volume\n")
    files = screener.csv_files(tmp_path, ["ABC", "XYZ", "NOPE"])
    assert files["ABC"].name == "ABC_5m_2026-03-01_2026-04-01.csv"       # the newest by name, whatever the case
    assert files["XYZ"].name == "XYZ.csv"
    assert files["NOPE"] == tmp_path / "NOPE_5m.csv" and not files["NOPE"].exists()
    assert "[ABC] 2 files in" in caplog.text
    assert screener.csv_files(tmp_path / "missing", ["ABC"]) == {"ABC": tmp_path / "missing" / "ABC_5m.csv"}


def test_csv_as_of_is_a_bar_after_the_newest_bar_of_any_readable_file(tmp_path, caplog):
    a = write_bars(tmp_path / "A_5m.csv", make_bars([1.0, 2.0], start=ist(2026, 9, 1, 9, 15)))
    b = write_bars(tmp_path / "B_5m.csv", make_bars([1.0] * 3, start=ist(2026, 9, 3, 9, 15)))
    (tmp_path / "C_5m.csv").write_text("not,a,bar,file\n1,2,3,4\n")
    files = {"A": a, "B": b, "C": tmp_path / "C_5m.csv", "D": tmp_path / "D_5m.csv"}
    assert screener.csv_as_of(files) == ist(2026, 9, 3, 9, 30)
    assert "Cannot read bars from" in caplog.text
    now = screener.csv_as_of({"D": tmp_path / "D_5m.csv"})
    assert now.tzinfo is not None and abs((now - engine.datetime.now(engine.timezone.utc)).total_seconds()) < 5


# --------------------------------------------------------------------------------------------------- screen()
def test_screen_reads_every_symbol_and_names_the_root_cause_of_an_unread_one(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    write_bars(tmp_path / "BRK_5m.csv", breakout_frame())
    write_bars(tmp_path / "SET_5m.csv", make_bars(BASE + [102.0] * 19 + [103.0]))
    (tmp_path / "BAD_5m.csv").write_text("datetime_ist,open,high,low,close,volume\ngarbage\n")
    files = screener.csv_files(tmp_path, ["SET", "BRK", "GONE", "BAD"])
    watchlist = {"SET": None, "BRK": None, "GONE": None, "BAD": None}
    rows = asyncio.run(screener.screen(watchlist, engine.CsvReplayAdapter(files), engine.AlphaEngine(),
                                       as_of=screener.csv_as_of(files)))
    assert [(r.symbol, r.status) for r in rows] == [("BRK", "BREAKOUT"), ("SET", "SETUP"), ("BAD", "NO_DATA"),
                                                    ("GONE", "NO_DATA")]
    gone = rows[-1]
    assert gone.note.startswith("No CSV file for symbol (looked for ") and gone.note.endswith("GONE_5m.csv).")
    assert rows[-2].note == "History fetch failed; symbol excluded."
    assert "ALPHA TRIGGER" in caplog.text
    assert not [h for h in engine.logger.handlers if isinstance(h, screener._Reasons)]


def test_screen_reports_a_history_that_does_not_reach_the_listing(tmp_path):
    files = {"LATE": write_bars(tmp_path / "LATE_5m.csv", breakout_frame())}     # bars start 2026-09-01
    adapter = engine.CsvReplayAdapter(files)
    rows = asyncio.run(screener.screen({"LATE": ist(2026, 8, 20)}, adapter, engine.AlphaEngine(),
                                       as_of=screener.csv_as_of(files)))
    assert rows[0].status == "NO_DATA"
    assert "12 days after the 2026-08-20 listing" in rows[0].note and "--allow-partial-history" in rows[0].note
    rows = asyncio.run(screener.screen({"LATE": ist(2026, 8, 20)}, adapter, engine.AlphaEngine(),
                                       as_of=screener.csv_as_of(files), allow_partial_history=True))
    assert rows[0].status == "BREAKOUT"


def test_screen_with_a_broker_that_cannot_start_reports_it_on_every_row():
    class Broken(engine.BrokerAdapter):
        async def boot(self):
            raise RuntimeError("no session")

        async def fetch_historical_bars(self, symbol, start_date, end_date, interval):
            raise AssertionError("never reached")

    rows = asyncio.run(screener.screen({"A": None, "B": None}, Broken(), engine.AlphaEngine()))
    assert [(r.symbol, r.status, r.note) for r in rows] == [("A", "NO_DATA", "Broker start-up failed: RuntimeError('no session')"),
                                                            ("B", "NO_DATA", "Broker start-up failed: RuntimeError('no session')")]


# --------------------------------------------------------------------------------------------------- reports
def test_the_table_aligns_columns_and_renders_missing_values_as_dashes():
    rows = [screener.Row("ABC", "BREAKOUT", note="stop 1.00 target 2.00", last_bar=ist(2026, 9, 1, 9, 15), close=106.0,
                         base_high=104.5, gap_pct=1.4354, avwap=102.1, rvol=5.0, atr=1.2, sessions=1, bars=171,
                         breakouts=1, last_breakout=ist(2026, 9, 1, 9, 15), stop=1.0, target=2.0),
            screener.Row("NOPE", "NO_DATA", note="no history")]
    text = screener.render_table(rows)
    lines = text.splitlines()
    assert lines[0].split() == ["Symbol", "Status", "Last", "bar", "Close", "Base", "high", "Gap%", "AVWAP", "RVOL",
                                "Sessions", "Signals", "Last", "signal", "Note"]
    assert set(lines[1]) == {"-", " "}
    assert lines[2].split() == ["ABC", "BREAKOUT", "2026-09-01", "09:15", "106.00", "104.50", "+1.44", "102.10", "5.00",
                                "1", "1", "2026-09-01", "09:15", "stop", "1.00", "target", "2.00"]
    assert lines[3].split() == ["NOPE", "NO_DATA", "-", "-", "-", "-", "-", "-", "0", "0", "-", "no", "history"]
    end = lines[0].index("Close") + len("Close")                          # numbers are right-aligned under their header
    assert lines[2][end - 6:end] == "106.00" and lines[3][end - 1] == "-" and lines[3][end - 2] == " "
    start = lines[0].index("Note")                                         # text is left-aligned
    assert lines[2][start:].startswith("stop 1.00") and lines[3][start:] == "no history"
    assert screener.render_table([]).splitlines()[0].startswith("Symbol  Status")


def test_json_and_csv_reports_carry_every_field_with_iso_times(tmp_path):
    row = screener.classify("IPO", breakout_frame(), engine.AlphaEngine())
    doc = screener.report([row], ist(2026, 9, 2, 0, 0), "csv", {"near_pct": 3.0})
    assert doc["as_of"] == "2026-09-02T00:00:00+05:30" and doc["source"] == "csv" and doc["statuses"][0] == "BREAKOUT"
    assert doc["rows"][0]["last_bar"] == "2026-09-01T23:25:00+05:30" and doc["rows"][0]["status"] == "BREAKOUT"
    json.dumps(doc)                                                        # every value is serialisable
    out = tmp_path / "rows.csv"
    with open(out, "w", encoding="utf-8", newline="") as stream:
        screener.write_csv([row, screener.Row("X", "NO_DATA", note="n")], stream)
    back = pd.read_csv(out)
    assert list(back.columns) == list(screener.Row.__dataclass_fields__)
    assert back.loc[0, "last_breakout"] == "2026-09-01T23:25:00+05:30" and back.loc[0, "stop"] == pytest.approx(row.stop)
    assert back.loc[1, "symbol"] == "X" and pd.isna(back.loc[1, "close"])


# --------------------------------------------------------------------------------------------------- the CLI
def test_cli_screens_a_directory_ranks_the_rows_and_writes_both_reports(tmp_path):
    write_bars(tmp_path / "BRK_5m_2026-09-01_2026-09-01.csv", breakout_frame())
    write_bars(tmp_path / "SET_5m.csv", make_bars(BASE + [102.0] * 19 + [103.0]))
    write_bars(tmp_path / "YNG_5m.csv", make_bars([50.0] * 30))
    universe = tmp_path / "ipos.txt"
    universe.write_text("symbol,listing_date\nset,2026-09-01\n# nothing on disk for this one:\nGONE\n")
    proc = cli("--source", "csv", "--csv-dir", str(tmp_path), "--universe", str(universe), "BRK=2026-09-01", "YNG",
               "--json", str(tmp_path / "out.json"), "--csv-out", str(tmp_path / "out.csv"))
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("Symbol  Status")
    assert [line.split()[:2] for line in lines[2:]] == [["BRK", "BREAKOUT"], ["SET", "SETUP"], ["YNG", "BASE"], ["GONE", "NO_DATA"]]
    doc = json.loads((tmp_path / "out.json").read_text())
    assert [r["symbol"] for r in doc["rows"]] == ["BRK", "SET", "YNG", "GONE"]
    assert doc["as_of"] == "2026-09-01T23:30:00+05:30"                     # a bar after the newest bar on disk
    assert doc["parameters"]["recent_sessions"] == 3 and doc["parameters"]["near_pct"] == 3.0
    assert (tmp_path / "out.csv").read_text().splitlines()[1].startswith("BRK,BREAKOUT,stop ")
    assert "3 of 4 symbol(s) read: 1 BREAKOUT, 1 SETUP, 1 BASE, 1 NO_DATA" in proc.stderr
    assert "Traceback" not in proc.stdout


def test_cli_exit_codes(tmp_path):
    assert cli().returncode == 2                                           # nothing to screen
    assert cli("SWIGGY=13-11-2024").returncode == 2                        # a bad date
    assert cli("SWIGGY", "--universe", str(tmp_path / "none.txt")).returncode == 2
    assert cli("SWIGGY", "--source", "csv", "--csv-dir", str(tmp_path / "none")).returncode == 2
    assert cli("SWIGGY", "--recent-sessions", "0").returncode == 2
    assert cli("SWIGGY", "--near-pct", "-1").returncode == 2
    assert cli("SWIGGY", "--base-sessions", "0").returncode == 2
    (tmp_path / "ipos.txt").write_text("SWIGGY\n")
    proc = cli("SWIGGY", "--universe", str(tmp_path / "ipos.txt"))
    assert proc.returncode == 2 and "SWIGGY is given both on the command line and in" in proc.stderr
    proc = cli("NOPE", "--source", "csv", "--csv-dir", str(tmp_path))     # read nothing at all
    assert proc.returncode == 1 and proc.stdout.splitlines()[2].split()[:2] == ["NOPE", "NO_DATA"]
    assert "0 of 1 symbol(s) read: 1 NO_DATA" in proc.stderr


def test_cli_reads_the_bundled_data_offline_by_default():
    proc = cli("--source", "csv", "SWIGGY")
    assert proc.returncode == 0, proc.stderr
    assert "SWIGGY  FAILED  2026-09-25 15:15  264.85     285.55  -7.25  277.04  0.00        13        1  2026-09-23 09:15" in proc.stdout
    assert "as of 2026-09-25 15:20 IST" in proc.stderr and "demo semantics" in proc.stderr


def test_cli_refuses_an_unwritable_report_after_printing_the_table(tmp_path):
    proc = cli("--source", "csv", "SWIGGY", "--json", str(tmp_path / "missing-dir" / "out.json"))
    assert proc.returncode == 1 and "SWIGGY  FAILED" in proc.stdout and "Cannot write the report" in proc.stderr


def test_kite_source_needs_credentials(monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    assert asyncio.run(screener.main(["SWIGGY=2024-11-13", "--source", "kite"])) == 2


def test_yahoo_source_uses_the_public_adapter_and_its_history_limit(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    calls = []

    async def fake_fetch(self, symbol, start_date, end_date, interval="5m"):
        calls.append((symbol, start_date, end_date))
        return breakout_frame() if symbol == "BRK" else engine.empty_bars()

    monkeypatch.setattr(engine.PublicExchangeAdapter, "fetch_historical_bars", fake_fetch)
    # A listing older than Yahoo's ~60 days of 5m bars is unread without --allow-partial-history: the adapter would
    # have clamped its start (calls[0]); with the flag the frame is anchored at its first bar.
    assert asyncio.run(screener.main(["BRK=2026-06-01", "NONE", "--source", "yahoo"])) == 1
    assert [c[0] for c in calls] == ["BRK", "NONE"] and calls[0][1] >= ist(2026, 6, 1)   # never before the listing
    assert "the IPO base and AVWAP anchor are unknown" in caplog.text
    calls.clear()
    assert asyncio.run(screener.main(["BRK=2026-06-01", "--source", "yahoo", "--allow-partial-history"])) == 0
    assert "ALPHA TRIGGER: Base Breakout @ 106.00" in caplog.text
    assert calls[0][2].tzinfo is not None                                  # as_of is an aware UTC time


def test_screener_module_exposes_no_order_path():
    """A screener reads; it never constructs a gateway or a live feed."""
    source = Path(screener.__file__).read_text()
    for forbidden in ("KiteOrderGateway", "PaperGateway", "ExecutionRouter", "start_kite_feed", "LiveTickAdapter",
                      "place_order", "simulate_live_market"):
        assert forbidden not in source


def test_a_stop_signal_ends_the_screener_with_the_conventional_code(tmp_path):
    """Ctrl-C during the run: 130, no traceback (the screener is a batch job; engine.py's signal routing is not used)."""
    code = f"""
import asyncio, os, signal, sys
sys.path.insert(0, {str(Path(screener.__file__).parent)!r})
import screener, engine

class Slow(engine.BrokerAdapter):
    async def fetch_historical_bars(self, symbol, start_date, end_date, interval):
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.sleep(5)
        return engine.empty_bars()

async def main(argv):
    rows = await screener.screen({{"X": None}}, Slow(), engine.AlphaEngine())
    return 0
screener.main = main
sys.exit(screener.run(["X"]))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 130, proc.stderr
    assert "Interrupted." in proc.stderr and "Traceback" not in proc.stderr


def test_every_status_is_ranked_and_documented():
    doc = Path(screener.__file__).read_text().split('"""')[1]
    for status in screener.STATUS_ORDER:
        assert f"    {status}" in doc
    assert len(set(screener.STATUS_ORDER)) == 8


def test_classify_never_looks_past_the_last_bar():
    """The reading on bar t uses bars <= t: truncating the future must not change it."""
    df = breakout_frame(after=[(106.5, 100_000)] * 3 + [(103.0, 100_000)] * 2 + [(120.0, 5_000_000)])
    full = screener.classify("IPO", df, engine.AlphaEngine())
    assert full.status == "BREAKOUT" and full.breakouts == 2               # a re-cross after a dip counts again
    cut = screener.classify("IPO", df.iloc[:-1], engine.AlphaEngine())
    assert cut.status == "FAILED" and cut.breakouts == 1 and cut.close == 103.0


def test_gap_and_note_use_the_bars_own_prices_not_rounded_ones():
    df = make_bars(BASE + [102.0] * 19 + [104.4999])
    row = screener.classify("IPO", df, engine.AlphaEngine())
    assert row.status == "SETUP" and row.gap_pct == pytest.approx((104.4999 / 104.5 - 1) * 100)
    assert row.note.startswith("0.00% below the base")


def test_session_bars_helper_builds_contiguous_sessions():
    df = session_bars([[1.0, 2.0], [3.0]])
    assert list(df.index) == [ist(2026, 9, 1, 9, 15), ist(2026, 9, 1, 9, 20), ist(2026, 9, 2, 9, 15)]
    assert df["Open"].iloc[2] == 2.0
