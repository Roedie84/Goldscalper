"""Handelen wanneer de markt open is, niet wanneer de klok het toestaat.

Het vaste tijdvenster was een proxy voor iets dat directer meetbaar is: buiten
de Londen/New York-overlap is de spread breder en de beweging kleiner. Dat
filteren max_spread en de volatiliteitscontrole al, en die kijken naar wat er
werkelijk gebeurt in plaats van naar het tijdstip.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles
from gold_scalper.broker.simulator import SimulatorVenue
from gold_scalper.strategy.scalping import ScalpConfig, evaluate


@pytest.fixture(scope="module")
def market():
    return asyncio.run(SimulatorVenue(seed=20260823, spread=0.2).candles(
        "XAU_USD", "1m", 900
    ))


def _evaluate(market, hour, **over):
    cfg = ScalpConfig(commission_per_lot_per_side=0.0, volume=0.01,
                      max_spread=0.45, **over)
    w = slice(500, 800)
    candles = Candles(market.timestamp[w], market.open[w], market.high[w],
                      market.low[w], market.close[w], market.volume[w])
    price = market.close[w][-1]
    return evaluate(candles, price - 0.1, price + 0.1, cfg, hour, 0, 1e9)


def test_no_time_window_by_default(market):
    """Om drie uur 's nachts mag er gehandeld worden als de markt open is."""
    signal = _evaluate(market, hour=3)
    assert signal.reject_reason != "outside_hours"


def test_window_can_be_enabled(market):
    signal = _evaluate(market, hour=3, enforce_trading_hours=True,
                       trading_hours_utc=(7, 20))
    assert signal.reject_reason == "outside_hours"


def test_window_allows_hours_inside_it(market):
    signal = _evaluate(market, hour=12, enforce_trading_hours=True,
                       trading_hours_utc=(7, 20))
    assert signal.reject_reason != "outside_hours"


def test_default_config_has_hours_off():
    cfg = ScalpConfig()
    assert cfg.enforce_trading_hours is False


# ---------------- de vervanging van het tijdvenster ----------------

def test_quiet_market_is_rejected_on_volatility_not_clock(market):
    """Zonder tijdvenster moet stilte gefilterd worden op gemeten beweging."""
    flat = Candles(
        list(range(300)), [3300.0] * 300, [3300.05] * 300,
        [3299.95] * 300, [3300.0] * 300, [10.0] * 300,
    )
    cfg = ScalpConfig(commission_per_lot_per_side=0.0, volume=0.01,
                      enforce_trading_hours=False, real_spread=False)
    signal = evaluate(flat, 3299.9, 3300.1, cfg, 3, 0, 1e9)
    assert not signal.should_trade


def test_assumed_spread_without_window_tightens_the_filter():
    """Bij een aangenomen spread filtert max_spread niets, want het is een
    vergelijking met een constante. De volatiliteitsdrempel vervangt dat."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "strategy" / "scalping.py").read_text()
    body = source.split("def evaluate(")[1]
    assert "quiet_floor" in body
    assert "not cfg.real_spread and not cfg.enforce_trading_hours" in body


def test_real_spread_keeps_the_normal_floor(market):
    """Met een gemeten spread is de strengere drempel niet nodig."""
    lenient = _evaluate(market, hour=3, real_spread=True)
    strict = _evaluate(market, hour=3, real_spread=False)
    # Beide moeten een uitkomst geven; de strenge mag niet mílder zijn.
    assert lenient.components["volatility"] >= strict.components["volatility"] - 1e-9


def test_venues_declare_whether_spread_is_measured():
    from gold_scalper.broker.simulator import SimulatorVenue as Sim
    assert Sim().has_real_spread is False


# ---------------- spread relatief aan volatiliteit ----------------

def test_spread_limit_scales_with_volatility():
    """Een absolute grens is betekenisloos zonder de volatiliteit erbij.

    Bij goud op 3300 met ATR 1,65 is een spread van 0,30 hetzelfde verhaal als
    0,77 bij goud op 4642 met ATR 4,22. Een vaste grens van 0,30 weigerde 1098
    evaluaties op rij zodra de eerste echte brokerdata binnenkwam.
    """
    from gold_scalper.analysis.signals import Candles

    def market(atr_target: float, price: float) -> Candles:
        n = 300
        highs, lows, closes, opens = [], [], [], []
        for i in range(n):
            base = price + (i % 7 - 3) * atr_target * 0.4
            opens.append(base)
            closes.append(base + atr_target * 0.1)
            highs.append(base + atr_target * 0.6)
            lows.append(base - atr_target * 0.6)
        return Candles(list(range(n)), opens, highs, lows, closes, [10.0] * n)

    cfg = ScalpConfig(commission_per_lot_per_side=0.0, volume=0.01,
                      max_spread_atr_ratio=0.35, max_spread=9.0)

    # Rustige markt: spread 0,60 is te breed.
    quiet = market(1.0, 3300.0)
    narrow = evaluate(quiet, 3300.0 - 0.30, 3300.0 + 0.30, cfg, 12, 0, 1e9)
    # Beweeglijke markt: dezelfde spread valt in het niet.
    lively = market(4.2, 4642.0)
    wide = evaluate(lively, 4642.0 - 0.30, 4642.0 + 0.30, cfg, 12, 0, 1e9)

    assert narrow.reject_reason == "spread_too_wide"
    assert wide.reject_reason != "spread_too_wide"


def test_absolute_limit_still_catches_absurd_spreads():
    """Vangnet voor het geval de ATR zelf onbetrouwbaar is."""
    from gold_scalper.analysis.signals import Candles

    candles = Candles(list(range(300)), [4642.0] * 300, [4650.0] * 300,
                      [4634.0] * 300, [4642.0] * 300, [10.0] * 300)
    cfg = ScalpConfig(commission_per_lot_per_side=0.0, volume=0.01,
                      max_spread=3.0, max_spread_atr_ratio=1.0)
    signal = evaluate(candles, 4600.0, 4700.0, cfg, 12, 0, 1e9)
    assert signal.reject_reason == "spread_too_wide"
    assert "absolute" in signal.reason
