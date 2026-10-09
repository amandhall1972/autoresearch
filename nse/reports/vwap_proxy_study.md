# VWAP proxy study (daily typical price vs hourly-bar session VWAP)

Sample: five liquid Nifty 50 names, 1-hour bars, 2025-01-01..2025-03-28 (the vendor's intraday history for NSE starts in 2023, so this cannot cover 2020-2026).

| name | days | median |sessionVWAP - HLC3| (bps) | 90th pct (bps) | median |rolling20 VWAP diff| (bps) | sign agreement close>VWAP20 |
|---|---|---|---|---|---|
| HDFCBANK | 61 | 11.0 | 23.9 | 1.5 | 100.0% |
| INFY | 61 | 12.6 | 34.0 | 8.5 | 100.0% |
| RELIANCE | 61 | 8.6 | 27.2 | 2.8 | 97.6% |
| SBIN | 61 | 9.1 | 27.2 | 3.0 | 100.0% |
| TATASTEEL | 61 | 17.8 | 39.7 | 5.7 | 100.0% |

Across the sample the daily typical price sits within a median 11 bps of the hourly-bar session VWAP, the 20-day rolling VWAPs built from the two differ by a median 3 bps, and the close-above-VWAP20 signal agrees on 99.5% of days.

Interpretation: for the rolling/anchored VWAP levels used by this strategy (multi-day windows), the daily-bar proxy is a faithful substitute; it is not a substitute for an intraday session-VWAP strategy, which this project does not attempt.