"""Het barsarchief.

De beperking van dit project is niet het aantal ideeën maar het aantal
metingen. Bij vijfentwintig trades per dag kost één hypothese drie weken; met
een jaar historie is dezelfde toets in een minuut gedaan.

Tot nu toe werden bars in het geheugen verzameld en bij elke herstart
weggegooid.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles
from gold_scalper.storage.bar_archive import BarArchive

T0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
STAP = 900   # M15


def _candles(n: int, start: int = T0, stap: int = STAP, gat_na: int | None = None):
    ts, o, h, l, c, v = [], [], [], [], [], []
    t = start
    for i in range(n):
        prijs = 4400.0 + i * 0.5
        ts.append(t); o.append(prijs); h.append(prijs + 2)
        l.append(prijs - 2); c.append(prijs + 0.5); v.append(100.0)
        t += stap * (12 if gat_na is not None and i == gat_na else 1)
    return Candles(ts, o, h, l, c, v)


@pytest.fixture
def archive(tmp_path):
    a = BarArchive(tmp_path / "bars.db")
    a.connect()
    yield a
    a.close()


def test_bars_survive_a_restart(tmp_path):
    """De kern: zonder dit werd elke verzameling bij een herstart weggegooid."""
    pad = tmp_path / "bars.db"
    eerste = BarArchive(pad)
    eerste.connect()
    eerste.store("GOLD", "15m", _candles(100))
    eerste.close()

    tweede = BarArchive(pad)
    tweede.connect()
    assert tweede.count("GOLD", "15m") == 100
    assert len(tweede.load("GOLD", "15m")) == 100


def test_duplicates_are_not_counted_twice(archive):
    """Zonder ontdubbeling telt een backtest dezelfde beweging twee keer mee."""
    archive.store("GOLD", "15m", _candles(50))
    archive.store("GOLD", "15m", _candles(50))
    assert archive.count("GOLD", "15m") == 50


def test_a_later_fetch_overwrites(archive):
    """Een bar die de broker levert is nauwkeuriger dan een bar die uit losse
    koersen is opgebouwd."""
    archive.store("GOLD", "15m", _candles(10), "quotes")
    beter = _candles(10)
    beter.high[0] = 9999.0
    archive.store("GOLD", "15m", beter, "broker")
    assert archive.load("GOLD", "15m").high[0] == 9999.0


def test_symbols_and_timeframes_are_separate(archive):
    archive.store("GOLD", "15m", _candles(10))
    archive.store("GOLD", "5m", _candles(20, stap=300))
    archive.store("SILVER", "15m", _candles(30))
    assert archive.count("GOLD", "15m") == 10
    assert archive.count("GOLD", "5m") == 20
    assert archive.count("SILVER", "15m") == 30


# ---------------- gaten ----------------

def test_gaps_are_counted(archive):
    """Een backtest over een reeks met onopgemerkte gaten meet iets anders dan
    hij denkt: de bar na een gat van drie uur ziet eruit als een enorme
    beweging."""
    archive.store("GOLD", "15m", _candles(50, gat_na=20))
    stats = archive.stats("GOLD", "15m")
    assert stats.gaps == 1
    assert stats.largest_gap_bars == 11


def test_coverage_reflects_the_gaps(archive):
    zonder = BarArchive(archive.path)
    archive.store("GOLD", "15m", _candles(50))
    vol = archive.stats("GOLD", "15m").coverage
    assert vol == pytest.approx(1.0)


def test_an_empty_archive_reports_nothing(archive):
    stats = archive.stats("GOLD", "15m")
    assert stats.bars == 0 and stats.coverage == 0.0


def test_loading_an_empty_archive_raises(archive):
    """Stil een lege reeks teruggeven zou een backtest op nul bars laten
    draaien en een uitkomst van nul opleveren die eruitziet als een meting."""
    with pytest.raises(ValueError, match="Geen bars"):
        archive.load("GOLD", "15m")


# ---------------- selectie ----------------

def test_a_range_can_be_loaded(archive):
    archive.store("GOLD", "15m", _candles(100))
    deel = archive.load("GOLD", "15m", start=T0 + 10 * STAP, end=T0 + 19 * STAP)
    assert len(deel) == 10


def test_a_limit_takes_the_most_recent(archive):
    """Bij een limiet zijn de laatste bars de relevante."""
    archive.store("GOLD", "15m", _candles(100))
    laatste = archive.load("GOLD", "15m", limit=10)
    assert len(laatste) == 10
    assert laatste.timestamp[-1] == T0 + 99 * STAP


def test_bars_come_back_in_order(archive):
    archive.store("GOLD", "15m", _candles(100))
    stamps = archive.load("GOLD", "15m").timestamp
    assert stamps == sorted(stamps)


def test_available_lists_what_is_there(archive):
    archive.store("GOLD", "15m", _candles(10))
    archive.store("GOLD", "5m", _candles(20, stap=300))
    beschikbaar = {(r["symbol"], r["timeframe"]): r["bars"]
                   for r in archive.available()}
    assert beschikbaar[("GOLD", "15m")] == 10
    assert beschikbaar[("GOLD", "5m")] == 20


def test_the_archive_never_breaks_the_loop():
    """Archiveren mag nooit de handelslus slopen; er staat een brede vangnet
    omheen in de coordinator."""
    from pathlib import Path

    bron = (Path(__file__).resolve().parent.parent / "custom_components"
            / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    blok = bron.split("Bar niet gearchiveerd")[0][-500:]
    assert "except Exception" in blok
