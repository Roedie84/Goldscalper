"""Vergelijken met de broker in plaats van de eigen code testen.

Unittests toetsen of de code doet wat de bedoeling was; ze weten niet of die
bedoeling klopt met hoe de broker zich gedraagt. Drie fouten uit de praktijk,
alle drie ontdekt door de brokerinterface naast de eigen rapportage te leggen:

* modify_stop wiste de take-profit
* close(units) sloot de hele positie
* orders in USD op een account in euro's
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.adapter import VenuePosition
from gold_scalper.broker.reconcile_audit import compare_positions
from gold_scalper.const import CONTRACT_SIZE
from gold_scalper.storage.database import Trade


def _position(**over):
    base = dict(ticket="T1", symbol="GOLD", side="buy", units=10.0,
                open_price=4663.0, stop_loss=4659.0, take_profit=4671.0)
    base.update(over)
    return VenuePosition(**base)


def _trade(**over):
    base = dict(
        run_id=1, mode="demo", symbol="GOLD", side="buy",
        volume=10.0 / CONTRACT_SIZE, open_time="2026-08-25T21:00:00+00:00",
        open_price=4663.0, open_mid=4663.0, open_spread=0.6,
        stop_loss=4659.0, take_profit=4671.0, broker_ticket="T1",
    )
    base.update(over)
    return Trade(**base)


def test_matching_state_gives_no_findings():
    audit = compare_positions([_position()], [_trade()])
    assert audit.findings == []
    assert audit.positions_checked == 1


def test_missing_stop_is_critical():
    """Het enige scenario met in principe onbegrensd verlies."""
    audit = compare_positions([_position(stop_loss=None)], [_trade()])
    assert any(f.code == "geen_stop" for f in audit.findings)
    assert audit.critical


def test_missing_target_is_a_warning_not_critical():
    """Geen doel is hinderlijk, geen stop is gevaarlijk."""
    audit = compare_positions([_position(take_profit=None)], [_trade()])
    codes = {f.code: f.severity for f in audit.findings}
    assert codes["geen_doel"] == "waarschuwing"
    assert not audit.critical


def test_size_mismatch_is_critical():
    """De deelsluitingbug: broker sloot alles, database boekte de helft."""
    audit = compare_positions([_position(units=10.0)],
                              [_trade(volume=5.0 / CONTRACT_SIZE)])
    assert any(f.code == "omvang_verschilt" for f in audit.critical)


def test_unknown_position_is_critical():
    audit = compare_positions([_position(ticket="T9")], [_trade()])
    codes = {f.code for f in audit.critical}
    assert "onbekende_positie" in codes


def test_vanished_position_is_only_a_warning():
    """Waarschijnlijk gesloten op een stop; wordt alsnog afgerekend."""
    audit = compare_positions([], [_trade()])
    assert not audit.critical
    assert any(f.code == "verdwenen_positie" for f in audit.findings)


def test_stop_level_difference_is_reported():
    audit = compare_positions([_position(stop_loss=4660.5)], [_trade()])
    assert any(f.code == "stop_verschilt" for f in audit.findings)


def test_target_level_difference_is_reported():
    """Precies de bug waarbij modify_stop de take-profit wiste."""
    audit = compare_positions([_position(take_profit=4675.0)], [_trade()])
    assert any(f.code == "doel_verschilt" for f in audit.findings)


def test_direction_mismatch_is_critical():
    audit = compare_positions([_position(side="sell")], [_trade(side="buy")])
    assert any(f.code == "richting_verschilt" for f in audit.critical)


def test_currency_mismatch_is_reported():
    """Orders in USD op een account in euro's: resultaten en risicolimieten
    rekenen dan in verschillende eenheden."""
    audit = compare_positions(
        [_position()], [_trade()],
        expected_currency="USD", account_currency="EUR",
    )
    assert any(f.code == "valuta_verschilt" for f in audit.findings)


def test_same_currency_is_silent():
    audit = compare_positions(
        [_position()], [_trade()],
        expected_currency="USD", account_currency="USD",
    )
    assert not any(f.code == "valuta_verschilt" for f in audit.findings)


def test_rounding_noise_is_ignored():
    """Een verschil van een duizendste is afronding, geen bevinding."""
    audit = compare_positions([_position(units=10.001, stop_loss=4659.001)],
                              [_trade()])
    assert audit.findings == []
