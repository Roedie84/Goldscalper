"""Waarschuwen als de stop groter is dan het doel.

Een doel van 5 tegen een stop van 6,5 vereist 64% winnaars om quitte te spelen,
tegen 45% bij de standaardverhouding. Dat mag - er zijn strategieën die van een
hoge trefkans leven - maar wie die keuze maakt hoort het getal te zien in plaats
van het te ontdekken na tweehonderd trades.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.validation import reward_risk_warning

warn = reward_risk_warning


def _input(**over):
    base = {
        "take_profit_atr": 1.5, "stop_loss_atr": 1.0,
        "take_profit_usd": 0.0, "stop_loss_usd": 0.0,
    }
    base.update(over)
    return base


def test_default_ratio_is_not_flagged():
    assert warn(_input(), atr=6.0) is None


def test_fixed_target_below_atr_stop_is_flagged():
    """Precies de instelling die aanleiding gaf tot deze controle."""
    message = warn(_input(take_profit_usd=5.0), atr=6.038)
    assert message is not None
    assert "kleiner dan je stop" in message
    assert "%" in message


def test_the_required_hit_rate_is_stated():
    message = warn(_input(take_profit_usd=5.0, stop_loss_usd=10.0), atr=6.0)
    # 10 / (5 + 10) = 66,7%
    assert "67%" in message


def test_both_fixed_and_sane_is_not_flagged():
    assert warn(_input(take_profit_usd=10.0, stop_loss_usd=5.0), atr=6.0) is None


def test_equal_target_and_stop_is_not_flagged():
    """Eén op één is een verdedigbare keuze, geen waarschuwing waard."""
    assert warn(_input(take_profit_usd=6.0, stop_loss_usd=6.0), atr=6.0) is None


def test_atr_multipliers_can_also_be_upside_down():
    assert warn(_input(take_profit_atr=0.8, stop_loss_atr=1.5), atr=6.0) is not None


def test_mixed_units_without_atr_stays_silent():
    """Een vast bedrag en een ATR-multiplier zijn onvergelijkbaar zonder de
    ATR; dan liever niets zeggen dan iets verkeerds."""
    assert warn(_input(take_profit_usd=5.0), atr=None) is None


def test_zero_values_do_not_crash():
    assert warn(_input(take_profit_atr=0, stop_loss_atr=0), atr=0) is None
    assert warn({}, atr=6.0) is None


def test_warning_is_shown_in_the_form_not_only_logged():
    """Alleen in het logboek zetten helpt niemand die het scherm gebruikt."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "config_flow.py").read_text(encoding="utf-8")
    assert "_pending_warning" in source
    assert "description_placeholders" in source
