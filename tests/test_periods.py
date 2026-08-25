"""Resultaat per dag, week en maand.

Een totaalcijfer verbergt wat je wilt weten: vierhonderd winst kan betekenen
dat je elke week iets verdiende, of dat één week alles opleverde. Dat zijn twee
heel verschillende systemen, en alleen het eerste is er een.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.storage.database import Trade
from gold_scalper.storage.periods import build_periods

AMS = ZoneInfo("Europe/Amsterdam")
T0 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def _trade(dagen: int, net: float, uur: int = 10):
    moment = (T0 + timedelta(days=dagen)).replace(hour=uur)
    return Trade(
        run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.01,
        open_time=moment.isoformat(), open_price=4640.0, open_mid=4640.0,
        open_spread=0.6,
        close_time=(moment + timedelta(minutes=5)).isoformat(),
        net_pnl=net, gross_pnl=net + 0.6, total_cost=0.6,
    )


def test_empty_history_gives_empty_report():
    report = build_periods([])
    assert report.daily == [] and report.weekly == []


def test_trades_group_per_day():
    report = build_periods([_trade(0, 1.0), _trade(0, 2.0), _trade(1, -1.0)])
    assert len(report.daily) == 2
    assert report.daily[0].net == pytest.approx(3.0)
    assert report.daily[1].net == pytest.approx(-1.0)


def test_weeks_and_months_aggregate():
    trades = [_trade(d, 1.0) for d in range(40)]
    report = build_periods(trades)
    assert len(report.daily) == 40
    assert 5 <= len(report.weekly) <= 7
    assert len(report.monthly) == 2


def test_costs_and_gross_are_kept_apart():
    """Een winstcijfer zonder de kostenkolom ernaast is misleidend."""
    report = build_periods([_trade(0, 1.0), _trade(0, 2.0)])
    day = report.daily[0]
    assert day.costs == pytest.approx(1.2)
    assert day.gross == pytest.approx(4.2)
    assert day.net == pytest.approx(3.0)


def test_best_and_worst_trade_per_day():
    report = build_periods([_trade(0, 5.0), _trade(0, -3.0), _trade(0, 1.0)])
    day = report.daily[0]
    assert day.best == 5.0 and day.worst == -3.0


def test_local_midnight_decides_the_day():
    """Een trade van half twee 's nachts hoort bij die nacht zoals jij hem
    beleeft, niet bij de vorige dag."""
    trade = _trade(0, 1.0, uur=23)      # 23:00 UTC = 01:00 lokaal, volgende dag
    utc = build_periods([trade])
    local = build_periods([trade], AMS)
    assert utc.daily[0].start != local.daily[0].start


# ---------------- reeksen ----------------

def test_longest_streaks_are_reported():
    """Acht verliesdagen op rij is iets anders dan acht verspreid, ook als het
    totaal gelijk is."""
    nets = [1, 1, 1, -1, -1, -1, -1, 1, 1]
    report = build_periods([_trade(d, n) for d, n in enumerate(nets)])
    streaks = report.streaks()
    assert streaks["longest_losing"] == 4
    assert streaks["longest_winning"] == 3
    assert streaks["total_days"] == 9


def test_share_of_winning_days():
    nets = [1, 1, -1, -1]
    report = build_periods([_trade(d, n) for d, n in enumerate(nets)])
    assert report.streaks()["share_winning"] == 0.5


def test_median_day_is_reported():
    """Het gemiddelde wordt door één uitschieter opgetild; de mediaan niet."""
    nets = [1, 1, 1, 1, 100]
    report = build_periods([_trade(d, n) for d, n in enumerate(nets)])
    assert report.streaks()["median_day"] == 1.0


def test_dict_output_is_bounded():
    """Twee jaar dagcijfers in een sensorattribuut is onbruikbaar."""
    trades = [_trade(d, 1.0) for d in range(200)]
    data = build_periods(trades).as_dict()
    assert len(data["daily"]) <= 31
    assert len(data["weekly"]) <= 26


def test_open_trades_are_ignored():
    open_trade = Trade(
        run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.01,
        open_time=T0.isoformat(), open_price=4640.0, open_mid=4640.0,
        open_spread=0.6,
    )
    report = build_periods([_trade(0, 1.0), open_trade])
    assert report.daily[0].trades == 1


def test_spread_paid_is_reported_separately(tmp_path):
    """De actuele spread zegt niets over trades die uren geleden liepen.
    Zonder dit onderscheid lijkt een correcte kostprijs onverklaarbaar: de
    sensor toont 0,60 terwijl je 0,82 betaalde."""
    from gold_scalper.storage.performance import compute

    trades = []
    for i, spread in enumerate([0.80, 0.85, 0.60]):
        trade = _trade(i, 1.0)
        trade.open_spread = spread
        trades.append(trade)

    stats = compute(trades, 10000.0)
    assert stats["spread_paid_median"] == pytest.approx(0.80)
    assert stats["spread_paid_max"] == pytest.approx(0.85)


def test_spread_paid_is_none_without_data():
    from gold_scalper.storage.performance import compute
    assert compute([], 1000.0).get("spread_paid_median") is None
