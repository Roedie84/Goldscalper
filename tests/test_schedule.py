"""Handelstijden als onafhankelijke controle op de broker.

De integratie leunde volledig op het veld marketState. Klopt dat veld niet, dan
handelt de bot op verouderde koersen zonder dat iets het merkt. Een tweede bron
maakt dat zichtbaar.
"""
import os
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.schedule import (
    SPOT_GOLD, WEEKEND_GOLD, cross_check, is_open, minutes_until_close,
)

AMS = ZoneInfo("Europe/Amsterdam")


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """Augustus 2026: 31 = maandag, dus 31+n geeft de weekdag."""
    return datetime(2026, 8, 31, hour, minute, tzinfo=AMS).replace(
        day=31 if day == 0 else day
    )


def _weekday(name: str, hour: int, minute: int = 0) -> datetime:
    """Maandag 31-08-2026 tot en met zondag 06-09-2026."""
    dagen = {"ma": (8, 31), "di": (9, 1), "wo": (9, 2), "do": (9, 3),
             "vr": (9, 4), "za": (9, 5), "zo": (9, 6)}
    maand, dag = dagen[name]
    return datetime(2026, maand, dag, hour, minute, tzinfo=AMS)


# ---------------- het rooster zelf ----------------

@pytest.mark.parametrize("dag,uur,verwacht", [
    ("ma", 0, True), ("ma", 12, True),
    ("wo", 22, True), ("do", 3, True),
    ("vr", 22, True),
])
def test_open_during_the_week(dag, uur, verwacht):
    assert is_open(SPOT_GOLD, _weekday(dag, uur, 30))[0] is verwacht


@pytest.mark.parametrize("dag,uur", [("za", 12), ("zo", 12), ("zo", 22)])
def test_closed_in_the_weekend(dag, uur):
    assert is_open(SPOT_GOLD, _weekday(dag, uur))[0] is False


def test_daily_break_is_respected():
    """23:00 tot 24:00 sluit de onderliggende markt."""
    assert is_open(SPOT_GOLD, _weekday("wo", 22, 59))[0] is True
    assert is_open(SPOT_GOLD, _weekday("wo", 23, 30))[0] is False
    assert is_open(SPOT_GOLD, _weekday("do", 0, 1))[0] is True


def test_friday_close():
    assert is_open(SPOT_GOLD, _weekday("vr", 22, 59))[0] is True
    assert is_open(SPOT_GOLD, _weekday("vr", 23, 1))[0] is False


def test_weekend_session_is_separate():
    assert is_open(WEEKEND_GOLD, _weekday("za", 12))[0] is True
    assert is_open(WEEKEND_GOLD, _weekday("za", 8))[0] is False
    assert is_open(WEEKEND_GOLD, _weekday("zo", 23, 50))[0] is False


# ---------------- tijd tot sluiting ----------------

def test_minutes_until_the_daily_break():
    assert minutes_until_close(SPOT_GOLD, _weekday("wo", 22, 50)) == pytest.approx(
        10, abs=1
    )


def test_no_countdown_when_closed():
    assert minutes_until_close(SPOT_GOLD, _weekday("za", 12)) is None


# ---------------- kruiscontrole ----------------

def test_agreement_passes_through():
    open_now, note = cross_check(True, SPOT_GOLD, _weekday("wo", 12))
    assert open_now is True and note is None


def test_broker_says_open_while_schedule_says_closed():
    """Het gevaarlijke geval: handelen op een koers van uren geleden."""
    open_now, note = cross_check(True, SPOT_GOLD, _weekday("za", 12))
    assert open_now is False
    assert note is not None and "rooster" in note


def test_broker_says_closed_wins_too():
    """Bij onenigheid wint altijd 'gesloten'. Een gemiste kans kost niets."""
    open_now, note = cross_check(False, SPOT_GOLD, _weekday("wo", 12))
    assert open_now is False
    assert note is not None and "feestdag" in note


def test_both_closed_is_silent():
    open_now, note = cross_check(False, SPOT_GOLD, _weekday("za", 12))
    assert open_now is False and note is None


def test_disagreement_is_reported_not_hidden():
    """Het rooster is geen waarheid: feestdagen staan er niet in. Een afwijking
    hoort gemeld te worden zodat je hem kunt beoordelen."""
    _, note = cross_check(True, SPOT_GOLD, _weekday("zo", 12))
    assert note and ("verouderd" in note or "rooster" in note)
