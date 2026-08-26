"""De positielimiet uitsplitsen naar richting.

Voorheen zag je alleen "max_positions" in de signaaltrechter. Dat verbergt het
verschil tussen een herhaald signaal in dezelfde richting - onschuldig - en
vastzitten in een long terwijl je systeem short denkt. Dat tweede is het
overwegen waard; het eerste niet.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles
from gold_scalper.broker.simulator import SimulatorVenue
from gold_scalper.strategy.scalping import ScalpConfig, evaluate


@pytest.fixture(scope="module")
def market():
    venue = SimulatorVenue(seed=20260825)
    candles = asyncio.run(venue.candles("XAU_USD", "5m", 900))
    window = slice(300, 700)
    return Candles(
        candles.timestamp[window], candles.open[window], candles.high[window],
        candles.low[window], candles.close[window], candles.volume[window],
    )


def _signal(market, count, side, threshold=0.0):
    """Drempel op nul: deze tests gaan over de uitsplitsing naar richting, niet
    over de vraag of er toevallig een signaal sterk genoeg is. Met de
    standaarddrempel hangt de uitkomst af van de marktreeks, en dan meet je
    iets anders dan je denkt."""
    # Ook het volatiliteitsfilter uitzetten: deze tests gaan over de
    # uitsplitsing naar richting, niet over de vraag of de markt toevallig
    # beweeglijk genoeg is. Anders meet je iets anders dan je denkt.
    cfg = ScalpConfig(
        commission_per_lot_per_side=0.0, volume=0.10,
        # Alle filters open: deze tests gaan over de uitsplitsing naar
        # richting, niet over de vraag of de markt toevallig beweeglijk
        # genoeg is of de spread smal genoeg. Anders meet je iets anders
        # dan je denkt, en slaagt de test om de verkeerde reden.
        max_spread=9.0, max_spread_atr_ratio=1.0,
        quiet_floor=0.0, min_edge_multiple=0.01,
        entry_threshold=threshold,
    )
    price = market.close[-1]
    return evaluate(market, price - 0.41, price + 0.41, cfg, 12, count, 1e9, side)


def test_no_position_is_not_blocked(market):
    assert _signal(market, 0, 0).reject_reason != "max_positions"


def _signal_direction(market) -> int:
    """Welke kant wijst het signaal op deze reeks op?

    Uitlezen in plaats van aannemen. De richting kantelt bij elke wijziging in
    de strategie, en een test die hem hardcodeert faalt dan om een reden die
    niets met de uitsplitsing te maken heeft - wat hij juist zou moeten toetsen.
    """
    return _signal(market, 0, 0).direction


def test_same_direction_is_labelled(market):
    richting = _signal_direction(market)
    signal = _signal(market, 1, richting)
    assert signal.reject_reason == "max_positions_zelfde_richting"
    assert "bevestigt" in signal.reason


def test_opposite_direction_is_labelled(market):
    """De situatie die er werkelijk toe doet."""
    signal = _signal(market, 1, -_signal_direction(market))
    assert signal.reject_reason == "max_positions_tegengesteld"
    assert "andere richting" in signal.reason


def test_weak_signal_is_labelled_separately(market):
    """Onder de drempel zou er toch niets gebeuren; dat is iets anders dan
    vastzitten met een sterk tegensignaal."""
    signal = _signal(market, 1, _signal_direction(market), threshold=0.99)
    assert signal.reject_reason == "max_positions_geen_signaal"


def test_the_score_survives_the_rejection(market):
    """Een weigering met score nul verbergt of het signaal sterk was. -0,08 is
    ruis; -0,72 betekent dat je vastzit in iets waarvan je systeem het
    tegenovergestelde denkt."""
    free = _signal(market, 0, 0)
    blocked = _signal(market, 1, -1)
    assert blocked.score == pytest.approx(free.score)
    assert blocked.score != 0.0


def test_weak_signal_gets_its_own_label(market):
    """Een positie open én geen signaal is geen conflict; dat hoort niet in
    dezelfde categorie als een tegensignaal."""
    cfg = ScalpConfig(
        commission_per_lot_per_side=0.0, volume=0.10,
        # Alle filters open: deze tests gaan over de uitsplitsing naar
        # richting, niet over de vraag of de markt toevallig beweeglijk
        # genoeg is of de spread smal genoeg. Anders meet je iets anders
        # dan je denkt, en slaagt de test om de verkeerde reden.
        max_spread=9.0, max_spread_atr_ratio=1.0,
        quiet_floor=0.0, min_edge_multiple=0.01,
        entry_threshold=0.99,
    )
    price = market.close[-1]
    signal = evaluate(market, price - 0.41, price + 0.41, cfg, 12, 1, 1e9, 1)
    assert signal.reject_reason == "max_positions_geen_signaal"


def test_cooldown_does_not_mask_the_split(market):
    """Met een open positie is de cooldown irrelevant; hij mag de uitsplitsing
    niet overschaduwen."""
    cfg = ScalpConfig(
        commission_per_lot_per_side=0.0, volume=0.10,
        # Alle filters open: deze tests gaan over de uitsplitsing naar
        # richting, niet over de vraag of de markt toevallig beweeglijk
        # genoeg is of de spread smal genoeg. Anders meet je iets anders
        # dan je denkt, en slaagt de test om de verkeerde reden.
        max_spread=9.0, max_spread_atr_ratio=1.0,
        quiet_floor=0.0, min_edge_multiple=0.01,
        entry_threshold=0.0,
    )
    price = market.close[-1]
    signal = evaluate(market, price - 0.41, price + 0.41, cfg, 12, 1, 0.0, -1)
    assert signal.reject_reason.startswith("max_positions_")


def test_coordinator_passes_the_side():
    """Zonder de richting valt er niets uit te splitsen."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    assert 'getattr(first, "side", "buy")' in source
    assert "self._last_entry_ts, side," in source


def test_unknown_direction_is_not_guessed(market):
    """Zonder bekende richting is "zelfde richting" een aanname die je niet
    kunt doen; dan is eerlijk zeggen dat het onbekend is beter dan een label
    dat toevallig klopt."""
    signal = _signal(market, 1, 0)
    assert signal.reject_reason == "max_positions_richting_onbekend"
