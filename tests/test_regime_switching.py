"""Trend en mean reversion mogen elkaar niet opheffen.

De oorspronkelijke score telde een trendvolgende en een contraire component
op. Die correleren -0,78, dus ze hieven elkaar grotendeels op en de
samengestelde score haalde zelden een drempel. Het gevolg was één trade in ruim
drie uur - geen selectiviteit maar besluiteloosheid.
"""
import asyncio
import os
import statistics
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles
from gold_scalper.broker.simulator import SimulatorVenue
from gold_scalper.strategy.scalping import ScalpConfig, evaluate


@pytest.fixture(scope="module")
def market():
    venue = SimulatorVenue(seed=20260823, spread=0.2)
    return asyncio.run(venue.candles("XAU_USD", "1m", 1200))


def _scores(market, regime_switching: bool) -> list[float]:
    cfg = ScalpConfig(
        commission_per_lot_per_side=0.0, volume=0.01,
        regime_switching=regime_switching, entry_threshold=0.0,
        trading_hours_utc=(0, 24), max_spread=9.0,
    )
    out = []
    for i in range(400, 1100, 3):
        w = slice(i - 300, i)
        candles = Candles(
            market.timestamp[w], market.open[w], market.high[w],
            market.low[w], market.close[w], market.volume[w],
        )
        price = market.close[w][-1]
        signal = evaluate(candles, price - 0.1, price + 0.1, cfg, 12, 0, 1e9)
        out.append(signal.score)
    return out


def test_regime_switching_produces_stronger_scores(market):
    """Niet 'meer trades is beter', maar: de score wordt niet langer door
    twee tegengestelde componenten naar nul getrokken."""
    additive = [abs(s) for s in _scores(market, False)]
    switched = [abs(s) for s in _scores(market, True)]
    assert statistics.median(switched) > statistics.median(additive)


def test_regime_switching_passes_threshold_more_often(market):
    additive = sum(1 for s in _scores(market, False) if abs(s) >= 0.45)
    switched = sum(1 for s in _scores(market, True) if abs(s) >= 0.45)
    assert switched > additive * 2


def test_regime_is_reported_in_components(market):
    """Je moet kunnen zien welk verhaal de bot volgde bij een trade."""
    cfg = ScalpConfig(commission_per_lot_per_side=0.0, volume=0.01,
                      regime_switching=True, entry_threshold=0.0,
                      trading_hours_utc=(0, 24), max_spread=9.0)
    w = slice(700, 1000)
    candles = Candles(market.timestamp[w], market.open[w], market.high[w],
                      market.low[w], market.close[w], market.volume[w])
    price = market.close[w][-1]
    signal = evaluate(candles, price - 0.1, price + 0.1, cfg, 12, 0, 1e9)
    assert signal.components["regime"] in ("trend", "range")
    assert "adx" in signal.components


def test_additive_mode_still_available(market):
    """Bewaard voor vergelijking; de oude opzet mag niet stilzwijgend verdwijnen."""
    scores = _scores(market, False)
    assert len(scores) > 0
    assert all(-1.0 <= s <= 1.0 for s in scores)


def test_regime_switching_is_the_default():
    assert ScalpConfig().regime_switching is True
