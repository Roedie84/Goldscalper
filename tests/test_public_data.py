"""Publieke marktdata: parsing, nulkosten-markering en de kostenprojectie."""
import asyncio, json, os, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.adapter import VenueError
from gold_scalper.broker.public_data import PublicDataVenue
from gold_scalper.modes import LiveGate
from gold_scalper.storage.database import Trade
from gold_scalper.storage.performance import cost_projection


class FakeURL:
    def __init__(self, host): self.host = host


class FakeResponse:
    """Bootst een aiohttp-respons na, inclusief headers en URL.

    Die twee ontbraken eerst, waardoor de testdubbel niet meer leek op wat
    aiohttp werkelijk teruggeeft en een content-type-controle er dwars
    doorheen viel.
    """

    def __init__(self, payload, status=200, content_type="application/json",
                 host="query1.finance.yahoo.com"):
        self._payload, self.status = payload, status
        self.headers = {"Content-Type": content_type}
        self.url = FakeURL(host)

    async def json(self, content_type=None): return self._payload
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeSession:
    def __init__(self, payload, status=200, content_type="application/json",
                 host="query1.finance.yahoo.com"):
        self.payload, self.status = payload, status
        self.content_type, self.host = content_type, host
        self.calls = []
    def get(self, url, **kw):
        self.calls.append({"url": url, **kw})
        return FakeResponse(self.payload, self.status, self.content_type, self.host)


def chart(timestamps, o, h, l, c, v=None, price=3300.0, state="REGULAR"):
    return {"chart": {"result": [{
        "meta": {"regularMarketPrice": price, "regularMarketTime": 1755000000,
                 "marketState": state},
        "timestamp": timestamps,
        "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c,
                                  "volume": v or [10] * len(timestamps)}]},
    }]}}


SAMPLE = chart(
    [1755000000, 1755000060, 1755000120, 1755000180],
    [3300.0, 3300.5, 3301.0, 3301.2], [3300.8, 3301.2, 3301.5, 3301.6],
    [3299.8, 3300.2, 3300.7, 3301.0], [3300.5, 3301.0, 3301.2, 3301.4],
)


def venue(payload=SAMPLE, spread=0.0, status=200,
          content_type="application/json", host="query1.finance.yahoo.com"):
    return PublicDataVenue(
        FakeSession(payload, status, content_type, host), "GC=F", spread
    )


# ---------------- parsing ----------------

def test_candles_parse_and_drop_the_forming_one():
    """De laatste candle is nog in wording en mag de analyse niet in."""
    c = asyncio.run(venue().candles("GC=F", "1m", 100))
    assert len(c) == 3
    c.validate()


def test_null_candles_are_skipped_not_interpolated():
    """Yahoo geeft null bij candles zonder handel. Interpoleren zou elke
    indicator erna vervuilen."""
    payload = chart([1, 2, 3, 4, 5],
                    [3300.0, None, 3301.0, 3301.2, 3301.3],
                    [3300.8, None, 3301.5, 3301.6, 3301.7],
                    [3299.8, None, 3300.7, 3301.0, 3301.1],
                    [3300.5, None, 3301.2, 3301.4, 3301.5])
    c = asyncio.run(venue(payload).candles("GC=F", "1m", 100))
    assert len(c) == 3  # 5 minus de null minus de vormende


def test_unknown_timeframe_rejected():
    with pytest.raises(VenueError):
        asyncio.run(venue().candles("GC=F", "3m", 10))


def test_rate_limit_gives_actionable_message():
    with pytest.raises(VenueError, match="429"):
        asyncio.run(venue(status=429).candles("GC=F", "1m", 10))


def test_empty_result_is_explained():
    with pytest.raises(VenueError, match="handelsuren|bruikbare"):
        asyncio.run(venue(chart([], [], [], [], [])).candles("GC=F", "1m", 10))


