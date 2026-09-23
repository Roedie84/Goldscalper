"""Het indicatorlab.

Twintig indicatoren toetsen op dezelfde data vindt er altijd een paar die
"werken" - puur door toeval. Deze tests bewaken de twee beveiligingen die dat
moeten voorkomen: de scheiding tussen ontdekken en bevestigen, en de drempel
die met het aantal toetsen meestijgt.
"""
import asyncio
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.indicator_lab import (
    DISCOVERY_SHARE, INDICATOREN, _uitkomst, drempel_t, run_lab,
)
from gold_scalper.analysis.signals import Candles
from gold_scalper.broker.simulator import SimulatorVenue


def _bars(seed=11, n=1822):
    return asyncio.run(SimulatorVenue(seed=seed).candles("XAU_USD", "15m", n))


# ---------------- de drempel ----------------

@pytest.mark.parametrize("n,verwacht", [(1, 1.96), (5, 2.58), (10, 2.81), (22, 3.06)])
def test_the_threshold_rises_with_the_number_of_tests(n, verwacht):
    """Bij twintig toetsen op t = 2,0 is de kans op minstens één vals
    positief ongeveer 64 procent."""
    assert drempel_t(n) == pytest.approx(verwacht, abs=0.03)


def test_the_lab_uses_the_raised_threshold():
    rapport = run_lab(_bars())
    assert rapport.toetsen == len(INDICATOREN)
    assert rapport.drempel == drempel_t(len(INDICATOREN)) > 2.9


# ---------------- ontdekken en bevestigen ----------------

def test_discovery_and_confirmation_are_separate():
    rapport = run_lab(_bars())
    assert rapport.ontdekking_bars == int(rapport.bars * DISCOVERY_SHARE)
    assert rapport.ontdekking_bars + rapport.bevestiging_bars == rapport.bars


def test_pure_noise_produces_no_winner():
    """De kern. Op willekeurige koersen hoort geen enkele indicator te slagen.

    Faalt deze test, dan lekt er informatie uit de toekomst of is de drempel te
    laag - en dan zou het lab op echte data toevalstreffers als ontdekking
    presenteren.
    """
    random.seed(3)
    prijs, bars = 4300.0, []
    for i in range(2000):
        o = prijs
        prijs += random.gauss(0, 2.0)
        h = max(o, prijs) + abs(random.gauss(0, 1.0))
        l = min(o, prijs) - abs(random.gauss(0, 1.0))
        bars.append((1_700_000_000 + i * 900, o, h, l, prijs, 100.0))
    ruis = Candles(*[list(k) for k in zip(*bars)])

    rapport = run_lab(ruis)
    assert not [r for r in rapport.resultaten if r.geslaagd], (
        "een indicator slaagt op zuivere ruis"
    )


def test_the_direction_is_fixed_before_confirmation():
    """De richting wordt in de ontdekking gekozen en in de bevestiging niet
    meer aangepast. Dat is precies waarom sommige indicatoren in de
    bevestiging sterk negatief uitvallen: hun ontdekte richting hield geen
    stand."""
    rapport = run_lab(_bars())
    for r in rapport.resultaten:
        if r.trades:
            assert r.richting in ("hoog = long, laag = short",
                                  "hoog = short, laag = long")


# ---------------- uitkomsten ----------------

def _vlak(n=30, prijs=100.0):
    return Candles(
        list(range(n)), [prijs] * n, [prijs] * n, [prijs] * n, [prijs] * n, [1.0] * n,
    )


def test_a_bar_hitting_both_counts_as_the_stop():
    """Binnen een bar is de volgorde onbekend; de gunstige aanname zou elke
    uitkomst te rooskleurig maken."""
    c = _vlak()
    c.high[1], c.low[1] = 110.0, 90.0
    res, _ = _uitkomst(c, 0, 1, 2.0, 1.5, 1.0, 16)
    assert res == pytest.approx(-2.0)


def test_the_target_is_reached():
    c = _vlak()
    c.high[3] = 103.5
    res, duur = _uitkomst(c, 0, 1, 2.0, 1.5, 1.0, 16)
    assert res == pytest.approx(3.0) and duur == 3


def test_a_short_mirrors():
    c = _vlak()
    c.low[2] = 96.5
    res, _ = _uitkomst(c, 0, -1, 2.0, 1.5, 1.0, 16)
    assert res == pytest.approx(3.0)


# ---------------- verslag ----------------

def test_a_baseline_is_reported():
    """Zonder ijkpunt is niet te zien of een indicator iets toevoegt, of
    alleen meelift op een markt die één kant op ging."""
    nullijn = run_lab(_bars()).nullijn
    assert "altijd_long" in nullijn and "altijd_short" in nullijn


def test_binary_indicators_are_tested():
    rapport = run_lab(_bars())
    psar = next(r for r in rapport.resultaten if r.naam == "psar_kant")
    assert psar.trades > 0, psar.reden


def test_too_short_an_archive_gives_no_verdict():
    rapport = run_lab(_bars(n=300))
    assert "400" in rapport.conclusie and not rapport.resultaten


def test_no_volume_indicators():
    """Bij een CFD is het volume een tikteller; een indicator op een nepgetal
    levert een nepresultaat."""
    for naam in INDICATOREN:
        for verboden in ("obv", "vwap", "money_flow", "chaikin", "klinger", "force"):
            assert verboden not in naam
