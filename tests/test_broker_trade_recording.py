"""Orders die naar de broker gaan moeten in de database komen.

In papermodus schrijft de paper-broker elke trade weg. Bij demo en live ging de
order naar de broker en verdween daarna uit de eigen administratie: het
overzicht toonde "2 signalen uitgevoerd" naast "0 trades", er was geen
resultaat, geen kostenmeting, geen verliesanalyse, en de bewijsfase vorderde
nooit.

Dat maakte de demomodus zinloos, want juist het meten was het doel.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
sys.path.insert(0, str(PKG.parent))

SOURCE = (PKG / "coordinator.py").read_text(encoding="utf-8")


def _method(name: str) -> str:
    """Haal de body van één methode op, via de parser.

    Met tekstmarkeringen knippen gaat twee keer mis: `_open_position` is een
    prefix van `_open_positions`, en een methode eindigt niet op een
    voorspelbare regel. De AST kent de werkelijke grenzen.
    """
    tree = ast.parse(SOURCE)
    lines = SOURCE.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name == name:
                return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} bestaat niet")


def test_orders_are_written_to_the_database():
    body = _method("_open_position")
    assert "_record_broker_open" in body, (
        "een order naar de broker wordt niet vastgelegd"
    )


def test_closing_writes_the_result():
    body = _method("_close_position")
    assert "_record_broker_close" in body


def test_broker_initiated_closes_are_settled():
    """Een stop die de broker zelf uitvoert, verdwijnt zonder dat wij iets
    merken. Zonder afstemming blijft de rij eeuwig open staan."""
    assert "_settle_vanished_positions" in SOURCE
    update = SOURCE.split("async def _async_update_data")[1][:2500]
    assert "_settle_vanished_positions" in update


def test_slippage_is_measured_not_assumed():
    """Het hele punt van demo-modus: de fillprijs vergelijken met wat je
    verwachtte, in plaats van een model te vertrouwen."""
    body = _method("_record_broker_open")
    assert "open_slippage" in body
    assert "result.fill_price" in body


def test_gross_uses_mids_and_net_uses_fills():
    """Bruto is de beweging die de strategie ving; netto is wat er na spread en
    slippage overblijft. Beide op dezelfde prijzen berekenen zou de kosten
    onzichtbaar maken."""
    body = _method("_record_broker_close")
    assert "open_mid" in body and "quote.mid" in body
    assert "trade.open_price" in body and "exit_price" in body
    assert "total_cost" in body


def test_the_ticket_links_database_to_broker():
    body = _method("_record_broker_open")
    assert "broker_ticket" in body


def test_risk_manager_learns_about_the_result():
    """Anders tellen demo-verliezen niet mee voor de daglimiet."""
    body = _method("_record_broker_close")
    assert "record_close" in body


def test_no_dict_to_class_trick_for_the_ticket():
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "type" and len(node.args) == 3):
            pytest.fail(f"dict-naar-klasse-truc op regel {node.lineno}")


def test_close_reason_is_inferred_honestly():
    """De broker vertelt niet waarom hij sloot; dat afleiden mag, maar het
    resultaat hoort als afleiding gemarkeerd te zijn."""
    body = _method("_settle_vanished_positions")
    assert "broker_gesloten" in body
    assert "stop_loss" in body and "take_profit" in body
