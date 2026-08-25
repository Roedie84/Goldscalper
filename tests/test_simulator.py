"""Simulator: realisme, reproduceerbaarheid, en de vergrendeling."""
import asyncio, json, os, statistics, sys
from datetime import datetime, timezone
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.volatility import atr
from gold_scalper.broker.adapter import VenueError
from gold_scalper.broker.simulator import SimulatorVenue
from gold_scalper.modes import LiveGate


@pytest.fixture(scope="module")
def venue():
    return SimulatorVenue()


# ---------------- realisme ----------------

def test_m1_atr_matches_real_gold(venue):
    """Te lage ATR maakt de kostenpoort betekenisloos; te hoge maakt hem te mild."""
    cd = asyncio.run(venue.candles("XAU_USD", "1m", 1440))
    values = [v for v in atr(cd, 14) if v is not None]
    assert 0.25 <= statistics.median(values) <= 0.60


def test_daily_range_matches_real_gold(venue):
    cd = asyncio.run(venue.candles("XAU_USD", "1d", 30))
    ranges = [cd.high[i] - cd.low[i] for i in range(len(cd))]
    assert 15.0 <= statistics.median(ranges) <= 55.0


def test_candles_are_internally_consistent(venue):
    cd = asyncio.run(venue.candles("XAU_USD", "1m", 200))
    cd.validate()  # high>=low, close binnen range, timestamps oplopend


def test_history_is_reproducible(venue):
    """Een geschiedenis die per aanroep verandert maakt indicatoren onzin."""
    a = asyncio.run(venue.candles("XAU_USD", "1m", 300))
    b = asyncio.run(venue.candles("XAU_USD", "1m", 300))
    assert a.close == b.close and a.timestamp == b.timestamp


def test_different_seeds_give_different_markets():
    a = asyncio.run(SimulatorVenue(seed=1).candles("XAU_USD", "1m", 200))
    b = asyncio.run(SimulatorVenue(seed=2).candles("XAU_USD", "1m", 200))
    assert a.close != b.close


def test_spread_is_wider_outside_active_hours(venue):
    base = int(datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc).timestamp())
    quiet = venue.spread_at(base + 2 * 3600)     # Aziatische nacht
    active = venue.spread_at(base + 15 * 3600)   # Londen/New York
    assert quiet > active


def test_market_closed_on_saturday(venue):
    saturday = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    assert not venue._is_market_open(saturday)
    wednesday = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    assert venue._is_market_open(wednesday)


def test_configurable_spread_reaches_quote():
    wide = SimulatorVenue(spread=0.50)
    narrow = SimulatorVenue(spread=0.10)
    base = int(datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc).timestamp())
    assert wide.spread_at(base) > narrow.spread_at(base) * 3


# ---------------- vergrendeling ----------------

def test_simulator_refuses_to_place_orders(venue):
    with pytest.raises(VenueError):
        asyncio.run(venue.place_order("XAU_USD", "buy", 1.0))


def test_simulator_reports_it_is_simulated(venue):
    assert venue.describe()["simulated"] is True
    assert venue.supports_trading is False


def test_gate_blocks_simulated_run_even_when_profitable():
    """De kern: een geslaagde simulatie mag nooit echt geld vrijgeven."""
    stats = dict(trades=5000, ready_for_live=True, blocking_reasons=[],
                 net_pnl=9999.0, total_costs=2000.0)
    run = {
        "started_at": "2026-01-01T00:00:00+00:00",
        "config_json": json.dumps({"venue": "simulator", "simulated": True}),
    }
    daily = [{"date": f"2026-06-{i+1:02d}", "trades": 40, "net_pnl": 200.0} for i in range(25)]
    result = LiveGate().evaluate(stats, run, daily, {"verdict": "houdbaar", "explanation": "consistent"})
    assert not result.unlocked
    assert result.checks["echte_marktdata"] is False
    assert any("simulator" in r for r in result.reasons)


def test_gate_allows_real_data_run():
    stats = dict(trades=600, ready_for_live=True, blocking_reasons=[],
                 net_pnl=1000.0, total_costs=450.0)
    run = {
        "started_at": "2026-01-01T00:00:00+00:00",
        "config_json": json.dumps({"venue": "oanda", "simulated": False}),
    }
    daily = [{"date": f"2026-06-{i+1:02d}", "trades": 30, "net_pnl": 50.0} for i in range(20)]
    assert LiveGate().evaluate(stats, run, daily, {"verdict": "houdbaar", "explanation": "consistent"}).unlocked


def test_gate_blocks_when_config_unparseable_but_mentions_simulator():
    run = {"started_at": "2026-01-01T00:00:00+00:00", "config_json": "venue=SIMULATOR"}
    stats = dict(trades=600, ready_for_live=True, blocking_reasons=[],
                 net_pnl=100.0, total_costs=80.0)
    daily = [{"date": f"2026-06-{i+1:02d}", "trades": 30, "net_pnl": 5.0} for i in range(20)]
    assert not LiveGate().evaluate(stats, run, daily, {"verdict": "houdbaar", "explanation": "consistent"}).unlocked
