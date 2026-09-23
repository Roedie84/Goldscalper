"""De leerlaag.

Kernonderscheid: metingen mogen automatisch worden toegepast, parameter-
optimalisatie nooit. Een bot die zijn drempel bijstelt na een slechte week past
zich aan de ruis van die week aan, en wordt daarmee niet beter maar instabieler.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.learning.analysis import (
    MIN_OBSERVATIONS, evaluate_threshold, measure_execution, regime_performance,
)
from gold_scalper.storage.database import Trade

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


def _trade(i, net, *, slippage=0.02, spread=0.30, reason="take_profit",
           score=0.5, regime="trend"):
    moment = T0 + timedelta(minutes=i * 7)
    return Trade(
        run_id=1, mode="paper", symbol="GOLD", side="buy", volume=0.01,
        open_time=moment.isoformat(), open_price=4650.0, open_mid=4650.0,
        open_spread=spread, open_slippage=slippage,
        close_time=(moment + timedelta(minutes=3)).isoformat(),
        close_slippage=slippage, close_reason=reason,
        net_pnl=net, gross_pnl=net + 0.2, total_cost=0.2,
        signal_score=score, regime=regime,
    )


# ---------------- metingen ----------------

def test_too_few_trades_yields_no_conclusions():
    facts = measure_execution([_trade(i, 1.0) for i in range(10)])
    assert facts.measured_slippage is None
    assert "minimaal" in facts.notes[0]


def test_slippage_is_measured_against_the_assumption():
    facts = measure_execution(
        [_trade(i, 1.0, slippage=0.06) for i in range(50)], assumed_slippage=0.02
    )
    assert facts.measured_slippage == pytest.approx(0.06)
    assert facts.slippage_ratio == pytest.approx(3.0)
    assert any("te optimistisch" in n for n in facts.notes)


def test_lower_slippage_than_assumed_is_also_reported():
    facts = measure_execution(
        [_trade(i, 1.0, slippage=0.005) for i in range(50)], assumed_slippage=0.02
    )
    assert any("strenger dan nodig" in n for n in facts.notes)


def test_spread_is_grouped_by_hour():
    trades = [_trade(i, 1.0, spread=0.3 + (i % 3) * 0.1) for i in range(80)]
    facts = measure_execution(trades)
    assert facts.spread_by_hour
    assert all(0 <= hour <= 23 for hour in facts.spread_by_hour)


def test_unexpected_target_rate_is_flagged():
    """Wijkt de trefkans sterk af van wat doel en stop voorspellen, dan klopt
    de ATR-schatting waarschijnlijk niet."""
    trades = [_trade(i, 1.0, reason="take_profit") for i in range(50)]
    facts = measure_execution(trades)
    assert facts.target_hit_rate == 1.0
    assert any("ATR" in n for n in facts.notes)


# ---------------- optimalisatie ----------------

def test_no_proposal_without_enough_trades():
    trades = [_trade(i, 1.0) for i in range(40)]
    assert evaluate_threshold(trades, 0.45, [0.3, 0.6]) is None


def test_overfitted_improvement_is_rejected():
    """Winst op de zoekhelft die op de controlehelft verdwijnt, is ruis."""
    trades = []
    for i in range(200):
        eerste_helft = i < 100
        hoge_score = i % 2 == 0
        # In de eerste helft zijn hoge scores winstgevend, in de tweede niet.
        net = (2.0 if hoge_score else -1.0) if eerste_helft else (-1.0 if hoge_score else 2.0)
        trades.append(_trade(i, net, score=0.8 if hoge_score else 0.2))
    proposal = evaluate_threshold(trades, 0.45, [0.3, 0.7])
    if proposal is not None:
        assert not proposal.accept
        assert "overfitting" in proposal.reasoning or "toeval" in proposal.reasoning


def test_proposal_is_never_auto_applied():
    """De uitkomst is een voorstel; toepassen is een menselijke beslissing."""
    from gold_scalper.learning import analysis
    source = (
        os.path.join(os.path.dirname(analysis.__file__), "analysis.py")
    )
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    # Er mag nergens een configuratie geschreven worden vanuit deze module.
    assert "async_update_entry" not in text
    assert "config_entry" not in text


def test_consistent_improvement_can_be_recommended():
    trades = []
    for i in range(300):
        hoge_score = i % 2 == 0
        net = 2.0 if hoge_score else -1.5
        trades.append(_trade(i, net, score=0.8 if hoge_score else 0.2))
    proposal = evaluate_threshold(trades, 0.1, [0.5, 0.7])
    assert proposal is not None
    assert proposal.suggested > 0.1
    assert proposal.out_of_sample_gain > 0


# ---------------- regimes ----------------

def test_regime_performance_is_descriptive():
    trades = (
        [_trade(i, 2.0, regime="trend") for i in range(40)]
        + [_trade(i + 100, -1.0, regime="range") for i in range(40)]
    )
    result = regime_performance(trades)
    assert result["trend"]["net_pnl"] > 0
    assert result["range"]["net_pnl"] < 0
    assert result["trend"]["trades"] == 40


def test_small_regime_sample_is_not_significant():
    trades = [_trade(i, 2.0, regime="trend") for i in range(5)]
    assert regime_performance(trades)["trend"]["significant"] is False
