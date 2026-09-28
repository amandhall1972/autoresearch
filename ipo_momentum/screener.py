"""
====================================================================================
IPO MOMENTUM SCREENER - WHERE EACH RECENT LISTING STANDS AGAINST THE BREAKOUT RULE
====================================================================================
Reads the history of several IPOs exactly as engine.py does (the same adapters, listing anchoring and indicators),
judges the last closed bar of each with the same AlphaEngine, and prints one ranked row per symbol. It sends no
orders and opens no live feed: run engine.py on a symbol to trade it.

Quick start (see README.md, "Screening several IPOs"):
    python screener.py --source csv SWIGGY                          # bundled real bars, fully offline
    python screener.py NEWIPO=2026-09-08 OTHER=2026-09-15           # Yahoo history (5m bars reach back ~60 days)
    python screener.py --universe ipos.csv --source kite            # Zerodha history (KITE_API_KEY, KITE_ACCESS_TOKEN;
                                                                    #   --max-lookback-days N for a listing older than 180 days)

Statuses, best first:
    BREAKOUT  the rule fired on the last closed bar (the row carries the engine's stop and target)
    HOLDING   it fired within the last --recent-sessions sessions and the close is still above the base high
    SETUP     no recent signal; the close is within --near-pct below the base high and above the AVWAP
    ABOVE     the close is above the base high, but no signal in the window (crossed on thin volume or below the
              AVWAP, or before the window)
    FAILED    it fired within the window and the close is back at or below the base high
    BELOW     the base is complete and none of the above holds
    BASE      the IPO base is not complete, or fewer than 20 bars (the volume baseline) have traded since the listing
    NO_DATA   the history could not be read or does not reach the listing (the note says why)
Time is kept as the engine keeps it, per source: Yahoo history is read up to now; Kite history up to 30 s before
the host clock (Kite serves the running candle: the allowance is the engine's own for a host clock ahead of the
exchange, so keep the clock synced); a CSV file up to a bar after its newest bar, but never past the start of the
bar now forming. A symbol without a listing date is fetched from at most 20 days back (the engine's demo window).
Nothing here is investment advice.
====================================================================================
"""

import argparse
import asyncio
import csv
import io
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

import engine
from engine import (BAR_MINUTES, IST, AlphaEngine, BrokerAdapter, CsvReplayAdapter, ProductionOrchestrator,
                    PublicExchangeAdapter, ZerodhaKiteAdapter, _lookback_days, _positive, configure_logging)

logger = logging.getLogger("QUANT_ENGINE.screener")

STATUS_ORDER = ("BREAKOUT", "HOLDING", "SETUP", "ABOVE", "FAILED", "BELOW", "BASE", "NO_DATA")
DEFAULT_CSV_DIR = engine.DEFAULT_CSV.parent
DEFAULT_RECENT_SESSIONS = 3
DEFAULT_NEAR_PCT = 3.0
# Kite serves the running candle, so a bar counts as closed only this long after its end on the host clock: the
# engine's max_stamp_ahead, how far a host clock may run ahead of the exchange before its stamps are not believed.
KITE_CLOCK_ALLOWANCE = timedelta(seconds=30)


