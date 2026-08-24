"""Stooq: CSV-parsing en de grenzen van deze bron."""
import asyncio
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.adapter import VenueError
from gold_scalper.broker.stooq import StooqVenue

HISTORY = "\n".join(
    ["Date,Open,High,Low,Close,Volume"]
    + [f"2026-06-{d:02d},3300.0,3310.0,3290.0,3305.0,0" for d in range(1, 29)]
)


class FakeResponse:
    def __init__(self, text, status=200):
        self._text, self.status = text, status
    async def text(self): return self._text
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeSession:
    def __init__(self, text, status=200):
        self.text, self.status = text, status
        self.calls = []
    def get(self, url, **kw):
        self.calls.append({"url": url, **kw})
        return FakeResponse(self.text, self.status)


def venue(text=HISTORY, status=200, spread=0.0):
    return StooqVenue(FakeSession(text, status), "xauusd", spread)


def test_history_parses():
    c = asyncio.run(venue().candles("xauusd", "1d", 100))
    assert len(c) == 28
    c.validate()


def test_intraday_is_refused_with_an_explanation():
    """Stooq heeft geen gratis intraday; dat moet je meteen weten in plaats van
    na een dag zonder trades."""
    with pytest.raises(VenueError, match="intraday"):
        asyncio.run(venue().candles("xauusd", "1m", 100))


def test_unknown_symbol_is_detected_despite_http_200():
    """Stooq geeft 'No data' terug met status 200; zonder controle zou dat
    verderop als 'geen candles' opduiken."""
    with pytest.raises(VenueError, match="kent symbool"):
        asyncio.run(venue("No data").candles("xauusd", "1d", 10))


def test_empty_body_is_detected():
    with pytest.raises(VenueError):
        asyncio.run(venue("   ").candles("xauusd", "1d", 10))


def test_http_error_is_reported():
    with pytest.raises(VenueError, match="HTTP 503"):
        asyncio.run(venue(HISTORY, status=503).candles("xauusd", "1d", 10))


def test_malformed_rows_are_skipped_not_fatal():
    text = HISTORY + "\n2026-07-01,kapot,x,y,z,0"
    c = asyncio.run(venue(text).candles("xauusd", "1d", 100))
    assert len(c) == 28


def test_quote_applies_assumed_spread():
    quote_csv = "Symbol,Date,Time,Open,High,Low,Close\nXAUUSD,2026-08-24,15:00:00,3300,3310,3290,3305"
    q = asyncio.run(venue(quote_csv, spread=0.4).quote())
    assert q.mid == pytest.approx(3305.0)
    assert q.spread == pytest.approx(0.4)


def test_zero_spread_marks_costs_disabled():
    assert venue(spread=0.0).costs_disabled is True
    assert venue(spread=0.3).costs_disabled is False


def test_cannot_trade():
    with pytest.raises(VenueError):
        asyncio.run(venue().place_order("xauusd", "buy", 1.0))


def test_describe_flags_no_intraday():
    """Het dashboard moet kunnen tonen dat deze bron geen scalping ondersteunt."""
    d = venue().describe()
    assert d["intraday"] is False
    assert d["real_prices"] is True
    assert d["real_spread"] is False
