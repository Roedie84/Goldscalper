"""Home Assistant's NumberSelector geeft ALTIJD een float terug, ook als het
veld een geheel getal voorstelt. Elke waarde uit de config flow die als int
gebruikt wordt moet dat overleven.

Deze tests bestaan omdat precies dit een crash veroorzaakte bij het opzetten:
een zaad van 20260823.0 sloopte de bitwise hash in de simulator.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.simulator import SimulatorVenue, _fractal, _hash01
from gold_scalper.coordinator import _as_datetime, _as_int
from gold_scalper.broker.exits import ExitConfig, ExitManager

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


# ---------------- de crash die optrad ----------------

def test_hash_survives_float_seed():
    """De oorspronkelijke fout: 'unsupported operand type(s) for &: float and int'."""
    assert 0.0 <= _hash01(5, 20260823.0) < 1.0
    assert _hash01(5, 20260823.0) == _hash01(5, 20260823)


def test_fractal_survives_float_seed():
    assert -1.0 <= _fractal(1.5, 20260823.0) <= 1.0


def test_simulator_accepts_all_config_values_as_floats():
    """Precies zoals HA ze doorgeeft."""
    venue = SimulatorVenue(seed=20260823.0, spread=0.2, balance=10000.0)
    candles = asyncio.run(venue.candles("XAU_USD", "1m", 400))
    assert len(candles) == 400
    candles.validate()


def test_float_and_int_seed_give_identical_markets():
    a = asyncio.run(SimulatorVenue(seed=42.0).candles("XAU_USD", "1m", 100))
    b = asyncio.run(SimulatorVenue(seed=42).candles("XAU_USD", "1m", 100))
    assert a.close == b.close


# ---------------- coercie-helpers ----------------

def test_as_int_handles_config_flow_floats():
    assert _as_int(20.0, 5) == 20
    assert _as_int("7", 5) == 7
    assert _as_int(None, 5) == 5
    assert _as_int("onzin", 5) == 5


def test_as_datetime_handles_both_position_shapes():
    """Paper-trades bewaren ISO-strings, venue-posities datetimes."""
    assert _as_datetime(NOW, NOW) == NOW
    assert _as_datetime("2026-08-23T12:00:00+00:00", NOW) == NOW
    # Zonder tijdzone: aannemen dat het UTC is, niet crashen op naive/aware
    assert _as_datetime("2026-08-23T12:00:00", NOW).tzinfo is not None
    assert _as_datetime(None, NOW) == NOW
    assert _as_datetime("kapot", NOW) == NOW


def test_exit_manager_works_with_string_open_time_via_coercion():
    """De tweede latente fout: een string doorgeven waar een datetime hoort,
    zou crashen op (now - opened).total_seconds()."""
    manager = ExitManager(ExitConfig())
    opened = _as_datetime("2026-08-23T11:58:00+00:00", NOW)
    action = manager.evaluate(
        side="buy", volume=1.0, open_price=3300.0, current_stop=3299.6,
        bid=3300.35, ask=3300.55, atr=0.4, opened_at=opened, now=NOW,
        round_trip_cost_per_oz=0.24,
    )
    assert action.kind in ("hold", "modify_stop", "partial_close", "close")