@dataclass
class Row:
    """One symbol's reading on its last closed bar. Prices are in rupees; ``gap_pct`` is the close against the
    base high in percent (positive above it); ``breakouts`` counts every bar since the listing on which the
    rule fired (walk-forward, so a later re-cross counts again)."""
    symbol: str
    status: str
    note: str = ""
    last_bar: Optional[datetime] = None
    close: Optional[float] = None
    base_high: Optional[float] = None
    gap_pct: Optional[float] = None
    avwap: Optional[float] = None
    rvol: Optional[float] = None
    atr: Optional[float] = None
    sessions: int = 0
    bars: int = 0
    breakouts: int = 0
    last_breakout: Optional[datetime] = None
    stop: Optional[float] = None
    target: Optional[float] = None

    def as_dict(self) -> dict:
        d = asdict(self)
        for key in ("last_bar", "last_breakout"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d


# ------------------------------------------------------------------------------ the universe
def parse_universe(lines: Iterable[str], where: str = "universe", header: bool = True) -> Dict[str, Optional[datetime]]:
    """``SYMBOL``, ``SYMBOL=YYYY-MM-DD`` or ``SYMBOL,YYYY-MM-DD`` per line (further columns are ignored); blank lines,
    ``#`` comments and, with ``header``, a leading ``symbol,listing_date`` header are skipped. Symbols are upper-cased;
    one listed twice is an error, as is a date in another format."""
    out: Dict[str, Optional[datetime]] = {}
    first = header
    for n, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(",")                      # further columns may hold anything, '=' included
        sym, eq, when = parts[0].partition("=")
        if not eq and len(parts) > 1:
            when = parts[1]
        sym, when = sym.strip().upper(), when.strip()
        if first and sym == "SYMBOL":
            first = False
            continue
        first = False
        if not sym or any(c.isspace() for c in sym):
            raise ValueError(f"{where} line {n}: {raw.strip()!r} is not SYMBOL or SYMBOL=YYYY-MM-DD")
        if sym in out:
            raise ValueError(f"{where} line {n}: {sym} is listed twice")
        listing = None
        if when:
            try:
                listing = datetime.strptime(when, "%Y-%m-%d").replace(tzinfo=IST)
            except ValueError as e:
                raise ValueError(f"{where} line {n}: listing date {when!r} is not YYYY-MM-DD") from e
        out[sym] = listing
    return out


def csv_files(csv_dir: Path, symbols: Iterable[str]) -> Dict[str, Path]:
    """The bar file of each symbol: ``SYMBOL_*.csv`` or ``SYMBOL.csv`` in ``csv_dir`` (the newest by name when there are
    several). A symbol without one maps to a path that does not exist, which the adapter reports."""
    files: Dict[str, Path] = {}
    candidates = sorted(csv_dir.glob("*.csv")) if csv_dir.is_dir() else []
    for sym in symbols:
        matches = sorted((p for p in candidates if p.stem.split("_")[0].upper() == sym), key=lambda p: p.name.upper())
        if matches:
            files[sym] = matches[-1]
            if len(matches) > 1:
                logger.warning(f"[{sym}] {len(matches)} files in {csv_dir} start with {sym}_; using {matches[-1].name}.")
        else:
            files[sym] = csv_dir / f"{sym}_5m.csv"
    return files


def csv_as_of(files: Dict[str, Path], now: Optional[datetime] = None) -> datetime:
    """A bar after the newest bar in any readable file, so every file's last bar counts as closed (a file's own clock,
    as engine.py --source csv keeps time, so the screen reads the same on any date), but never past the start of the
    bar now forming on the host clock: a file written during the session may end with the running bar."""
    now = now or datetime.now(timezone.utc)
    last: List[pd.Timestamp] = []
    for path in files.values():
        if not path.exists():
            continue
        try:
            df = CsvReplayAdapter.load(path)
        except Exception as e:                       # the adapter reports it, per symbol, when it reads the file
            logger.warning(f"Cannot read bars from {path}: {e!r}")
            continue
        if len(df):
            last.append(df.index[-1])
    if not last:
        return now
    forming = engine.bar_floor(now.astimezone(IST))
    return min((max(last) + pd.Timedelta(minutes=BAR_MINUTES)).to_pydatetime(), forming)


def source_as_of(source: str, files: Optional[Dict[str, Path]] = None, now: Optional[datetime] = None) -> datetime:
    """Up to when a source's history is read (see the module docstring): the last closed bar is the last bar that
    ends at or before this."""
    now = now or datetime.now(timezone.utc)
    if source == "kite":
        return now - KITE_CLOCK_ALLOWANCE
    if source == "csv":
        return csv_as_of(files or {}, now)
    return now


# ------------------------------------------------------------------------------ the reading
def classify(symbol: str, df: pd.DataFrame, alpha: AlphaEngine, recent_sessions: int = DEFAULT_RECENT_SESSIONS,
             near_pct: float = DEFAULT_NEAR_PCT) -> Row:
    """Where ``symbol`` stands on the last closed bar of ``df`` (history from its listing, as the orchestrator
    anchors it). Every figure is the AlphaEngine's own, on bars up to and including that one."""
    row = Row(symbol=symbol, status="BASE", bars=len(df), sessions=int(df.index.normalize().nunique()))
    if df.empty:
        row.status, row.note = "NO_DATA", "no bars"
        return row
    last_ts = df.index[-1]
    row.last_bar = last_ts.to_pydatetime()
    row.close = float(df["Close"].iloc[-1])
    base_len = alpha.base_length(df)
    warm_up = max(base_len, alpha.rvol_lookback)
    if len(df) <= warm_up:
        base = f"the first {alpha.base_sessions} sessions" if alpha.base_sessions else f"{alpha.base_bars} bars"
        row.note = (f"base incomplete: {len(df)} bars, evaluation starts after {warm_up} "
                    f"(the base is {base}, the volume baseline {alpha.rvol_lookback} bars)")
        return row
    ind = alpha.indicators(df)
    last = ind.iloc[-1]
    row.base_high = float(last["Base_High"])
    row.avwap = float(last["AVWAP"]) if pd.notna(last["AVWAP"]) else None
    row.rvol = float(last["RVOL"]) if pd.notna(last["RVOL"]) else None
    row.atr = float(last["ATR"]) if pd.notna(last["ATR"]) else None
    row.gap_pct = (row.close / row.base_high - 1.0) * 100.0
    fired = ind.index[ind["Breakout"].astype(bool)]
    row.breakouts = len(fired)
    if len(fired):
        row.last_breakout = fired[-1].to_pydatetime()
    window = set(sorted(set(df.index.date))[-recent_sessions:])
    above = row.close > row.base_high
    if bool(ind["Breakout"].iloc[-1]):                   # evaluate()'s own test
        row.status = "BREAKOUT"
        signal = alpha.evaluate(symbol, df)              # logs the trigger as the engine would
        if signal is not None:
            row.stop, row.target = signal.stop_loss, signal.target
            row.note = f"stop {signal.stop_loss:.2f} target {signal.target:.2f}"
    elif len(fired) and fired[-1].date() in window:
        row.status = "HOLDING" if above else "FAILED"
        row.note = (f"breakout {fired[-1]:%Y-%m-%d %H:%M}, close "
                    + ("still above the base" if above else "back at or below the base"))
    elif above:
        row.status = "ABOVE"
        row.note = "above the base with no signal in the window (crossed on thin volume or below the AVWAP, or before it)"
    # Tick-grid prices land a hair past a round percentage in floating point (97/100 -> -3.0000000000000027).
    elif row.gap_pct + near_pct >= -1e-9 and row.avwap is not None and row.close > row.avwap:
        row.status = "SETUP"
        row.note = f"{abs(row.gap_pct):.2f}% below the base, above the AVWAP"
    else:
        row.status = "BELOW"
        if row.avwap is not None and row.close <= row.avwap:
            row.note = f"{abs(row.gap_pct):.2f}% below the base, at or below the AVWAP {row.avwap:.2f}"
        else:
            row.note = f"{abs(row.gap_pct):.2f}% below the base"
    return row


def rank(rows: Iterable[Row]) -> List[Row]:
    """Best status first; within a status the close nearest to (or furthest above) the base first; then by symbol."""
    return sorted(rows, key=lambda r: (STATUS_ORDER.index(r.status),
                                       -(r.gap_pct if r.gap_pct is not None else -math.inf), r.symbol))


class _Reasons(logging.Handler):
    """Keeps the first error the history sync logged for each symbol (the root cause), for the NO_DATA rows."""
    def __init__(self):
        super().__init__(logging.ERROR)
        self.by_symbol: Dict[str, str] = {}
        self.general: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("[") and "]" in msg:
            sym, rest = msg[1:msg.index("]")], msg[msg.index("]") + 1:].strip()
            self.by_symbol.setdefault(sym, rest)
        elif self.general is None:
            self.general = msg


async def screen(watchlist: Dict[str, Optional[datetime]], adapter: BrokerAdapter, alpha: AlphaEngine,
                 as_of: Optional[datetime] = None, max_lookback_days: int = 180, allow_partial_history: bool = False,
                 recent_sessions: int = DEFAULT_RECENT_SESSIONS, near_pct: float = DEFAULT_NEAR_PCT) -> List[Row]:
    """Load every symbol's history through the engine's orchestrator (its anchoring rules apply: a history that does
    not reach the listing is NO_DATA unless ``allow_partial_history``) and rank the readings."""
    orchestrator = ProductionOrchestrator(watchlist, adapter, as_of=as_of, max_lookback_days=max_lookback_days,
                                          allow_partial_history=allow_partial_history)
    reasons = _Reasons()
    engine.logger.addHandler(reasons)
    try:
        await orchestrator.build_the_ground()
    finally:
        engine.logger.removeHandler(reasons)
    rows = []
    end = (as_of or datetime.now(timezone.utc)).astimezone(IST)
    for sym in watchlist:
        df = orchestrator.market_state.get(sym)
        if df is None:
            listing = orchestrator.watchlist.get(sym)
            if listing is not None and listing > end:        # a typo in the date: nothing can have traded yet
                note = f"listing date {listing:%Y-%m-%d} is after the as-of time {end:%Y-%m-%d %H:%M} IST"
            else:
                note = reasons.by_symbol.get(sym) or reasons.general or "no history"
            rows.append(Row(sym, "NO_DATA", note=note))
        else:
            rows.append(classify(sym, df, alpha, recent_sessions, near_pct))
    return rank(rows)


# ------------------------------------------------------------------------------ reports
def _fmt(value, spec: str = "") -> str:
    if value is None:
        return "-"
    if hasattr(value, "strftime"):                   # a datetime, whichever class a host's clock shim hands out
        return f"{value:%Y-%m-%d %H:%M}"
    if isinstance(value, float) and not math.isfinite(value):
        return "-"
    return format(value, spec)


COLUMNS = [("Symbol", "symbol", "", "<"), ("Status", "status", "", "<"), ("Last bar", "last_bar", "", "<"),
           ("Close", "close", ".2f", ">"), ("Base high", "base_high", ".2f", ">"), ("Gap%", "gap_pct", "+.2f", ">"),
           ("AVWAP", "avwap", ".2f", ">"), ("RVOL", "rvol", ".2f", ">"), ("Sessions", "sessions", "d", ">"),
           ("Signals", "breakouts", "d", ">"), ("Last signal", "last_breakout", "", "<"), ("Note", "note", "", "<")]


def render_table(rows: Iterable[Row]) -> str:
    """A plain aligned table, one line per symbol, in the given order."""
    rows = list(rows)
    cells = [[_fmt(getattr(r, attr), spec) for _, attr, spec, _ in COLUMNS] for r in rows]
    widths = [max(len(head), *(len(c[i]) for c in cells)) if cells else len(head)
              for i, (head, _, _, _) in enumerate(COLUMNS)]
    def line(values):
        return "  ".join(f"{v:{align}{w}}" for v, w, (_, _, _, align) in zip(values, widths, COLUMNS, strict=True)).rstrip()
    out = [line([head for head, _, _, _ in COLUMNS]), line(["-" * w for w in widths])]
    out += [line(c) for c in cells]
    return "\n".join(out)


def report(rows: Iterable[Row], as_of: Optional[datetime], source: str, parameters: dict) -> dict:
    return {"generated_at": datetime.now(IST).isoformat(), "as_of": None if as_of is None else as_of.astimezone(IST).isoformat(),
            "source": source, "parameters": parameters, "statuses": list(STATUS_ORDER),
            "rows": [r.as_dict() for r in rows]}


def write_csv(rows: Iterable[Row], stream: io.TextIOBase) -> None:
    fields = [f for f in Row.__dataclass_fields__]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(r.as_dict())


# ------------------------------------------------------------------------------ the CLI
def _recent_sessions(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a whole number of sessions >= 1")
    return value


def _percent(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a finite percentage >= 0")
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IPO momentum screener: where each listing stands against the engine's "
                                            "base-breakout rule on its last closed bar. No orders, no live feed.")
    p.add_argument("symbols", nargs="*", metavar="SYMBOL[=YYYY-MM-DD]",
                   help="NSE tradingsymbols, each with its IPO listing date (without one, only the last 20 days are "
                        "fetched and their first bar is treated as the listing: demo semantics)")
    p.add_argument("--universe", type=Path, help="file of SYMBOL, SYMBOL=YYYY-MM-DD or SYMBOL,YYYY-MM-DD lines "
                                                 "(a symbol,listing_date header and # comments are skipped)")
    p.add_argument("--source", choices=["yahoo", "csv", "kite"], default="yahoo", help="historical data source (default: yahoo)")
    p.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR,
                   help="for --source csv: directory of SYMBOL_*.csv bar files (default: the bundled data directory)")
    p.add_argument("--base-sessions", type=int, help="define the IPO base as the first N sessions (default: first 150 bars)")
    p.add_argument("--max-lookback-days", type=_lookback_days, default=180,
                   help="fetch history from at most this many days back (default: 180; a symbol without a listing "
                        "date uses at most 20, as the engine's demos do)")
    p.add_argument("--allow-partial-history", action="store_true",
                   help="screen symbols whose history does not reach the listing (base anchored at the first bar)")
    p.add_argument("--rvol-threshold", type=_positive, default=2.0)
    p.add_argument("--rvol-mode", choices=AlphaEngine.RVOL_MODES, default="trailing",
                   help="volume baseline: previous 20 bars, or the same slot in prior sessions")
    p.add_argument("--risk-reward", type=_positive, default=3.0, help="target distance in multiples of risk")
    p.add_argument("--recent-sessions", type=_recent_sessions, default=DEFAULT_RECENT_SESSIONS,
                   help=f"a signal within the last N sessions is HOLDING or FAILED (default: {DEFAULT_RECENT_SESSIONS})")
    p.add_argument("--near-pct", type=_percent, default=DEFAULT_NEAR_PCT,
                   help=f"a close within this many percent below the base high is a SETUP (default: {DEFAULT_NEAR_PCT})")
    p.add_argument("--json", type=Path, metavar="PATH", help="also write the readings as JSON")
    p.add_argument("--csv-out", type=Path, metavar="PATH", help="also write the readings as CSV")
    return p


def watchlist_from(args: argparse.Namespace) -> Dict[str, Optional[datetime]]:
    """The symbols on the command line and in --universe, merged; a symbol in both, or twice, is an error."""
    watchlist = parse_universe(args.symbols, "argument", header=False)
    if args.universe is not None:
        try:
            lines = args.universe.read_text(encoding="utf-8-sig").splitlines()   # a spreadsheet's BOM is skipped
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"Cannot read --universe {args.universe}: {e}") from e
        for sym, listing in parse_universe(lines, str(args.universe)).items():
            if sym in watchlist:
                raise ValueError(f"{sym} is given both on the command line and in {args.universe}")
            watchlist[sym] = listing
    return watchlist


