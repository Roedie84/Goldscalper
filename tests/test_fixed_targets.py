"""Doel en stop als vast bedrag in plaats van een veelvoud van de ATR.

Vaste afstanden passen zich niet aan de markt aan, en dat snijdt twee kanten
op: bij een rustige markt haal je het doel nooit, bij een onrustige markt word
je uit de stop geschud. De ATR-variant blijft daarom de standaard.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles
from gold_scalper.strategy.scalping import ScalpConfig, evaluate


def _market(atr_target: float, price: float = 4630.0) -> Candles:
    n = 300
    opens, highs, lows, closes = [], [], [], []
    for i in range(n):
        base = price + i * atr_target * 0.15
        opens.append(base)
        closes.append(base + atr_target * 0.5)
        highs.append(base + atr_target * 0.7)
        lows.append(base - atr_target * 0.3)
    return Candles(list(range(n)), opens, highs, lows, closes, [10.0] * n)


def _signal(cfg, atr_target=6.0):
    candles = _market(atr_target)
    price = candles.close[-1]
    return evaluate(candles, price - 0.3, price + 0.3, cfg, 12, 0, 1e9)


def _cfg(**over):
    base = dict(commission_per_lot_per_side=0.0, volume=0.01,
                entry_threshold=0.0, max_spread=9.0, max_spread_atr_ratio=1.0,
                min_edge_multiple=0.1)
    base.update(over)
    return ScalpConfig(**base)


def test_default_scales_with_volatility():
    quiet = _signal(_cfg(), atr_target=2.0)
    lively = _signal(_cfg(), atr_target=12.0)
    assert lively.components["target_usd"] > quiet.components["target_usd"] * 3


def test_fixed_target_overrides_the_atr():
    signal = _signal(_cfg(take_profit_usd=10.0, stop_loss_usd=4.0))
    assert signal.components["target_usd"] == pytest.approx(10.0)
    assert signal.components["stop_usd"] == pytest.approx(4.0)


def test_fixed_target_does_not_scale():
    """Precies de eigenschap waar je op moet letten."""
    quiet = _signal(_cfg(take_profit_usd=10.0), atr_target=2.0)
    lively = _signal(_cfg(take_profit_usd=10.0), atr_target=12.0)
    assert quiet.components["target_usd"] == lively.components["target_usd"]


def test_zero_means_use_the_atr():
    signal = _signal(_cfg(take_profit_usd=0.0, stop_loss_usd=0.0), atr_target=6.0)
    assert signal.components["target_usd"] > 1.0
    assert signal.components["target_usd"] != 10.0


def test_only_target_fixed_leaves_stop_dynamic():
    """De twee zijn onafhankelijk instelbaar."""
    signal = _signal(_cfg(take_profit_usd=10.0), atr_target=6.0)
    assert signal.components["target_usd"] == pytest.approx(10.0)
    assert signal.components["stop_usd"] != pytest.approx(10.0)


def test_levels_land_on_the_right_side_of_the_entry():
    """Bij een long ligt het doel boven en de stop onder; bij een short
    andersom. De instapprijs zit niet in het signaal, dus die wordt uit de
    quote afgeleid zoals de strategie dat ook doet."""
    candles = _market(6.0)
    price = candles.close[-1]
    bid, ask = price - 0.3, price + 0.3
    signal = evaluate(
        candles, bid, ask,
        _cfg(take_profit_usd=10.0, stop_loss_usd=4.0), 12, 0, 1e9,
    )
    if not signal.should_trade:
        pytest.skip("geen signaal in deze marktopzet")

    entry = ask if signal.direction == 1 else bid
    if signal.direction == 1:
        assert signal.take_profit > entry > signal.stop_loss
    else:
        assert signal.take_profit < entry < signal.stop_loss


def test_distances_match_the_configured_amounts():
    candles = _market(6.0)
    price = candles.close[-1]
    bid, ask = price - 0.3, price + 0.3
    signal = evaluate(
        candles, bid, ask,
        _cfg(take_profit_usd=10.0, stop_loss_usd=4.0), 12, 0, 1e9,
    )
    if not signal.should_trade:
        pytest.skip("geen signaal in deze marktopzet")

    entry = ask if signal.direction == 1 else bid
    assert abs(signal.take_profit - entry) == pytest.approx(10.0, abs=0.01)
    assert abs(signal.stop_loss - entry) == pytest.approx(4.0, abs=0.01)


def test_changing_the_target_starts_a_new_run():
    """Andere doelen betekenen andere trades; die horen niet in één
    bewijsfase."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    body = source.split("def _fingerprint_material(")[1].split("\n    @staticmethod")[0]
    assert '"take_profit"' in body
    assert '"stop_loss"' in body
