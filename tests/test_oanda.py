"""OANDA-adapter tegen een nagebootste API. Getest wordt vooral de vertaling:
eenheden, richting via teken, en de plafonds."""
import os, sys, asyncio, json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.oanda import OandaVenue
from gold_scalper.broker.adapter import TradingDisabledError, VenueError


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload; self.status = status
    async def json(self): return self._payload
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeSession:
    def __init__(self, routes): self.routes = routes; self.calls = []
    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for key, value in self.routes.items():
            if key in url:
                return FakeResponse(*value) if isinstance(value, tuple) else FakeResponse(value)
        return FakeResponse({"errorMessage": "geen route"}, 404)


PRICING = {"prices": [{"time": "2026-08-23T12:00:00.000000000Z", "tradeable": True,
                       "bids": [{"price": "3300.10"}], "asks": [{"price": "3300.28"}]}]}


def venue(routes, trading=True, max_units=10.0):
    return OandaVenue(FakeSession(routes), "tok", "001-004-1-001",
                      trading_enabled=trading, max_units=max_units)


def test_instrument_naming_variants():
    v = venue({})
    assert v._instrument("XAUUSD") == "XAU_USD"
    assert v._instrument("XAU/USD") == "XAU_USD"
    assert v._instrument("xau_usd") == "XAU_USD"


def test_quote_parses_bid_ask_and_spread():
    q = asyncio.run(venue({"/pricing": PRICING}).quote("XAUUSD"))
    assert q.bid == pytest.approx(3300.10)
    assert q.spread == pytest.approx(0.18)


def test_candles_skip_incomplete():
    """Een lopende candle mag nooit in de analyse belanden."""
    payload = {"candles": [
        {"time": "2026-08-23T11:58:00Z", "complete": True, "volume": 10,
         "mid": {"o": "1", "h": "2", "l": "0.5", "c": "1.5"}},
        {"time": "2026-08-23T11:59:00Z", "complete": True, "volume": 12,
         "mid": {"o": "1.5", "h": "2.5", "l": "1.2", "c": "2.0"}},
        {"time": "2026-08-23T12:00:00Z", "complete": False, "volume": 3,
         "mid": {"o": "2.0", "h": "2.1", "l": "1.9", "c": "2.05"}},
    ]}
    c = asyncio.run(venue({"/candles": payload}).candles("XAUUSD", "1m", 100))
    assert len(c) == 2


def test_unknown_timeframe_rejected():
    with pytest.raises(VenueError):
        asyncio.run(venue({}).candles("XAUUSD", "3m", 10))


def test_buy_sends_positive_units_sell_negative():
    fill = {"orderFillTransaction": {"id": "5", "price": "3300.30", "units": "2",
                                     "tradeOpened": {"tradeID": "77"}}}
    v = venue({"/pricing": PRICING, "/orders": fill})
    asyncio.run(v.place_order("XAUUSD", "buy", 2.0))
    assert json.loads(json.dumps(v._session.calls[-1]["json"]))["order"]["units"] == "2.0"
    v2 = venue({"/pricing": PRICING, "/orders": fill})
    asyncio.run(v2.place_order("XAUUSD", "sell", 2.0))
    assert v2._session.calls[-1]["json"]["order"]["units"] == "-2.0"


def test_stop_loss_is_attached_on_fill():
    """Server-side stop vanaf het eerste moment; overleeft een HA-crash."""
    fill = {"orderFillTransaction": {"id": "5", "price": "3300.30", "units": "1",
                                     "tradeOpened": {"tradeID": "77"}}}
    v = venue({"/pricing": PRICING, "/orders": fill})
    asyncio.run(v.place_order("XAUUSD", "buy", 1.0, stop_loss=3299.0))
    assert v._session.calls[-1]["json"]["order"]["stopLossOnFill"]["price"] == "3299.000"


def test_slippage_is_measured():
    fill = {"orderFillTransaction": {"id": "5", "price": "3300.35", "units": "1",
                                     "tradeOpened": {"tradeID": "77"}}}
    r = asyncio.run(venue({"/pricing": PRICING, "/orders": fill}).place_order("XAUUSD", "buy", 1.0))
    assert r.success and r.slippage == pytest.approx(0.07, abs=1e-9)


def test_trading_disabled_blocks_orders():
    with pytest.raises(TradingDisabledError):
        asyncio.run(venue({"/pricing": PRICING}, trading=False).place_order("XAUUSD", "buy", 1.0))


def test_units_cap_enforced():
    with pytest.raises(VenueError):
        asyncio.run(venue({"/pricing": PRICING}, max_units=5.0).place_order("XAUUSD", "buy", 500.0))


def test_rejected_order_reports_reason():
    reject = {"orderRejectTransaction": {"rejectReason": "INSUFFICIENT_MARGIN"}}
    r = asyncio.run(venue({"/pricing": PRICING, "/orders": reject}).place_order("XAUUSD", "buy", 1.0))
    assert not r.success and r.error == "INSUFFICIENT_MARGIN"


def test_bad_token_gives_clear_message():
    v = venue({"/summary": ({"errorMessage": "x"}, 401)})
    with pytest.raises(VenueError, match="token"):
        asyncio.run(v.account())


def test_positions_map_sign_to_side():
    payload = {"trades": [
        {"id": "1", "instrument": "XAU_USD", "currentUnits": "-3", "price": "3300.0",
         "openTime": "2026-08-23T12:00:00Z", "unrealizedPL": "1.5"},
        {"id": "2", "instrument": "XAU_USD", "currentUnits": "2", "price": "3301.0",
         "openTime": "2026-08-23T12:01:00Z", "unrealizedPL": "-0.5"},
    ]}
    ps = asyncio.run(venue({"/openTrades": payload}).positions("XAUUSD"))
    assert ps[0].side == "sell" and ps[0].units == 3.0
    assert ps[1].side == "buy" and ps[1].units == 2.0


def test_venue_reports_it_runs_inside_home_assistant():
    d = venue({}).describe()
    assert d["runs_in_home_assistant"] is True
    assert d["requires_external_process"] is False