def test_user_agent_is_sent():
    """Zonder herkenbare user-agent weigert Yahoo met 429."""
    v = venue()
    asyncio.run(v.candles("GC=F", "1m", 10))
    assert "Mozilla" in v._session.calls[0]["headers"]["User-Agent"]


# ---------------- spread is een aanname ----------------

def test_zero_spread_means_bid_equals_ask():
    q = asyncio.run(venue(spread=0.0).quote())
    assert q.bid == q.ask == pytest.approx(3300.0)
    assert q.spread == 0.0


def test_assumed_spread_is_applied_symmetrically():
    q = asyncio.run(venue(spread=0.30).quote())
    assert q.spread == pytest.approx(0.30)
    assert q.mid == pytest.approx(3300.0)


def test_venue_declares_spread_is_not_real():
    v = venue(spread=0.25)
    assert v.has_real_spread is False
    assert v.describe()["real_prices"] is True
    assert v.describe()["real_spread"] is False


def test_costs_disabled_flag():
    assert venue(spread=0.0).costs_disabled is True
    assert venue(spread=0.20).costs_disabled is False


def test_cannot_place_orders():
    with pytest.raises(VenueError):
        asyncio.run(venue().place_order("GC=F", "buy", 1.0))


# ---------------- kostenprojectie ----------------

def _trade(gross, volume=0.01, cost=0.0):
    return Trade(run_id=1, mode="paper", symbol="GC=F", side="buy", volume=volume,
                 open_time="2026-08-20T09:00:00+00:00", open_price=3300.0,
                 open_mid=3300.0, open_spread=0.0, gross_pnl=gross,
                 net_pnl=gross - cost, total_cost=cost)


def test_projection_shows_profit_becoming_loss():
    """De kern: winst bij nul kosten hoort zichtbaar om te slaan bij een
    realistische spread."""
    trades = [_trade(0.5) for _ in range(100)]  # 100 x 1 oz, +50 bruto
    rows = cost_projection(trades)
    assert rows[0]["net_pnl"] == pytest.approx(50.0)
    assert rows[0]["profitable"]
    at_35 = next(r for r in rows if r["spread"] == 0.35)
    assert at_35["costs"] == pytest.approx(35.0)
    assert at_35["net_pnl"] == pytest.approx(15.0)


def test_projection_empty_without_trades():
    assert cost_projection([]) == []


def test_gate_blocks_zero_cost_run():
    """Ook met duizenden winstgevende trades."""
    stats = dict(trades=5000, ready_for_live=True, blocking_reasons=[],
                 net_pnl=9999.0, total_costs=0.0, costs_disabled=True)
    run = {"started_at": "2026-01-01T00:00:00+00:00",
           "config_json": json.dumps({"venue": "public_data", "simulated": False})}
    daily = [{"date": f"2026-06-{i+1:02d}", "trades": 40, "net_pnl": 200.0} for i in range(25)]
    result = LiveGate().evaluate(stats, run, daily, {"verdict": "houdbaar", "explanation": "consistent"})
    assert not result.unlocked
    assert result.checks["kosten_meegerekend"] is False
    assert any("kosten" in r for r in result.reasons)


def test_gate_allows_run_with_real_costs():
    stats = dict(trades=600, ready_for_live=True, blocking_reasons=[],
                 net_pnl=1000.0, total_costs=450.0)
    run = {"started_at": "2026-01-01T00:00:00+00:00",
           "config_json": json.dumps({"venue": "oanda", "simulated": False})}
    daily = [{"date": f"2026-06-{i+1:02d}", "trades": 30, "net_pnl": 50.0} for i in range(20)]
    assert LiveGate().evaluate(stats, run, daily, {"verdict": "houdbaar", "explanation": "consistent"}).unlocked