def _config_error(args: argparse.Namespace) -> Optional[str]:
    if args.base_sessions is not None and args.base_sessions < 1:
        return "--base-sessions must be at least 1."
    if args.source == "csv" and not args.csv_dir.is_dir():
        return f"--csv-dir {args.csv_dir} is not a directory."
    if args.source == "kite" and not all((os.environ.get("KITE_API_KEY"), os.environ.get("KITE_ACCESS_TOKEN"))):
        return "Set KITE_API_KEY and KITE_ACCESS_TOKEN for --source kite."
    if args.json is not None and args.csv_out is not None and args.json.resolve() == args.csv_out.resolve():
        return "--json and --csv-out must be different files."
    return None


def _write_reports(args: argparse.Namespace, rows: List[Row], as_of: datetime) -> bool:
    """The --json and --csv-out files; False when one could not be written (the table was already printed)."""
    parameters = {"rvol_threshold": args.rvol_threshold, "rvol_mode": args.rvol_mode, "risk_reward": args.risk_reward,
                  "base_sessions": args.base_sessions, "max_lookback_days": args.max_lookback_days,
                  "allow_partial_history": args.allow_partial_history, "recent_sessions": args.recent_sessions,
                  "near_pct": args.near_pct}
    try:
        if args.json is not None:
            args.json.write_text(json.dumps(report(rows, as_of, args.source, parameters), indent=2), encoding="utf-8")
            logger.info(f"Wrote {args.json}")
        if args.csv_out is not None:
            with open(args.csv_out, "w", encoding="utf-8", newline="") as stream:
                write_csv(rows, stream)
            logger.info(f"Wrote {args.csv_out}")
    except OSError as e:
        logger.error(f"Cannot write the report: {e}")
        return False
    return True


