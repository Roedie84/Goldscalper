"""Winst deels nemen, en de rest risicovrij laten lopen.

Drie dingen gingen mis en ze versterkten elkaar: de deelsluiting werd niet
vastgelegd, de administratie overleefde geen herstart, en break-even was
losgekoppeld van het afromen. Gevolg: een positie stond op 50 dollar winst,
er werd niets genomen, en er was ook geen spoor van te vinden.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
sys.path.insert(0, str(PKG.parent))

from gold_scalper.broker.exits import ExitConfig, ExitManager

COORDINATOR = (PKG / "coordinator.py").read_text(encoding="utf-8")


ATR = 4.0
OPEN_PRICE = 4640.0


def _decide(profit_atr: float, partial_taken: bool = False, stop=None):
    """Vraag de exitmanager wat hij zou doen bij deze winst."""
    from datetime import datetime, timedelta, timezone

    manager = ExitManager(ExitConfig())
    opened = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    # Voor een long is de uitstapprijs de bid; die moet dus de winst dragen.
    bid = OPEN_PRICE + profit_atr * ATR
    return manager.evaluate(
        side="buy", volume=0.1, open_price=OPEN_PRICE, current_stop=stop,
        bid=bid, ask=bid + 0.6, atr=ATR,
        opened_at=opened, now=opened + timedelta(seconds=60),
        round_trip_cost_per_oz=0.62, partial_taken=partial_taken,
    )


def test_partial_fires_at_the_first_target():
    action = _decide(1.2)
    assert action.kind == "partial_close"
    assert 0 < action.close_fraction < 1


def test_nothing_happens_below_the_trigger():
    assert _decide(0.3).kind == "hold"


def test_partial_fires_only_once():
    assert _decide(1.2, partial_taken=True).kind != "partial_close"


# ---------------- break-even ná het afromen ----------------

def test_stop_moves_to_breakeven_right_after_a_partial():
    """De kern van de vraag: zodra de helft is afgeroomd is de rest gratis
    geworden. Die daarna alsnog met verlies laten sluiten is de slechtste van
    beide werelden."""
    action = _decide(1.05, partial_taken=True)
    assert action.kind == "modify_stop"
    assert action.new_stop >= OPEN_PRICE
    assert "deels genomen" in action.reason


def test_breakeven_after_partial_ignores_its_own_threshold():
    """Ook bij een winst onder de normale break-evendrempel."""
    action = _decide(0.2, partial_taken=True)
    assert action.kind == "modify_stop"
    assert action.new_stop >= OPEN_PRICE


def test_breakeven_includes_the_costs():
    """Op precies de instapprijs stoppen levert nog steeds verlies op, want de
    round trip is al betaald."""
    action = _decide(1.05, partial_taken=True)
    assert action.new_stop > OPEN_PRICE


def test_no_backwards_stop_move():
    """Een stop die al gunstiger staat mag niet terug."""
    action = _decide(1.05, partial_taken=True, stop=4700.0)
    assert action.kind != "modify_stop" or action.new_stop >= 4700.0


# ---------------- vastleggen en bewaren ----------------

def _method(name: str) -> str:
    tree = ast.parse(COORDINATOR)
    lines = COORDINATOR.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} bestaat niet")


def test_partial_close_is_recorded():
    """Anders wordt de winst wél genomen maar verschijnt hij nergens."""
    body = _method("_manage_open_positions")
    assert "_record_partial" in body


def test_the_recorded_part_has_its_own_row():
    body = _method("_record_partial")
    assert "insert_trade" in body
    assert "partial_close" in body


def test_the_remaining_position_shrinks():
    """De oorspronkelijke rij moet krimpen, anders tel je het gesloten deel
    dubbel."""
    body = _method("_record_partial")
    assert "trade.volume = max(" in body
    assert "update_trade" in body


def test_partial_state_survives_a_restart():
    """Zonder dit wordt na een herstart dezelfde positie opnieuw gehalveerd,
    en bij herhaling tot niets."""
    from gold_scalper.storage.state import RuntimeState

    state = RuntimeState(partial_taken=["DIAAA123"])
    assert RuntimeState(**state.as_dict()).partial_taken == ["DIAAA123"]
    assert "partial_taken" in COORDINATOR


def test_the_partial_counts_towards_the_daily_risk():
    body = _method("_record_partial")
    assert "record_close" in body
