"""Rekenen met twee valuta zonder ze door elkaar te halen.

Goud noteert in dollars, het account staat in euro's. De positiegrootte deelde
een risicobudget in euro's door een stopafstand in dollars, wat bij een koers
rond 1,08 een positie oplevert die zo'n acht procent te groot is.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.currency import (
    Conversion, derive_rate, derive_rate_from_position,
)


def test_same_currency_needs_nothing():
    c = Conversion(instrument="USD", account="USD")
    assert not c.needed and c.usable
    assert c.to_account(100.0) == 100.0
    assert c.note() is None


def test_different_currency_without_rate_is_flagged():
    """Een geschatte koers is hier gevaarlijker dan geen koers: hij maakt de
    fout onzichtbaar in plaats van zichtbaar."""
    c = Conversion(instrument="USD", account="EUR")
    assert c.needed and not c.usable
    assert c.to_account(100.0) == 100.0      # ongewijzigd, niet gegokt
    assert "acht procent" in c.note()


def test_conversion_both_ways():
    c = Conversion(instrument="USD", account="EUR", rate=0.926)
    assert c.to_account(108.0) == pytest.approx(100.0, abs=0.5)
    assert c.to_instrument(100.0) == pytest.approx(108.0, abs=0.5)


# ---------------- koers afleiden ----------------

def test_rate_from_a_position():
    """IG meldt de onrealiseerde winst in accountvaluta terwijl de
    prijsbeweging in instrumentvaluta staat. De verhouding is de koers, en die
    komt dus van de broker zelf."""
    # Long 10 oz van 4400 naar 4410 = 100 dollar beweging.
    # Meldt de broker 92,60 euro, dan is de koers 0,926.
    koers = derive_rate_from_position(
        unrealised_account=92.60, open_price=4400.0,
        current_price=4410.0, units=10.0, side="buy",
    )
    assert koers == pytest.approx(0.926, abs=0.001)


def test_rate_from_a_short_position():
    koers = derive_rate_from_position(
        unrealised_account=92.60, open_price=4410.0,
        current_price=4400.0, units=10.0, side="sell",
    )
    assert koers == pytest.approx(0.926, abs=0.001)


def test_a_tiny_move_gives_no_rate():
    """Onder één eenheid bepaalt afronding de uitkomst."""
    assert derive_rate_from_position(0.09, 4400.0, 4400.01, 10.0, "buy") is None


def test_an_absurd_rate_is_refused():
    """Geen koers is beter dan een verkeerde."""
    assert derive_rate_from_position(5000.0, 4400.0, 4410.0, 10.0, "buy") is None
    assert derive_rate(10000.0, 1.0) is None


def test_missing_data_gives_no_rate():
    assert derive_rate_from_position(None, 4400.0, 4410.0, 10.0, "buy") is None
    assert derive_rate_from_position(100.0, 4400.0, None, 10.0, "buy") is None


# ---------------- gevolg voor de positiegrootte ----------------

def test_sizing_converts_the_budget():
    from gold_scalper.strategy.sizing import SizingConfig, position_size

    zonder = position_size(
        SizingConfig(risk_based=True, risk_per_trade_pct=0.1, max_units=50.0),
        10000.0, 4400.0, 4393.0,
    )
    met = position_size(
        SizingConfig(risk_based=True, risk_per_trade_pct=0.1, max_units=50.0,
                     account_to_instrument=1.08),
        10000.0, 4400.0, 4393.0,
    )
    # Een euro is meer dollars waard, dus het budget in dollars is groter.
    assert met.units == pytest.approx(zonder.units * 1.08, rel=0.01)
    assert "naar instrumentvaluta" in met.reason


def test_sizing_without_a_rate_does_not_guess():
    from gold_scalper.strategy.sizing import SizingConfig, position_size

    result = position_size(
        SizingConfig(risk_based=True, risk_per_trade_pct=0.1, max_units=50.0,
                     account_to_instrument=None),
        10000.0, 4400.0, 4393.0,
    )
    assert "naar instrumentvaluta" not in result.reason
