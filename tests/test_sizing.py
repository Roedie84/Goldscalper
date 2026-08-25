"""Positiegrootte.

Een vaste ordergrootte laat je risico per trade meebewegen met de
volatiliteit: bij een ATR van 2 riskeer je met tien ounce twintig dollar, bij
een ATR van 12 honderdtwintig. Dezelfde beslissing, zes keer zo groot gevolg,
zonder dat je het koos.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.strategy.sizing import SizingConfig, position_size


def _size(cfg, entry=4640.0, stop=4634.0, score=0.6, equity=10000.0):
    return position_size(cfg, equity, entry, stop, score)


def test_fixed_mode_returns_the_configured_size():
    result = _size(SizingConfig(fixed_units=2.0))
    assert result.units == 2.0


def test_fixed_mode_is_still_capped():
    result = _size(SizingConfig(fixed_units=99.0, max_units=5.0))
    assert result.units == 5.0


def test_fixed_mode_reports_the_varying_risk():
    """Het punt: bij vaste grootte varieert je risico met de stopafstand."""
    narrow = _size(SizingConfig(fixed_units=2.0), stop=4638.0)   # 2 USD
    wide = _size(SizingConfig(fixed_units=2.0), stop=4628.0)     # 12 USD
    assert wide.risk_amount == pytest.approx(narrow.risk_amount * 6)


# ---------------- risicogestuurd ----------------

def test_risk_based_keeps_the_loss_constant():
    """De kern: dezelfde inzet in euro's, ongeacht de volatiliteit."""
    cfg = SizingConfig(risk_based=True, risk_per_trade_pct=0.5, max_units=50.0)
    narrow = _size(cfg, stop=4638.0)    # stop op 2 USD
    wide = _size(cfg, stop=4628.0)      # stop op 12 USD

    assert narrow.units > wide.units
    assert narrow.risk_amount == pytest.approx(50.0, rel=0.02)
    assert wide.risk_amount == pytest.approx(50.0, rel=0.02)


def test_risk_percentage_is_respected():
    cfg = SizingConfig(risk_based=True, risk_per_trade_pct=1.0, max_units=50.0)
    result = _size(cfg, equity=20000.0)
    assert result.risk_amount == pytest.approx(200.0, rel=0.02)


def test_shrinking_equity_shrinks_the_position():
    """Na verliezen automatisch kleiner: dat is wat een vaste grootte niet doet."""
    cfg = SizingConfig(risk_based=True, max_units=50.0)
    healthy = _size(cfg, equity=10000.0)
    battered = _size(cfg, equity=5000.0)
    assert battered.units == pytest.approx(healthy.units / 2, rel=0.02)


def test_hard_cap_still_applies():
    cfg = SizingConfig(risk_based=True, risk_per_trade_pct=5.0, max_units=3.0)
    result = _size(cfg, stop=4639.9)
    assert result.units == 3.0
    assert result.capped_by == "max_units"


def test_zero_stop_distance_falls_back_safely():
    cfg = SizingConfig(risk_based=True)
    result = _size(cfg, entry=4640.0, stop=4640.0)
    assert result.units == cfg.min_units
    assert result.capped_by == "geen_stopafstand"


# ---------------- schalen met signaalsterkte ----------------

def test_confidence_scaling_is_off_by_default():
    assert SizingConfig().scale_with_confidence is False


def test_stronger_signal_gives_a_larger_position():
    cfg = SizingConfig(risk_based=True, scale_with_confidence=True, max_units=50.0)
    weak = _size(cfg, score=0.45)
    strong = _size(cfg, score=1.0)
    assert strong.units > weak.units


def test_scaling_is_capped():
    """Boven anderhalf wordt één sterk signaal bepalend voor je resultaat, en
    dan is het geen systeem meer maar een gok."""
    cfg = SizingConfig(risk_based=True, scale_with_confidence=True,
                       max_confidence_multiple=1.5, max_units=500.0)
    base = _size(cfg, score=0.45)
    extreme = _size(cfg, score=5.0)
    assert extreme.units <= base.units * 1.5 * 1.01


def test_score_at_the_threshold_gets_no_bonus():
    cfg = SizingConfig(risk_based=True, scale_with_confidence=True, max_units=500.0)
    plain = _size(SizingConfig(risk_based=True, max_units=500.0), score=0.45)
    scaled = _size(cfg, score=0.45)
    assert scaled.units == pytest.approx(plain.units, rel=0.01)


def test_reason_explains_the_calculation():
    cfg = SizingConfig(risk_based=True, scale_with_confidence=True, max_units=50.0)
    result = _size(cfg, score=0.9)
    assert "budget" in result.reason
    assert "signaal" in result.reason


def test_default_risk_is_conservative():
    """Bij twintig verliezers op rij ben je tien procent kwijt: pijnlijk maar
    overleefbaar. Boven twee procent wordt dat existentieel."""
    assert SizingConfig().risk_per_trade_pct <= 1.0
