"""Opwarmen moet zich aanpassen aan wat de broker geeft.

De config flow slaagde met 120 candles, maar het opwarmen vroeg er 400 en
kreeg `error.price-history.io-error` terug - een melding die niet verklapt dat
het aantal het probleem is. Hoeveel een broker levert hangt af van instrument,
tijdsframe, omgeving en soms een weekquotum, dus een vast getal is altijd
ergens fout.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

COORDINATOR = (
    Path(__file__).resolve().parent.parent
    / "custom_components" / "gold_scalper" / "coordinator.py"
).read_text(encoding="utf-8")


def _warmup_body() -> str:
    return COORDINATOR.split("async def _fetch_warmup")[1].split("\n    def ")[0]


def _flat(text: str) -> str:
    """Maak er één regel van zonder de naden van gesplitste stringliterals.

    Python voegt aangrenzende strings samen, maar in de broncode staat er dan
    `" "` tussen. Zoeken op een zin die over twee regels loopt faalt daardoor,
    terwijl de zin in de uitvoer gewoon klopt.
    """
    # Eerst samenvoegen, dán de naden weghalen: ze ontstaan pas door het
    # samenvoegen van regels.
    flat = " ".join(text.split())
    return flat.replace('" "', "").replace('""', "")


def test_warmup_tries_progressively_smaller_counts():
    body = _warmup_body()
    assert "attempts" in body
    assert "WARMUP_CANDLES" in body and "MIN_WARMUP_CANDLES" in body


def test_warmup_accepts_fewer_than_requested():
    """Liever een kortere historie dan een integratie die niet opstart."""
    body = _warmup_body()
    assert ">= MIN_WARMUP_CANDLES" in body


def test_warmup_warns_when_it_settles_for_less():
    """Stilzwijgend minder historie gebruiken maakt indicatoren onbetrouwbaar
    zonder dat je het weet."""
    body = _flat(_warmup_body())
    assert "_LOGGER.warning" in body
    assert "minder betrouwbaar" in body


def test_final_failure_suggests_a_higher_timeframe():
    body = _flat(_warmup_body())
    assert "hoger" in body and "tijdsframe" in body


def test_minimum_matches_the_analysis_requirement():
    """De engine weigert onder de 60 candles; de ondergrens moet daarop aansluiten."""
    from gold_scalper.const import MIN_WARMUP_CANDLES
    from gold_scalper.analysis.engine import MIN_CANDLES
    assert MIN_WARMUP_CANDLES >= MIN_CANDLES


def test_both_warmup_paths_use_the_helper():
    """Ook het opnieuw opwarmen na een dataprobleem moet kunnen afbouwen."""
    assert COORDINATOR.count("_fetch_warmup()") >= 2


def test_attempts_descend():
    """Oplopend proberen zou zinloos zijn: de eerste poging moet de ruimste zijn."""
    from gold_scalper.const import MIN_WARMUP_CANDLES, WARMUP_CANDLES

    line = next(
        l for l in _warmup_body().splitlines() if "attempts = " in l
    )
    inner = line.split("[", 1)[1].rsplit("]", 1)[0]
    numbers = []
    for token in inner.split(","):
        token = token.strip()
        if not token:
            continue
        if token == "WARMUP_CANDLES":
            numbers.append(WARMUP_CANDLES)
        elif token == "MIN_WARMUP_CANDLES":
            numbers.append(MIN_WARMUP_CANDLES)
        else:
            numbers.append(int(token))
    assert numbers == sorted(numbers, reverse=True), numbers
    assert numbers[0] == WARMUP_CANDLES
    assert numbers[-1] == MIN_WARMUP_CANDLES
