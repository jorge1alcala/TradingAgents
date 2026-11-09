"""Persistent EODHD daily OHLCV cache for Surge Zone Lab.

This is a standalone adapter: it does not change TradingAgents' default vendors.
Use only completed EOD bars; no partial current-session candles.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

_ENDPOINT = "https://eodhd.com/api/eod/{symbol}"
_COLUMNS = "symbol,date,open,high,low,close,adjusted_close,volume"


def _day(value: str) -> date:
    return date.fromisoformat(value)


class EODHDDailyCache:
    def __init__(
        self,
        db_path: str | Path = "data/surge_market.sqlite3",
        token: str | None = None,
        fetcher: Callable | None = None,
    ):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.token = token if token is not None else os.environ.get("EODHD_API_TOKEN", "")
        self.fetcher = fetcher or requests.get
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS daily_bars (
                    symbol TEXT NOT NULL, date TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, adjusted_close REAL, volume INTEGER,
                    retrieved_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, date)
                );
                CREATE TABLE IF NOT EXISTS coverage (
                    symbol TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, start, end)
                );
                CREATE TABLE IF NOT EXISTS api_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, start TEXT, end TEXT,
                    requested_at TEXT NOT NULL, status TEXT NOT NULL
                );
            """)

    def _db(self):
        return sqlite3.connect(self.path)

    def request_count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM api_requests").fetchone()[0]

    def _coverage(self, db, symbol: str, start: date, end: date) -> list[tuple[date, date]]:
        rows = db.execute(
            "SELECT start,end FROM coverage WHERE symbol=? AND end>=? AND start<=?",
            (symbol, start.isoformat(), end.isoformat()),
        ).fetchall()
        return sorted((_day(a), _day(b)) for a, b in rows)

    @staticmethod
    def _gaps(start: date, end: date, windows: list[tuple[date, date]]) -> list[tuple[date, date]]:
        gaps = []
        cursor = start
        for a, b in windows:
            if b < cursor:
                continue
            if a > cursor:
                gaps.append((cursor, min(end, a - timedelta(days=1))))
            cursor = max(cursor, b + timedelta(days=1))
            if cursor > end:
                break
        if cursor <= end:
            gaps.append((cursor, end))
        return gaps

    def _fetch(self, db, symbol: str, start: date, end: date) -> int:
        if not self.token:
            raise ValueError("EODHD_API_TOKEN is missing; set it in your environment.")
        now = datetime.now(UTC).isoformat()
        params = {"api_token": self.token, "from": start.isoformat(),
                  "to": end.isoformat(), "period": "d", "fmt": "json"}
        try:
            response = self.fetcher(_ENDPOINT.format(symbol=symbol), params=params, timeout=45)
            response.raise_for_status()
            records = response.json()
            if not isinstance(records, list):
                raise ValueError("Unexpected EODHD response format")
            normalized = []
            for item in records:
                d = _day(str(item["date"]))
                o, h, low, c = (float(item[k]) for k in ("open", "high", "low", "close"))
                if not (low <= o <= h and low <= c <= h and low > 0):
                    raise ValueError("Invalid OHLC data")
                normalized.append((symbol, d.isoformat(), o, h, low, c,
                                   float(item["adjusted_close"]) if item.get("adjusted_close") is not None else None,
                                   int(item["volume"]) if item.get("volume") is not None else None, now))
        except Exception as exc:
            # Avoid propagating an HTTP URL that may contain the private API token.
            db.execute("INSERT INTO api_requests(symbol,start,end,requested_at,status) VALUES(?,?,?,?,?)",
                       (symbol, start.isoformat(), end.isoformat(), now, "failed"))
            db.commit()
            raise RuntimeError(f"EODHD request failed ({type(exc).__name__}); check the provider and logs.") from None
        db.executemany(
            "INSERT OR REPLACE INTO daily_bars VALUES (?,?,?,?,?,?,?,?,?)", normalized
        )
        db.execute("INSERT OR REPLACE INTO coverage VALUES (?,?,?,?)",
                   (symbol, start.isoformat(), end.isoformat(), now))
        db.execute("INSERT INTO api_requests(symbol,start,end,requested_at,status) VALUES(?,?,?,?,?)",
                   (symbol, start.isoformat(), end.isoformat(), now, "ok"))
        db.commit()
        return len(normalized)

    def get_bars(
        self, symbol: str, start: str, end: str, *,
        force_refresh: bool = False, refresh_last_days: int = 0,
    ) -> dict:
        """Return persisted bars and sync status.

        Historical periods previously fetched are never requested twice unless
        force_refresh=True. To revalidate recent provider corrections, set
        refresh_last_days=7 (or another positive integer).
        The caller supplies the last *completed* trading-session date as end.
        """
        symbol = symbol.strip().upper()
        if not symbol or not all(ch.isalnum() or ch in "._-" for ch in symbol):
            raise ValueError("Invalid symbol")
        a, b = _day(start), _day(end)
        if a > b:
            raise ValueError("start is after end")
        today = datetime.now(UTC).date()
        if b > today:
            raise ValueError("end cannot be in the future")
        if refresh_last_days < 0:
            raise ValueError("refresh_last_days must be nonnegative")

        requests_before = self.request_count()
        with self._db() as db:
            windows = self._coverage(db, symbol, a, b)
            gaps = [(a, b)] if force_refresh else self._gaps(a, b, windows)
            # Explicit recent-window revalidation, even if coverage exists.
            if not force_refresh and refresh_last_days:
                recent_start = max(a, b - timedelta(days=refresh_last_days - 1))
                gaps.append((recent_start, b))
            fetched = 0
            for left, right in gaps:
                fetched += self._fetch(db, symbol, left, right)
            rows = db.execute(
                "SELECT " + _COLUMNS + " FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
                (symbol, start, end),
            ).fetchall()
        columns = _COLUMNS.split(",")
        return {
            "symbol": symbol, "timeframe": "1d", "start": start, "end": end,
            "price_basis": "raw_ohlc_with_adjusted_close_separate",
            "bars": [dict(zip(columns, row, strict=True)) for row in rows],
            "new_or_refreshed_bars": fetched,
            "api_requests_this_scan": self.request_count() - requests_before,
            "source": "eodhd" if gaps else "local_database",
            "db_path": str(self.path),
        }
