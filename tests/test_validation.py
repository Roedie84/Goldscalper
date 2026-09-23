"""Toetst het meetinstrument: klopt de backtest met wat er live gebeurde?

Alle hypothesen die je op historische data toetst, rusten op de aanname dat de
backtest de werkelijkheid nabootst. Die aanname is zelden gecontroleerd, en als
hij niet klopt is elke toets erop waardeloos — inclusief de toetsen die je
overtuigden.
"""
import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.validation import MIN_LIVE_TRADES, compare
from gold_scalper.storage.database import Trade


@dataclass
class FakeBacktest:
    """Alleen de samenvatting telt voor de vergelijking."""

    trades: int = 100
    win_rate: float = 34.0
    net_pnl: float = 10.0
    total_costs: float = 104.0

    def summary(self) -> dict:
        return {
            "trades": self.trades, "win_rate": self.win_rate,
            "net_pnl": self.net_pnl, "total_costs": self.total_costs,
        }


def _live(n=100, trefkans=0.34, netto_per=0.10, kosten_per=1.04):
    trades = []
    winnaars = int(n * trefkans)
    for i in range(n):
        win = i < winnaars
        net = netto_per * n / max(1, winnaars) if win else -0.05
        trades.append(Trade(
            run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.013,
            open_time=f"2026-09-{(i % 20) + 1:02d}T10:00:00+00:00",
            open_price=4400.0, open_mid=4400.0, open_spread=0.6,
            close_time=f"2026-09-{(i % 20) + 1:02d}T10:30:00+00:00",
            net_pnl=net, gross_pnl=net + kosten_per, total_cost=kosten_per,
        ))
    return trades


def test_too_few_live_trades_gives_no_verdict():
    result = compare(_live(5), FakeBacktest())
    assert result.verdict == "onvoldoende_data"
    assert "nodig" in result.explanation


def test_an_empty_backtest_is_flagged():
    """De backtest zag geen enkel signaal terwijl de bot handelde. Meestal een
    gat in het archief."""
    result = compare(_live(), FakeBacktest(trades=0))
    assert result.verdict == "backtest_leeg"
    assert "gaten" in result.explanation


def test_matching_results_are_usable():
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=90, win_rate=34.0, total_costs=95.0,
                      net_pnl=live[0].net_pnl * 34)
    result = compare(live, bt)
    assert result.verdict in ("bruikbaar", "deels_bruikbaar")


def test_cost_divergence_is_the_worst_finding():
    """Kosten zijn rekenkunde, geen voorspelling. Wijken die af, dan is elke
    hypothese op deze backtest gebouwd op een verkeerde kostenbasis."""
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=100, win_rate=34.0, total_costs=300.0)
    result = compare(live, bt)
    assert result.verdict == "kosten_wijken_af"
    assert "rekenkunde" in result.explanation


def test_win_rate_divergence_is_reported():
    live = _live(100, trefkans=0.34)
    bt = FakeBacktest(trades=100, win_rate=60.0, total_costs=104.0)
    result = compare(live, bt)
    assert result.verdict == "trefkans_wijkt_af"
    assert "instapmoment" in result.explanation


def test_trade_count_may_differ_widely():
    """De bot handelde op losse koersen, de backtest op afgesloten bars. Een
    factor twee is te verklaren."""
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=180, win_rate=34.0, total_costs=187.0)
    result = compare(live, bt)
    aantal = next(c for c in result.comparisons if c.naam == "aantal trades")
    assert aantal.klopt


def test_a_tenfold_difference_does_not_pass():
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=1000, win_rate=34.0, total_costs=1040.0)
    result = compare(live, bt)
    aantal = next(c for c in result.comparisons if c.naam == "aantal trades")
    assert not aantal.klopt


def test_the_verdict_states_what_it_does_not_prove():
    """Een bruikbare backtest voorspelt de toekomst niet; hij bootst het
    verleden goed genoeg na om hypothesen te vergelijken."""
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=100, win_rate=34.0, total_costs=104.0,
                      net_pnl=sum(t.net_pnl for t in live))
    result = compare(live, bt)
    if result.verdict == "bruikbaar":
        assert "niet zegt" in result.explanation


def test_the_period_is_reported():
    result = compare(_live(100), FakeBacktest())
    assert result.period_start and result.period_end


# ---------------- richting van de afwijking ----------------

def test_an_optimistic_backtest_is_named_as_such():
    """De richting is belangrijker dan de grootte. Een backtest die gunstiger
    uitvalt dan de werkelijkheid, laat je conclusies trekken die live niet
    standhouden."""
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=100, win_rate=34.0, total_costs=104.0,
                      net_pnl=9999.0)
    result = compare(live, bt)
    netto = next(c for c in result.comparisons if c.naam == "netto per trade")
    assert netto.richting == "optimistisch"
    assert result.verdict == "te_optimistisch"
    assert "gevaarlijke richting" in result.explanation


def test_a_pessimistic_backtest_is_less_alarming():
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=100, win_rate=34.0, total_costs=104.0,
                      net_pnl=-9999.0)
    result = compare(live, bt)
    assert result.verdict == "pessimistisch"
    assert "veilige richting" in result.explanation


def test_low_costs_in_the_backtest_are_optimistic():
    """Voor kosten is de richting omgekeerd: te weinig kosten is te
    rooskleurig."""
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=100, win_rate=34.0, total_costs=10.0)
    result = compare(live, bt)
    kosten = next(c for c in result.comparisons if c.naam == "kosten per trade")
    assert kosten.richting == "optimistisch"
    assert "te rooskleurig" in result.explanation


def test_high_costs_in_the_backtest_are_pessimistic():
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=100, win_rate=34.0, total_costs=500.0)
    result = compare(live, bt)
    kosten = next(c for c in result.comparisons if c.naam == "kosten per trade")
    assert kosten.richting == "pessimistisch"
    assert "te somber" in result.explanation


def test_a_matching_measure_has_no_direction():
    live = _live(100, kosten_per=1.04)
    bt = FakeBacktest(trades=100, win_rate=34.0, total_costs=104.0)
    result = compare(live, bt)
    kosten = next(c for c in result.comparisons if c.naam == "kosten per trade")
    assert kosten.richting == "gelijk"
