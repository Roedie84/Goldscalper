"""De backtest.

Het belangrijkste ontwerpbesluit: hij bouwt de strategie niet na maar roept
dezelfde evaluate() en ExitManager aan als de live handel. Een backtest die de
strategie herimplementeert, toetst de herimplementatie.
"""
import ast
import asyncio
import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
sys.path.insert(0, str(PKG.parent))

from gold_scalper.analysis.backtest import WARMUP_BARS, run_backtest
from gold_scalper.broker.simulator import SimulatorVenue
from gold_scalper.strategy.scalping import ScalpConfig


@pytest.fixture(scope="module")
def market():
    return asyncio.run(SimulatorVenue(seed=20260825).candles("XAU_USD", "5m", 1200))


def _run(market, **over):
    cfg = ScalpConfig(commission_per_lot_per_side=0.0, volume=0.10, **over)
    return run_backtest(market, cfg, spread=0.60, slippage=0.022, units=10)


def test_uses_the_real_strategy_code():
    """De kern: geen nabouw. Vangt een toekomstige herimplementatie."""
    source = (PKG / "analysis" / "backtest.py").read_text(encoding="utf-8")
    assert "from ..strategy.scalping import ScalpConfig, evaluate" in source
    assert "from ..broker.exits import ExitConfig, ExitManager" in source
    # Geen eigen indicatorberekening
    for verboden in ("def _ema", "def _rsi", "def _bollinger", "def _atr"):
        assert verboden not in source, f"{verboden} is een nabouw"


def test_too_little_data_is_refused(market):
    from gold_scalper.analysis.signals import Candles
    short = Candles(*[list(x)[:50] for x in (
        market.timestamp, market.open, market.high,
        market.low, market.close, market.volume)])
    result = run_backtest(short, ScalpConfig())
    assert result.trades == []
    assert "minstens" in result.warnings[0]


def test_it_produces_trades(market):
    result = _run(market)
    assert result.summary()["trades"] > 0
    assert result.evaluations > 0


def test_costs_are_positive_and_match_the_spread(market):
    """Kosten die negatief uitvallen wijzen op een prijsniveaufout: bruto en
    netto op verschillende niveaus berekenen."""
    summary = _run(market).summary()
    assert summary["total_costs"] > 0
    per_trade = summary["total_costs"] / summary["trades"]
    # spread 0,60 x 10 ounce + slippage
    assert 5.5 <= per_trade <= 7.5, per_trade


def test_gross_minus_costs_equals_net(market):
    summary = _run(market).summary()
    assert summary["gross_pnl"] - summary["total_costs"] == pytest.approx(
        summary["net_pnl"], abs=0.01
    )


def test_wider_spread_costs_more(market):
    cfg = ScalpConfig(commission_per_lot_per_side=0.0, volume=0.10)
    narrow = run_backtest(market, cfg, spread=0.20, slippage=0.0, units=10)
    wide = run_backtest(market, cfg, spread=1.20, slippage=0.0, units=10)
    if narrow.summary()["trades"] and wide.summary()["trades"]:
        assert (
            wide.summary()["total_costs"] / wide.summary()["trades"]
            > narrow.summary()["total_costs"] / narrow.summary()["trades"]
        )


def test_stops_are_checked_against_bar_extremes():
    """Op de slotkoers toetsen mist stops die geraakt werden en daarna
    herstelden - allemaal in je voordeel."""
    source = (PKG / "analysis" / "backtest.py").read_text(encoding="utf-8")
    body = source.split("def run_backtest")[1]
    assert "candles.high[nxt]" in body and "candles.low[nxt]" in body


def test_stop_wins_when_both_are_hit_in_one_bar():
    """Uit een candle valt niet af te leiden welke eerst kwam. Gokken op de
    gunstige volgorde is precies hoe een backtest zichzelf rijk rekent."""
    source = (PKG / "analysis" / "backtest.py").read_text(encoding="utf-8")
    body = source.split("def run_backtest")[1]
    stop_index = body.index("if stop_hit:")
    target_index = body.index("elif target_hit:")
    assert stop_index < target_index


def test_rejections_are_counted(market):
    summary = _run(market).summary()
    assert summary["rejections"]
    assert sum(summary["rejections"].values()) > 0


def test_warmup_is_respected(market):
    result = _run(market)
    if result.trades:
        first = result.trades[0].opened_at
        assert first >= market.timestamp[WARMUP_BARS]


def test_open_position_at_the_end_is_flagged(market):
    result = _run(market)
    # Niet altijd het geval, maar als het gebeurt moet het gemeld worden.
    if result.warnings:
        assert any("open" in w for w in result.warnings)
