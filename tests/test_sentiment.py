"""Klantsentiment verzamelen, en afgesloten bars archiveren in beide paden.

De broker bewaart geen historie van zijn klantsentiment. Wie wil weten of het
iets voorspelt, moet het zelf verzamelen - elke dag die niet wordt vastgelegd
is voorgoed weg.

Wat er over bekend is valt tegen: twaalf jaar uurdata van een andere broker
liet zien dat retailpositionering de koers niet voorspelt. Een effect bij
extreme standen is niet uitgesloten, en dat is wat hier gemeten wordt.

Uitsluitend meting. Niets hiervan beïnvloedt een beslissing.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.storage.bar_archive import BarArchive


# ---------------- de adapter ----------------

def _ig(routes):
    """Een IG-adapter met vastgelegde antwoorden, zoals elders in de tests."""
    from test_ig_capital import ig
    return ig(routes)


def test_sentiment_is_read_from_the_broker():
    venue = _ig({"/clientsentiment/GOLD": ({
        "longPositionPercentage": 72.0, "shortPositionPercentage": 28.0,
        "marketId": "GOLD",
    }, 200)})
    venue._market_id = "GOLD"
    stand = asyncio.run(venue.client_sentiment())
    assert stand == {"long": 72.0, "short": 28.0, "market_id": "GOLD"}


def test_no_market_id_means_no_reading():
    """Een ontbrekende meting hoort geen getal te worden."""
    venue = _ig({})
    venue._market_id = None
    assert asyncio.run(venue.client_sentiment()) is None


def test_an_incomplete_answer_gives_no_reading():
    venue = _ig({"/clientsentiment/GOLD": ({"longPositionPercentage": 72.0}, 200)})
    venue._market_id = "GOLD"
    assert asyncio.run(venue.client_sentiment()) is None


def test_the_market_id_comes_with_the_quote():
    """Geen extra verzoek: het marktnummer staat al in de koersopvraging."""
    from pathlib import Path

    bron = (Path(__file__).resolve().parent.parent / "custom_components"
            / "gold_scalper" / "broker" / "ig_capital.py").read_text(encoding="utf-8")
    quote = bron.split("async def quote")[1].split("async def ")[0]
    assert "marketId" in quote and "self._market_id" in quote


# ---------------- het archief ----------------

@pytest.fixture
def archive(tmp_path):
    a = BarArchive(tmp_path / "bars.db")
    a.connect()
    yield a
    a.close()


T0 = int(datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp())


def test_sentiment_is_stored_per_bar(archive):
    archive.store_sentiment("GOLD", T0, 72.0, 28.0)
    archive.store_sentiment("GOLD", T0 + 900, 81.0, 19.0)
    stats = archive.sentiment_stats("GOLD")
    assert stats["waarnemingen"] == 2
    assert stats["extreem"] == 1


def test_extremes_count_on_both_sides(archive):
    """Een extreme stand is 75% of meer aan één kant - long of short."""
    archive.store_sentiment("GOLD", T0, 80.0, 20.0)
    archive.store_sentiment("GOLD", T0 + 900, 20.0, 80.0)
    archive.store_sentiment("GOLD", T0 + 1800, 60.0, 40.0)
    assert archive.sentiment_stats("GOLD")["extreem"] == 2


def test_progress_towards_the_test(archive):
    """De vooraf vastgelegde toets vraagt tweehonderd extreme waarnemingen."""
    for i in range(50):
        archive.store_sentiment("GOLD", T0 + i * 900, 80.0, 20.0)
    stats = archive.sentiment_stats("GOLD")
    assert stats["nodig_voor_toets"] == 200
    assert stats["voortgang"] == pytest.approx(0.25)


def test_the_same_bar_is_not_counted_twice(archive):
    archive.store_sentiment("GOLD", T0, 80.0, 20.0)
    archive.store_sentiment("GOLD", T0, 81.0, 19.0)
    assert archive.sentiment_stats("GOLD")["waarnemingen"] == 1


def test_an_empty_collection_reports_zero(archive):
    stats = archive.sentiment_stats("GOLD")
    assert stats["waarnemingen"] == 0 and stats["gemiddeld_long"] is None


# ---------------- de coordinator ----------------

def _bron():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "custom_components"
            / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")


def test_broker_bars_are_archived_too():
    """Eerst gebeurde het archiveren alleen in het pad voor zelfgebouwde bars.
    Wie de historie van de broker gebruikte, kreeg een archief dat stilstond:
    1817 bars, en dagen later 1822."""
    bron = _bron()
    brokerpad = bron.split("async def _maybe_update_candles")[1].split("\n    async def ")[0]
    assert "_on_bars_closed" in brokerpad


def test_both_paths_share_one_handler():
    bron = _bron()
    assert bron.count("await self._on_bars_closed(") >= 2


def test_measurement_never_breaks_the_loop():
    bron = _bron()
    blok = bron.split("async def _on_bars_closed")[1].split("\n    async def ")[0]
    assert blok.count("except Exception") >= 3


def test_nothing_here_makes_a_decision():
    """Vangt een toekomstige poging om van het sentiment een filter te maken."""
    from pathlib import Path

    scalping = (Path(__file__).resolve().parent.parent / "custom_components"
                / "gold_scalper" / "strategy" / "scalping.py").read_text(encoding="utf-8")
    assert "sentiment" not in scalping.lower()

    blok = scalping.split('components["williams_r"]')[0].split("williams_r(candles")[0]
    meting = scalping.split("from ..analysis.momentum import cci, williams_r")[1]
    meting = meting.split("if atr_value <= 0:")[0]
    for verboden in ("return reject", "should_trade", "score"):
        assert verboden not in meting, f"de meting beinvloedt de beslissing: {verboden}"


def test_the_new_columns_exist(tmp_path):
    from gold_scalper.storage.database import TradeDatabase

    db = TradeDatabase(tmp_path / "t.db")
    db.connect()
    kolommen = {r["name"] for r in db.conn.execute("PRAGMA table_info(trades)")}
    for k in ("entry_sentiment_long", "entry_williams_r", "entry_cci"):
        assert k in kolommen, k
