"""Het teken van elke component moet kloppen met wat hij belooft.

Een indicator die "momentum" heet maar contrair rekent, duwt de score tegen de
trend in zodra hij in de trendmodus wordt meegeteld. Dat leverde 33% winnaars
op waar willekeurig instappen er 43% haalt - slechter dan een muntje opgooien,
en het bleef maandenlang onzichtbaar omdat de naam klopte met de bedoeling en
niet met de code.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.strategy.scalping import (
    _micro_trend, _rsi_momentum, _rsi_reversion, _stretch,
)

STIJGEND = [4600.0 + i * 0.5 for i in range(300)]
DALEND = [4750.0 - i * 0.5 for i in range(300)]
VLAK = [4650.0 + (0.05 if i % 2 else -0.05) for i in range(300)]


def test_trend_follows_the_direction():
    assert _micro_trend(STIJGEND)[0] > 0
    assert _micro_trend(DALEND)[0] < 0


def test_stretch_opposes_the_direction():
    """Mean reversion: ver doorgeschoten omhoog betekent verwachte daling."""
    assert _stretch(STIJGEND)[0] < 0
    assert _stretch(DALEND)[0] > 0


def test_reversion_opposes_the_direction():
    """De naam zegt wat hij doet, en dat is het punt."""
    assert _rsi_reversion(STIJGEND)[0] < 0
    assert _rsi_reversion(DALEND)[0] > 0


def test_momentum_confirms_the_direction():
    """Momentum bevestigt; dat is wat het woord betekent. Deze functie heette
    eerder _momentum terwijl hij contrair rekende."""
    assert _rsi_momentum(STIJGEND)[0] > 0
    assert _rsi_momentum(DALEND)[0] < 0


def test_the_two_rsi_readings_are_exact_mirrors():
    for serie in (STIJGEND, DALEND, VLAK):
        assert _rsi_momentum(serie)[0] == pytest.approx(-_rsi_reversion(serie)[0])


def test_notes_state_which_reading_is_used():
    """In het logboek moet te zien zijn welke lezing gold; anders is een
    tekenfout achteraf niet te vinden."""
    assert "contrair" in _rsi_reversion(STIJGEND)[1]
    assert "bevestigend" in _rsi_momentum(STIJGEND)[1]


def test_trend_mode_uses_the_confirming_reading():
    """In een trend hoort de RSI de beweging te bevestigen. Hier stond de
    contraire variant, die de score tegen de trend in duwde."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "strategy" / "scalping.py").read_text(encoding="utf-8")
    blok = source.split("if trending:")[1].split("else:")[0]
    assert "-reversion_score" in blok, "trendmodus gebruikt de contraire lezing"


def test_range_mode_also_uses_the_confirming_reading():
    """Gemeten haalt de contraire lezing 38% en de bevestigende 50%, over
    vierhonderd waarnemingen. En twee indicatoren die hetzelfde zeggen voegen
    niets toe; ze verdubbelen het gewicht van één idee."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "strategy" / "scalping.py").read_text(encoding="utf-8")
    blok = source.split("if trending:")[1].split("leading, supporting = stretch_score")[0]
    else_blok = blok.split("else:")[1]
    assert "-reversion_score" in else_blok
