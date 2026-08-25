"""Verliezen ontleden.

De module leert bewust niet welke omstandigheden vermeden moeten worden: bij
een trefkans van 40% zijn verliezers noodzakelijk, en ze wegfilteren haalt ook
de winnaars weg. Wat hij wel doet is onderscheiden tussen pech en een fout in
het exitontwerp.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.learning.postmortem import (
    COSTS_ATE_IT, HELD_TOO_SHORT, MIN_PATTERN, NO_FOLLOW_THROUGH,
    STOP_TOO_TIGHT, WRONG_DIRECTION, analyse_losses,
)
from gold_scalper.storage.database import Trade


def _loss(i, *, net=-1.0, gross=-0.4, mfe=0.1, mae=-1.0, reason="stop_loss"):
    return Trade(
        run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.01,
        open_time=f"2026-08-25T10:{i % 60:02d}:00+00:00", open_price=4640.0,
        open_mid=4640.0, open_spread=0.6,
        close_time=f"2026-08-25T10:{i % 60:02d}:30+00:00",
        net_pnl=net, gross_pnl=gross, total_cost=gross - net,
        mfe=mfe, mae=mae, close_reason=reason,
    )


def test_too_few_losses_yields_no_pattern():
    result = analyse_losses([_loss(i) for i in range(5)])
    assert result.patterns == []
    assert "toeval" in result.conclusion


def test_stop_too_tight_is_recognised():
    """Uitgestopt en daarna alsnog richting het doel: dat is ontwerp, geen pech."""
    losers = [_loss(i, mfe=1.6, mae=-1.0, reason="stop_loss") for i in range(20)]
    result = analyse_losses(losers, typical_atr=1.0)
    assert result.patterns[0].cause == STOP_TOO_TIGHT
    assert result.patterns[0].actionable
    assert "stop_loss_atr" in result.patterns[0].suggestion


def test_costs_are_separated_from_strategy_failure():
    """Bruto positief maar netto negatief is geen strategiefout."""
    losers = [_loss(i, net=-0.3, gross=0.5, mfe=0.6, mae=-0.2) for i in range(20)]
    result = analyse_losses(losers, typical_atr=1.0)
    assert result.patterns[0].cause == COSTS_ATE_IT
    assert "kostenprobleem" in result.patterns[0].explanation


def test_timeout_near_target_flags_hold_time():
    losers = [_loss(i, mfe=1.2, mae=-0.3, reason="timeout") for i in range(20)]
    result = analyse_losses(losers, typical_atr=1.0)
    assert result.patterns[0].cause == HELD_TOO_SHORT
    assert "max_hold_seconds" in result.patterns[0].suggestion


def test_no_movement_is_a_signal_quality_issue():
    losers = [_loss(i, mfe=0.1, mae=-0.1, reason="timeout") for i in range(20)]
    result = analyse_losses(losers, typical_atr=1.0)
    assert result.patterns[0].cause == NO_FOLLOW_THROUGH
    assert not result.patterns[0].actionable


def test_ordinary_losses_are_not_dressed_up_as_fixable():
    """De belangrijkste eigenschap: gewone marktbeweging niet presenteren als
    iets dat je kunt oplossen."""
    losers = [_loss(i, mfe=0.2, mae=-1.5, reason="stop_loss") for i in range(30)]
    result = analyse_losses(losers, typical_atr=1.0)
    assert result.patterns[0].cause == WRONG_DIRECTION
    assert result.fixable_share < 0.25
    assert "onvermijdelijke" in result.conclusion


def test_mixed_causes_are_ranked():
    losers = (
        [_loss(i, mfe=1.6, reason="stop_loss") for i in range(20)]
        + [_loss(i + 100, mfe=0.2, mae=-1.5) for i in range(10)]
    )
    result = analyse_losses(losers, typical_atr=1.0)
    assert result.patterns[0].count > result.patterns[1].count
    assert 0.5 < result.fixable_share < 0.8


def test_suggestion_only_for_meaningful_share():
    """Een advies bij drie procent van de verliezen is ruis."""
    losers = (
        [_loss(i, mfe=1.6, reason="stop_loss") for i in range(2)]
        + [_loss(i + 100, mfe=0.2, mae=-1.5) for i in range(30)]
    )
    result = analyse_losses(losers, typical_atr=1.0)
    tight = next(p for p in result.patterns if p.cause == STOP_TOO_TIGHT)
    assert tight.suggestion is None


def test_it_never_suggests_avoiding_conditions():
    """De val die deze module bewust vermijdt."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "learning" / "postmortem.py").read_text(encoding="utf-8")
    for forbidden in ("blacklist", "avoid_hour", "exclude_regime", "skip_setup"):
        assert forbidden not in source
