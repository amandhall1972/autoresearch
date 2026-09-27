# Bundled market data

## `SWIGGY_5m_2026-09-08_2026-09-25.csv`

Real NSE 5-minute OHLCV bars for **Swiggy Ltd (NSE: SWIGGY)**, used by
`engine.py --source csv` (offline runs) and by the test suite.

| Property | Value |
| --- | --- |
| Vendor | Financial Modeling Prep, `intraday-5-min` chart endpoint, symbol `SWIGGY.NS` |
| Retrieved | 2026-09-26 |
| Coverage | 13 sessions, 2026-09-08 → 2026-09-25 (no session on Mon 2026-09-14, an NSE holiday) |
| Rows | 944 bars |
| Columns | `datetime_ist` (bar **start** time, naive IST wall clock), `open`, `high`, `low`, `close`, `volume` |
| Price range | 261.40 – 289.65 |
| SHA-256 | `3453c8470bbce98f36f072fb0a95aac81b119af6efb848e85f7ab3d04e0ab3bf` |

### How it was assembled

The vendor caps one response at roughly 500–580 bars, so the window was fetched
in two overlapping requests (2026-09-07→17 and 2026-09-06→25). Both raw JSON
responses were parsed byte-for-byte with pandas. No values were typed or
edited by hand. The 145 bars present in both responses matched on every field
(0 mismatches) before they were merged. Every row satisfies
`low <= min(open, close) <= max(open, close) <= high`.

### Known vendor artifacts (left as delivered)

* The vendor's last bar of each session is 15:10 or 15:15. The exchange's final
  15:20 and 15:25 bars are absent (sessions have 72–73 bars instead of 75).
* 5 bars carry `volume == 0` despite a non-zero range:
  2026-09-08 09:20, 2026-09-09 09:55, 2026-09-21 09:45 and 09:50, 2026-09-25 15:15.
  The engine leaves them out of the RVOL baseline, and with zero volume they
  carry no weight in AVWAP. Their prices still count in High/Low, ATR and the
  base.

These artifacts are why this file is a **test and demo fixture**. Use it to
exercise the pipeline deterministically, not as a research-grade dataset.
