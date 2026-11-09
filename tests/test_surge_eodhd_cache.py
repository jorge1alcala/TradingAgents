"""Unit tests for the optional EODHD daily cache; no internet access required."""
import pytest

from tradingagents.dataflows.vendors.eodhd.daily_cache import EODHDDailyCache


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeEODHD:
    def __init__(self):
        self.calls = []

    def __call__(self, url, *, params, timeout):
        self.calls.append(dict(params))
        date = params["from"]
        return FakeResponse([
            {"date": date, "open": 100, "high": 103, "low": 99,
             "close": 102, "adjusted_close": 101.5, "volume": 1000}
        ])


def test_second_identical_scan_survives_restart_without_api_call(tmp_path):
    provider = FakeEODHD()
    db_path = tmp_path / "cache.sqlite"
    first = EODHDDailyCache(db_path, token="test", fetcher=provider)
    a = first.get_bars("SPY.US", "2020-01-02", "2020-01-03")
    assert a["api_requests_this_scan"] == 1
    second = EODHDDailyCache(db_path, token="", fetcher=provider)
    b = second.get_bars("SPY.US", "2020-01-02", "2020-01-03")
    assert b["api_requests_this_scan"] == 0
    assert b["source"] == "local_database"
    assert a["bars"] == b["bars"]
    assert len(provider.calls) == 1


def test_incremental_extension_only_requests_missing_dates(tmp_path):
    provider = FakeEODHD()
    cache = EODHDDailyCache(tmp_path / "cache.sqlite", token="test", fetcher=provider)
    cache.get_bars("SPY.US", "2020-01-02", "2020-01-03")
    result = cache.get_bars("SPY.US", "2020-01-02", "2020-01-08")
    assert result["api_requests_this_scan"] == 1
    assert provider.calls[-1]["from"] == "2020-01-04"
    assert provider.calls[-1]["to"] == "2020-01-08"


def test_force_refresh_makes_new_request(tmp_path):
    provider = FakeEODHD()
    cache = EODHDDailyCache(tmp_path / "cache.sqlite", token="test", fetcher=provider)
    cache.get_bars("SPY.US", "2020-01-02", "2020-01-03")
    result = cache.get_bars("SPY.US", "2020-01-02", "2020-01-03", force_refresh=True)
    assert result["api_requests_this_scan"] == 1
    assert len(provider.calls) == 2


def test_rejects_future_and_invalid_symbols(tmp_path):
    cache = EODHDDailyCache(tmp_path / "cache.sqlite", token="test", fetcher=FakeEODHD())
    with pytest.raises(ValueError):
        cache.get_bars("../SPY", "2020-01-02", "2020-01-03")
    with pytest.raises(ValueError):
        cache.get_bars("SPY.US", "2020-01-03", "2020-01-02")
    with pytest.raises(ValueError):
        cache.get_bars("SPY.US", "2020-01-02", "2099-01-01")


def test_raw_prices_are_not_mixed_with_adjusted_close(tmp_path):
    provider = FakeEODHD()
    cache = EODHDDailyCache(tmp_path / "cache.sqlite", token="test", fetcher=provider)
    b = cache.get_bars("SPY.US", "2020-01-02", "2020-01-03")["bars"][0]
    assert b["close"] == 102
    assert b["adjusted_close"] == 101.5