async def main(argv: Optional[List[str]] = None) -> int:
    """Exit 0 when at least one symbol was read (whatever its status), 1 when none was, 2 for a bad configuration."""
    args = build_arg_parser().parse_intermixed_args(argv)    # symbols may come before and after the options
    logger.info("=== IPO MOMENTUM SCREENER ===")
    problem = _config_error(args)
    if problem is None:
        try:
            watchlist = watchlist_from(args)
        except ValueError as e:
            problem = str(e)
        else:
            if not watchlist:
                problem = "Nothing to screen: give symbols (SYMBOL or SYMBOL=YYYY-MM-DD) or --universe FILE."
    if problem:
        logger.critical(problem)
        return 2

    files = None
    if args.source == "kite":
        try:
            adapter: BrokerAdapter = ZerodhaKiteAdapter(os.environ["KITE_API_KEY"], os.environ["KITE_ACCESS_TOKEN"])
        except ImportError as e:                     # the optional SDK (pip install kiteconnect) is not installed
            logger.critical(str(e))
            return 2
    elif args.source == "csv":
        files = csv_files(args.csv_dir, watchlist)
        adapter = CsvReplayAdapter(files)
    else:
        adapter = PublicExchangeAdapter()
    as_of = source_as_of(args.source, files)
    alpha = AlphaEngine(rvol_threshold=args.rvol_threshold, risk_reward_ratio=args.risk_reward,
                        rvol_mode=args.rvol_mode, base_sessions=args.base_sessions)
    logger.info(f"Screening {len(watchlist)} symbol(s) from {args.source} as of {as_of.astimezone(IST):%Y-%m-%d %H:%M} IST.")
    rows = await screen(watchlist, adapter, alpha, as_of=as_of, max_lookback_days=args.max_lookback_days,
                        allow_partial_history=args.allow_partial_history, recent_sessions=args.recent_sessions,
                        near_pct=args.near_pct)

    try:
        print(render_table(rows), flush=True)
        printed = True
    except OSError as e:                             # stdout on a full disk or a failed device; the reports still go out
        logger.error(f"Cannot print the table: {e}")
        printed = False
        try:                                          # what stdout still holds is flushed at exit: let it go nowhere, quietly
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
    if not _write_reports(args, rows, as_of) or not printed:
        return 1

    counts = {status: sum(1 for r in rows if r.status == status) for status in STATUS_ORDER}
    read = len(rows) - counts["NO_DATA"]
    logger.info(f"{read} of {len(rows)} symbol(s) read: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    return 0 if read else 1


def run(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")     # a note may carry a path the console's code page cannot show
    try:
        return asyncio.run(main(argv))
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(run())
