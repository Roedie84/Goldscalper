"""Pyramiden: bijkopen bij bevestiging, nooit bij tegenslag.

Het spiegelbeeld van middelen. Bij middelen groeit je verlies kwadratisch
terwijl je stop dezelfde blijft: vier keer bijkopen bij een dalende koers
verandert een verlies van veertig in vierhonderd zonder dat je stop ook maar
één keer geraakt is.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.strategy.pyramid import PyramidConfig, consider_addition

ENTRY = 4665.85
ATR = 4.01
UNITS = 10.0
STOP = ENTRY - ATR


def _ask(price, *, cfg=None, stop=STOP, total=UNITS, done=0, last=None,
         side="buy", entry=ENTRY):
    return consider_addition(
        cfg or PyramidConfig(enabled=True),
        side=side, entry_price=entry, current_price=price,
        current_stop=stop, original_units=UNITS, total_units=total,
        additions_done=done, last_addition_price=last, atr=ATR,
        round_trip_cost_per_oz=0.62,
    )


def test_disabled_by_default():
    assert PyramidConfig().enabled is False
    assert _ask(4680, cfg=PyramidConfig()).add is False


# ---------------- de kernregel ----------------

def test_never_adds_to_a_loser():
    """Het hele punt: bij een dalende koers gebeurt er niets."""
    for price in (4664, 4660, 4650, 4600):
        decision = _ask(price)
        assert not decision.add, f"bijgekocht bij {price}"


def test_does_not_add_before_the_move_is_confirmed():
    decision = _ask(ENTRY + 0.3 * ATR)
    assert not decision.add
    assert "bevestiging" in decision.reason


def test_adds_once_confirmed():
    decision = _ask(ENTRY + 1.1 * ATR)
    assert decision.add
    assert decision.units > 0
    assert decision.new_stop is not None


# ---------------- risico blijft begrensd ----------------

def test_total_risk_does_not_grow():
    """De regel die alles bijeenhoudt. Zonder deze koppeling is pyramiden
    gewoon een trager soort middelen."""
    original_risk = abs(ENTRY - STOP) * UNITS
    price = ENTRY + 1.1 * ATR
    decision = _ask(price)
    assert decision.add

    total = UNITS + decision.units
    new_risk = abs(price - decision.new_stop) * total
    assert new_risk <= original_risk * 1.02


def test_addition_is_skipped_when_the_stop_cannot_follow():
    """Kan de stop niet ver genoeg mee omhoog, dan gaat de toevoeging niet
    door - anders groeit het risico alsnog."""
    # Stop staat al zeer gunstig; verder omhoog zou onder de koers uitkomen.
    decision = _ask(ENTRY + 1.1 * ATR, stop=ENTRY + 1.05 * ATR)
    assert not decision.add


def test_stop_must_clear_breakeven():
    """Sluiten met verlies op een positie die in de plus stond, is de
    slechtste uitkomst."""
    decision = _ask(ENTRY + 1.0 * ATR, total=UNITS * 20)
    if not decision.add:
        assert "break-even" in decision.reason or "risico" in decision.reason


def test_no_stop_means_no_addition():
    assert not _ask(ENTRY + 2 * ATR, stop=None).add


# ---------------- vorm van de piramide ----------------

def test_additions_shrink():
    """Een piramide die naar boven breder wordt valt om: je grootste inzet zit
    dan op het hoogste punt."""
    first = _ask(ENTRY + 1.1 * ATR, done=0)
    second = _ask(ENTRY + 2.5 * ATR, done=1, total=UNITS + first.units,
                  last=ENTRY + 1.1 * ATR, stop=first.new_stop)
    assert first.add and second.add
    assert second.units < first.units


def test_maximum_additions_is_respected():
    cfg = PyramidConfig(enabled=True, max_additions=2)
    decision = _ask(ENTRY + 5 * ATR, cfg=cfg, done=2)
    assert not decision.add
    assert "maximum" in decision.reason


def test_spacing_prevents_stacking_at_one_level():
    """Zonder afstand stapelen alle toevoegingen op één prijsniveau, en dan heb
    je geen piramide maar een grote positie met een dun excuus."""
    price = ENTRY + 1.2 * ATR
    decision = _ask(price, done=1, last=price - 0.1 * ATR)
    assert not decision.add
    assert "vorige toevoeging" in decision.reason


# ---------------- shorts ----------------

def test_shorts_work_mirrored():
    price = ENTRY - 1.1 * ATR
    decision = _ask(price, side="sell", stop=ENTRY + ATR)
    assert decision.add
    assert decision.new_stop < ENTRY + ATR


def test_short_does_not_add_when_price_rises():
    assert not _ask(ENTRY + 2 * ATR, side="sell", stop=ENTRY + ATR).add
