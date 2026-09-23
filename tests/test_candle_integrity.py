"""OHLCV-kolommen moeten altijd even lang blijven.

Deze tests komen voort uit een storing die in de diagnostiek zichtbaar werd:
`candles_loaded: 800` naast `indicator_bars: 1923`. De afkaplogica toetste op
de lengte van `close` maar stond binnen de lus over de kolommen, dus alleen
`close` werd afgekapt en de overige vijf groeiden door.

Het verraderlijke eraan is dat er niets zichtbaars misgaat: de integratie
draait door, sensoren tonen waarden, en de indicatoren rekenen ondertussen op
verschoven data.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles

COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
LIMIT = 800


def make(n: int) -> Candles:
    return Candles(list(range(n)), [1.0] * n, [2.0] * n, [0.5] * n, [1.5] * n, [10.0] * n)


def append_and_trim(candles: Candles, value: float, limit: int = LIMIT) -> None:
    """De gecorrigeerde logica: toevoegen, dán afkappen, over alle kolommen."""
    for field in COLUMNS:
        getattr(candles, field).append(value)
    overflow = len(candles.close) - limit
    if overflow > 0:
        for field in COLUMNS:
            del getattr(candles, field)[:overflow]


def test_columns_stay_equal_through_heavy_trimming():
    """1500 nieuwe candles op een venster van 800: ruim voorbij het punt waar
    de oude logica uiteenliep."""
    candles = make(400)
    for i in range(1500):
        append_and_trim(candles, float(i))
    lengths = {len(getattr(candles, f)) for f in COLUMNS}
    assert lengths == {LIMIT}
    candles.validate()


def test_no_trimming_below_the_limit():
    candles = make(400)
    for i in range(100):
        append_and_trim(candles, float(i))
    assert {len(getattr(candles, f)) for f in COLUMNS} == {500}


def test_the_old_buggy_logic_is_detectably_broken():
    """Legt vast wát er fout was, zodat de fix niet stilletjes teruggedraaid
    kan worden zonder dat een test aanslaat."""
    candles = make(400)
    for i in range(600):
        for field in COLUMNS:
            getattr(candles, field).append(float(i))
            if len(candles.close) > LIMIT:      # de fout: toets binnen de lus
                getattr(candles, field).pop(0)
    lengths = {len(getattr(candles, f)) for f in COLUMNS}
    assert len(lengths) > 1, "de oude logica hoort kolommen te laten divergeren"
    with pytest.raises(ValueError, match="ongelijke lengtes"):
        candles.validate()


def test_validate_catches_desync():
    candles = make(100)
    candles.close.pop()
    with pytest.raises(ValueError, match="ongelijke lengtes"):
        candles.validate()


def test_coordinator_appends_then_trims():
    """De volgorde in de broncode: afkappen ná het toevoegen van alle kolommen."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text()
    body = source.split("def _append_candle(")[1].split("\n    async def ")[0]
    append_pos = body.index("append(")
    trim_pos = body.index("overflow")
    assert append_pos < trim_pos, "afkappen mag niet binnen de kolomlus staan"
    assert "del getattr" in body, "alle kolommen moeten samen afgekapt worden"
