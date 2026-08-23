"""Exitbeheer. De belangrijkste test is dat break-even wordt getoetst tegen de
prijs waarop je écht uitstapt, niet tegen de mid."""
import os, sys
from datetime import datetime, timedelta, timezone
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.exits import ExitConfig, ExitManager

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
ATR = 0.40
COST = 0.39


def ev(m, **over):
    base = dict(side="buy", volume=0.10, open_price=3300.0, current_stop=3299.6,
                bid=3300.0, ask=3300.35, atr=ATR, opened_at=T0, now=T0 + timedelta(seconds=30),
                round_trip_cost_per_oz=COST, partial_taken=False)
    base.update(over); return m.evaluate(**base)


def test_holds_when_nothing_triggered():
    a = ev(ExitManager())
    assert a.kind == "hold"


def test_breakeven_moves_stop_above_entry():
    m = ExitManager(ExitConfig(enable_partial_close=False, enable_trailing=False))
    a = ev(m, bid=3300.35)  # +0.35 = 0.875 ATR
    assert a.kind == "modify_stop"
    assert a.new_stop > 3300.0  # boven instap: kan niet meer verliezen


def test_breakeven_uses_exit_price_not_mid():
    """Bij bid 3300.30 / ask 3300.65 is de mid 3300.475 (1.19 ATR) maar de
    werkelijke exit 3300.30 (0.75 ATR). Onder de trigger van 0.8, dus geen actie."""
    m = ExitManager(ExitConfig(breakeven_trigger_atr=0.8, enable_partial_close=False,
                               enable_trailing=False))
    a = ev(m, bid=3300.30, ask=3300.65)
    assert a.kind == "hold"


def test_partial_close_at_first_target():
    m = ExitManager(ExitConfig(partial_close_trigger_atr=1.0))
    a = ev(m, bid=3300.45, current_stop=3300.5)  # 1.125 ATR, stop al voorbij BE
    assert a.kind == "partial_close"
    assert a.close_fraction == pytest.approx(0.5)


def test_partial_close_only_once():
    m = ExitManager(ExitConfig(partial_close_trigger_atr=1.0))
    a = ev(m, bid=3300.45, current_stop=3300.5, partial_taken=True)
    assert a.kind != "partial_close"


def test_trailing_activates_on_larger_move():
    m = ExitManager(ExitConfig(trailing_activate_atr=1.5, trailing_distance_atr=1.2))
    a = ev(m, bid=3301.0)  # 2.5 ATR
    assert a.kind == "modify_stop"
    assert a.new_stop == pytest.approx(3301.0 - ATR * 1.2, abs=1e-3)


def test_trailing_stop_never_moves_backwards():
    m = ExitManager(ExitConfig(trailing_activate_atr=1.5, trailing_distance_atr=1.2))
    a = ev(m, bid=3301.0, current_stop=3300.9)  # bestaande stop al hoger
    assert a.kind != "modify_stop" or a.new_stop > 3300.9


def test_short_position_is_symmetric():
    m = ExitManager(ExitConfig(enable_partial_close=False, enable_trailing=False))
    a = m.evaluate(side="sell", volume=0.10, open_price=3300.0, current_stop=3300.4,
                   bid=3299.30, ask=3299.65, atr=ATR, opened_at=T0,
                   now=T0 + timedelta(seconds=30), round_trip_cost_per_oz=COST)
    assert a.kind == "modify_stop"
    assert a.new_stop < 3300.0  # onder instap voor een short


def test_time_stop_closes_a_position_going_nowhere():
    m = ExitManager(ExitConfig(time_stop_seconds=240, time_stop_deadzone_atr=0.3))
    a = ev(m, bid=3300.02, ask=3300.37, now=T0 + timedelta(seconds=300))
    assert a.kind == "close" and "kostenpost" in a.reason


def test_time_stop_leaves_a_winning_position_alone():
    m = ExitManager(ExitConfig(time_stop_seconds=240, time_stop_deadzone_atr=0.3,
                               enable_trailing=False, enable_partial_close=False))
    a = ev(m, bid=3300.60, now=T0 + timedelta(seconds=300))
    assert a.kind != "close"


def test_hard_limit_always_closes():
    m = ExitManager(ExitConfig(max_hold_seconds=900))
    a = ev(m, bid=3302.0, now=T0 + timedelta(seconds=1000))
    assert a.kind == "close" and "maximale positieduur" in a.reason


def test_no_atr_means_no_action():
    assert ev(ExitManager(), atr=0.0).kind == "hold"


def test_realised_profit_math():
    m = ExitManager()
    # long 0.10 lot (10 oz), 3300 -> 3300.50
    assert m.realised_profit("buy", 0.10, 3300.0, 3300.50) == pytest.approx(5.0)
    assert m.realised_profit("sell", 0.10, 3300.0, 3299.50) == pytest.approx(5.0)
