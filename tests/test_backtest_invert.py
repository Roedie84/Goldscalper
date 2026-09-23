"""Het signaal omdraaien in de backtest.

De gemeten trefkans is 30,4% waar willekeurig instappen 39,3% zou opleveren bij
dezelfde exits. Een signaal dat structureel slechter is dan toeval bevat
informatie - met het verkeerde teken.

Dit is een toets met nul vrijheidsgraden: er valt niets aan af te stellen. Het
werkt of het werkt niet, en dat is precies waarom deze toets wel mag waar
parameteroptimalisatie niet mag.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.backtest import run_backtest
from gold_scalper.broker.exits import ExitConfig
from gold_scalper.broker.simulator import SimulatorVenue
from gold_scalper.strategy.scalping import ScalpConfig


def _bars(seed=11, n=1200):
    return asyncio.run(SimulatorVenue(seed=seed).candles("XAU_USD", "15m", n))


def _cfg():
    return ScalpConfig(
        entry_threshold=0.45, take_profit_atr=1.5, stop_loss_atr=1.0,
        volume=0.02, max_spread_atr_ratio=0.75,
    )


def _run(invert, seed=11):
    return run_backtest(
        _bars(seed), _cfg(), ExitConfig(), spread=0.7, slippage=0.02,
        units=2.0, bar_seconds=900, invert=invert,
    )


def test_inverting_flips_every_side():
    """Elke trade hoort de andere kant op te staan; anders draait hij maar een
    deel om en meet je iets anders dan je bedoelde."""
    gewoon = _run(False)
    omgekeerd = _run(True)

    assert len(gewoon.trades) == len(omgekeerd.trades) > 20
    for a, b in zip(gewoon.trades, omgekeerd.trades):
        assert a.side != b.side


def test_the_levels_mirror_with_the_direction():
    """Alleen de kant omdraaien zou de stop boven de instap laten liggen voor
    een short en het doel eronder: de positie sluit dan meteen op een niveau
    dat al geraakt was. Je zou iets toetsen wat je niet bedoelde en een zinloze
    uitkomst krijgen die eruitziet als een antwoord.

    Herkenbaar aan het aandeel stops: staan de niveaus verkeerd, dan sluit
    vrijwel alles direct op de stop.
    """
    omgekeerd = _run(True)
    assert [t for t in omgekeerd.trades if t.side == "sell"]
    assert [t for t in omgekeerd.trades if t.side == "buy"]

    stops = [t for t in omgekeerd.trades if t.reason == "stop_loss"]
    doelen = [t for t in omgekeerd.trades if t.reason == "take_profit"]
    assert doelen, "geen enkel doel geraakt; de niveaus staan verkeerd om"
    assert len(stops) < len(omgekeerd.trades) * 0.9


def test_the_distances_stay_the_same():
    """De verhouding tussen doel en stop hoort gelijk te blijven, anders toets
    je een andere strategie in plaats van dezelfde met een ander teken."""
    gewoon = _run(False)
    omgekeerd = _run(True)

    def verhouding(res):
        w = [t.net for t in res.trades if t.net > 0]
        v = [t.net for t in res.trades if t.net <= 0]
        if not w or not v:
            return None
        return (sum(w) / len(w)) / abs(sum(v) / len(v))

    a, b = verhouding(gewoon), verhouding(omgekeerd)
    assert a and b
    assert abs(a - b) < 0.6, f"verhouding verschilt te veel: {a:.2f} vs {b:.2f}"


def test_costs_are_still_charged_when_inverted():
    """De nulmeting die wél houdbaar is.

    Eerst stond hier dat omkeren op ruisdata nooit winst mag opleveren. Dat is
    te streng: zodra één exit anders uitvalt, verschilt ook het instapmoment
    van de volgende trade, en lopen de twee reeksen uiteen. Het zijn dus niet
    dezelfde trades gespiegeld maar twee verschillende reeksen, en dan is een
    verschil in uitkomst geen bewijs van een lek.

    Wat wél moet gelden: de kosten worden onverkort in rekening gebracht. Een
    omkering die de kosten kwijtraakt, zou winst tonen die er niet is.
    """
    res = _run(True).summary()
    assert res["total_costs"] > 0
    assert res["net_pnl"] < res["gross_pnl"], "kosten worden niet afgetrokken"

    per_trade = res["total_costs"] / max(1, res["trades"])
    assert per_trade > 0.5, f"kosten per trade onwaarschijnlijk laag: {per_trade:.2f}"


def test_the_default_is_unchanged():
    """De omkering mag nooit per ongeluk aan staan."""
    import inspect

    sig = inspect.signature(run_backtest)
    assert sig.parameters["invert"].default is False


def test_the_service_offers_it():
    from pathlib import Path

    import yaml

    pad = (Path(__file__).resolve().parent.parent / "custom_components"
           / "gold_scalper" / "services.yaml")
    diensten = yaml.safe_load(pad.read_text(encoding="utf-8"))
    assert "invert" in diensten["backtest"]["fields"]