@pytest.mark.parametrize("state,expected", [
    ("REGULAR", True), ("PRE", True), ("POST", True),
    ("CLOSED", False), ("POSTPOST", False), ("PREPRE", False),
])
def test_market_state_maps_to_tradeable(state, expected):
    """Buiten handelsuren is regularMarketTime uren oud. Wordt dat niet als
    'gesloten' herkend, dan slaat de risicobewaking een noodstop."""
    payload = chart([1], [1.0], [2.0], [0.5], [1.5], state=state)
    q = asyncio.run(venue(payload).quote())
    assert q.tradeable is expected


# ---------------- kerncijfers zonder verliezers ----------------

def test_no_losses_gives_no_profit_factor_not_infinity():
    """Infinity is geen geldige JSON en breekt strikte parsers."""
    from gold_scalper.storage.performance import compute
    stats = compute([_trade(1.0) for _ in range(5)], 1000.0)
    assert stats["profit_factor"] is None
    import json
    json.dumps(stats)  # mag niet 'Infinity' bevatten
    assert "Infinity" not in json.dumps(stats)


def test_largest_loss_is_zero_without_losers():
    """min(nets) zou anders de kleinste winst als verlies presenteren."""
    from gold_scalper.storage.performance import compute
    stats = compute([_trade(1.22)], 1000.0)
    assert stats["largest_loss"] == 0.0
    assert stats["largest_win"] == 1.22


def test_flawless_run_is_blocked_not_passed():
    """Nul verliezers over honderden trades wijst op een boekhoudfout."""
    from gold_scalper.storage.performance import compute, verdict
    stats = compute([_trade(1.0) for _ in range(600)], 1000.0)
    result = verdict(stats)
    assert result["verdict"] == "failed"
    assert any("verliezende trade" in r for r in result["blocking_reasons"])


def test_normal_mixed_run_computes_profit_factor():
    from gold_scalper.storage.performance import compute
    trades = [_trade(2.0) for _ in range(6)] + [_trade(-1.0) for _ in range(4)]
    stats = compute(trades, 1000.0)
    assert stats["profit_factor"] == pytest.approx(3.0)
    assert stats["largest_loss"] == -1.0


# ---------------- crumb en verkeersbeperking ----------------

def test_crumb_is_appended_to_requests():
    """Zonder crumb-token weigert Yahoo met 429, ongeacht het pollinterval."""
    v = venue()
    v._crumb = "abc123"
    asyncio.run(v.candles("GC=F", "1m", 10))
    chart_call = next(c for c in v._session.calls if "chart" in c["url"])
    assert chart_call["params"]["crumb"] == "abc123"


def test_429_discards_a_stale_crumb():
    """Een verlopen token moet opnieuw opgehaald worden, niet eindeloos herhaald."""
    v = venue(status=429)
    v._crumb = "oud"
    with pytest.raises(VenueError):
        asyncio.run(v.candles("GC=F", "1m", 10))
    assert v._crumb is None


def test_429_message_points_at_the_real_cause():
    """'Verhoog je interval' was misleidend: het gaat om het ontbrekende token."""
    with pytest.raises(VenueError, match="crumb|cookie"):
        asyncio.run(venue(status=429).candles("GC=F", "1m", 10))


def test_quote_and_candles_share_one_request():
    """Twee verzoeken per cyclus naar een bron die met 429 om zich heen slaat,
    is er één te veel."""
    v = venue()
    v._crumb_failed = True          # sla de handshake over in de test
    asyncio.run(v.candles("GC=F", "1m", 10))
    before = len([c for c in v._session.calls if "chart" in c["url"]])
    asyncio.run(v.quote())
    after = len([c for c in v._session.calls if "chart" in c["url"]])
    assert after == before, "quote() deed een eigen verzoek in plaats van de cache"


def test_cache_expires():
    v = venue()
    v._crumb_failed = True
    v._cache_ttl = 0.0
    asyncio.run(v.candles("GC=F", "1m", 10))
    n1 = len([c for c in v._session.calls if "chart" in c["url"]])
    asyncio.run(v.candles("GC=F", "1m", 10))
    n2 = len([c for c in v._session.calls if "chart" in c["url"]])
    assert n2 > n1
