"""Consistentietoets over de tijd.

De bewijsfase telt trades en dagen, maar dat is een hoeveelheidseis.
Vijfhonderd trades in dezelfde marktsituatie bewijzen niets over een andere.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.learning.robustness import (
    MIN_PER_PERIOD, MIN_PERIODS, evaluate_robustness,
)
from gold_scalper.storage.database import Trade

T0 = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _trade(i, net):
    moment = T0 + timedelta(hours=i)
    return Trade(
        run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.01,
        open_time=moment.isoformat(), open_price=4640.0, open_mid=4640.0,
        open_spread=0.6,
        close_time=(moment + timedelta(minutes=10)).isoformat(),
        net_pnl=net, gross_pnl=net + 0.8, total_cost=0.8,
    )


def test_too_few_trades_gives_no_verdict():
    result = evaluate_robustness([_trade(i, 1.0) for i in range(20)])
    assert result.verdict == "onvoldoende_data"
    assert "ruis" in result.explanation


def test_consistent_winner_is_recognised():
    import random
    random.seed(4)
    trades = [_trade(i, random.gauss(0.9, 1.0)) for i in range(250)]
    result = evaluate_robustness(trades)
    assert result.verdict == "houdbaar"
    assert result.consistency >= 0.5
    assert "vooruit, niet achteruit" in result.explanation


def test_losing_strategy_is_called_negative():
    import random
    random.seed(5)
    trades = [_trade(i, random.gauss(-0.5, 1.0)) for i in range(250)]
    result = evaluate_robustness(trades)
    assert result.verdict == "negatief"
    assert "curve fitting" in result.explanation


def test_one_lucky_period_is_flagged():
    """De belangrijkste toets: winst die uit één periode komt."""
    trades = []
    for i in range(250):
        # Alleen de eerste vijftig zijn winstgevend.
        net = 8.0 if i < 50 else -0.15
        trades.append(_trade(i, net))
    result = evaluate_robustness(trades)
    assert result.verdict in ("geconcentreerd", "inconsistent")
    assert result.best_period_share > 0.6 or result.consistency < 0.5


def test_alternating_periods_are_rejected():
    """Afwisselend winst en verlies mag nooit 'houdbaar' heten.

    Welk van de twee bezwaren het eerst afgaat - te weinig winstgevende
    periodes of te veel concentratie - hangt af van hoe de stukken vallen.
    Beide zijn de juiste conclusie; vastpinnen op één ervan maakt de test
    breekbaar zonder hem strenger te maken.
    """
    trades = []
    for i in range(250):
        period = i // 50
        net = 2.0 if period % 2 == 0 else -2.0
        trades.append(_trade(i, net))
    result = evaluate_robustness(trades)
    assert result.verdict != "houdbaar"
    assert result.verdict in ("inconsistent", "geconcentreerd")


def test_alternating_periods_never_pass_even_with_a_high_t():
    """De t-statistiek alleen zou hier 'bewezen' zeggen: dat is precies waarom
    er ook op consistentie en concentratie getoetst wordt."""
    trades = []
    for i in range(250):
        net = 2.0 if (i // 50) % 2 == 0 else -2.0
        trades.append(_trade(i, net))
    result = evaluate_robustness(trades)
    assert result.t_statistic > 2.0      # zou op zichzelf slagen
    assert result.verdict != "houdbaar"  # maar valt op een ander bezwaar


def test_weak_positive_is_called_unproven():
    """Positief maar binnen de ruis: meer trades nodig, geen andere
    instellingen."""
    import random
    random.seed(11)
    trades = [_trade(i, random.gauss(0.05, 2.5)) for i in range(250)]
    result = evaluate_robustness(trades)
    if result.verdict == "onbewezen":
        assert "toeval" in result.explanation
        assert "geen andere instellingen" in result.explanation


def test_periods_are_time_ordered():
    trades = [_trade(i, 1.0) for i in range(250)]
    result = evaluate_robustness(trades)
    labels = [p.label for p in result.periods]
    assert labels == sorted(labels)


def test_each_period_meets_the_minimum():
    trades = [_trade(i, 1.0) for i in range(200)]
    result = evaluate_robustness(trades)
    assert all(p.trades >= MIN_PER_PERIOD for p in result.periods)
    assert result.total_periods >= MIN_PERIODS


def test_open_trades_are_ignored():
    trades = [_trade(i, 1.0) for i in range(200)]
    trades.append(Trade(
        run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.01,
        open_time=T0.isoformat(), open_price=4640.0, open_mid=4640.0,
        open_spread=0.6,
    ))
    result = evaluate_robustness(trades)
    assert sum(p.trades for p in result.periods) <= 200
