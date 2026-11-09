# Surge Zone Lab — EODHD daily price cache (experimental)

This branch adds an **optional**, persistent SQLite cache for **daily EODHD OHLCV**. The existing TradingAgents market analyst and vendor-routing configuration remain untouched. It does **not** automatically activate the cache from an existing Streamlit app; the current local app might not be committed to GitHub. Integrate the adapter after inspecting that local app.

## Windows / VS Code quick test

From the repo root in PowerShell:

```powershell
py -m venv .venv
..venvScriptsActivate.ps1
python -m pip install -e ".[dev]"
$env:EODHD_API_TOKEN = "YOUR_KEY"
python -m pytest tests/test_surge_eodhd_cache.py -q
```

To perform a **live** SPY daily scan (will spend an API call for a previously uncached range):

```powershell
@'
from tradingagents.dataflows.vendors.eodhd.daily_cache import EODHDDailyCache
cache = EODHDDailyCache()
result = cache.get_bars("SPY.US", "2020-01-01", "2026-11-06")
print({key: result[key] for key in ("source", "api_requests_this_scan", "new_or_refreshed_bars", "db_path")})
print("Stored candles:", len(result["bars"]))
'@ | python -
```

Run the same command again: if coverage was saved, the second run should require **zero EODHD requests**. To re-download the chosen window, pass `force_refresh=True`. To revalidate recent days, pass `refresh_last_days=7`.

## Data semantics

- DB defaults to `data/surge_market.sqlite3` and must be kept out of Git. Add/verify ignore rules in the local workspace.
- Market data is keyed by ticker and date; unadjusted OHLC and `adjusted_close` are stored **separately**. Do **not** mix adjusted close with raw high/low in zone calculations. Consider corporate actions when extending to long lookbacks.
- Coverage is date-range based, so non-trading dates within a successfully fetched period do not create repeated requests.
- `end` must refer to a **completed** trading session selected by the caller; this module cannot independently infer the exchange calendar or ensure today's bar is final.
- The optional `refresh_last_days` provides periodic correction handling; all dates in that requested range are re-fetched.
- A daily `coverage` record marks a successfully fetched interval and is written transactionally with its bars.
- Failed requests do not mark coverage as complete, allowing a retry.
- The request counter counts attempted API fetches. No key is printed or stored in the DB.
- If the vendor returns no bars for a range, that range is considered covered. Use Force Refresh if provider coverage subsequently changes.

## Integration next steps

1. Inspect the *actual* local Streamlit app entry point and its existing EODHD loader.
2. Use `EODHDDailyCache.get_bars` instead of directly calling the EODHD daily endpoint. Display source, request count and last stored bar.
3. Store zone results with strategy/config versioning separately from market bars. Keep original signals immutable for ML labels.
4. Extend later to 1m/5m/1h with provider window limits, time-zone/session calendars and a timeframe-aware key.
5. Validate all tests and real EODHD coverage before merging. This change does not implement a predictive model or full Surge Strategy score.

**Security:** do not commit EODHD keys or a populated local SQLite database. 
