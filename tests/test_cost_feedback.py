"""Het kostenmodel mag zichzelf niet opblazen.

De leerlaag zette de gemeten slippage terug als basis-slippage. Maar die meting
bevat al de volatiliteitscomponent, die er vervolgens opnieuw bovenop kwam.
Elke ronde telde hij dubbel: na veertig trades stond de slippage op het
zesvoudige en was een bruto winstgevende reeks (+44) omgeslagen in een verlies
van 224 - volledig boekhouding, geen markt.

Dat is precies de val waar de leerlaag tegen waarschuwt: leren van je eigen
uitvoer in plaats van van de werkelijkheid.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.paper import BrokerCosts, PaperBroker, Quote
from gold_scalper.storage.database import TradeDatabase

T0 = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def broker(tmp_path):
    db = TradeDatabase(tmp_path / "c.db")
    db.connect()
    run = db.start_run("paper", "v", "GOLD", {}, 10000.0, None, "fp")
    costs = BrokerCosts(
        commission_per_lot_per_side=0.0, base_slippage=0.02,
        volatility_slippage_factor=0.05, size_slippage_per_lot=0.0,
    )
    return PaperBroker(db, run, "GOLD", 10000.0, costs, seed=1)


def _quote(mid=4640.0, spread=0.60, atr=4.77, seconds=0):
    return Quote(bid=mid - spread / 2, ask=mid + spread / 2,
                 time=T0 + timedelta(seconds=seconds), atr=atr)


def test_slippage_is_capped_relative_to_the_spread():
    """Vangnet tegen een kostenmodel dat op hol slaat."""
    from gold_scalper.storage.database import TradeDatabase as DB

    with tempfile.TemporaryDirectory() as folder:
        db = DB(os.path.join(folder, "x.db"))
        db.connect()
        run = db.start_run("paper", "v", "GOLD", {}, 10000.0, None, "fp")
        # Absurde basis-slippage, zoals de terugkoppelingslus die opleverde.
        costs = BrokerCosts(commission_per_lot_per_side=0.0, base_slippage=5.0,
                            volatility_slippage_factor=0.05)
        broker = PaperBroker(db, run, "GOLD", 10000.0, costs, seed=1)
        quote = _quote()
        slippage = broker._slippage(quote, 0.01)
    assert slippage <= quote.spread * PaperBroker.MAX_SLIPPAGE_SPREAD_MULTIPLE


def test_costs_stay_in_the_expected_range(broker):
    """Bij een spread van 0,60 hoort een round trip ongeveer 1,10 te kosten,
    niet 6,70."""
    quote = _quote()
    trade = broker.open_position("buy", 0.01, quote)
    closed = broker.close_position(trade, _quote(4641.0, seconds=300), "test")
    assert 0.6 <= closed.total_cost <= 2.5, closed.total_cost


def test_costs_do_not_grow_over_repeated_trades(broker):
    """De kern van de storing: kosten die per trade oplopen."""
    costs = []
    for i in range(30):
        quote = _quote(seconds=i * 600)
        trade = broker.open_position("buy", 0.01, quote)
        closed = broker.close_position(
            trade, _quote(4640.5, seconds=i * 600 + 300), "test"
        )
        costs.append(closed.total_cost)

    first_five = sum(costs[:5]) / 5
    last_five = sum(costs[-5:]) / 5
    assert last_five < first_five * 1.5, (
        f"kosten liepen op van {first_five:.2f} naar {last_five:.2f}"
    )


def test_learning_does_not_write_back_into_the_cost_model():
    """De gemeten slippage mag de verwachting bijstellen, niet het model dat
    die meting produceert."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "custom_components"
        / "gold_scalper" / "coordinator.py"
    ).read_text(encoding="utf-8")
    body = source.split("async def _relearn")[1].split("\n    def _fingerprint")[0]

    assert "self.strategy_cfg.expected_slippage" in body
    assert "self.paper.costs.base_slippage =" not in body, (
        "de leerlaag schrijft terug in het kostenmodel dat de meting voedt"
    )


def test_implausible_slippage_is_reported():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "custom_components"
        / "gold_scalper" / "coordinator.py"
    ).read_text(encoding="utf-8")
    body = source.split("async def _relearn")[1].split("\n    def _fingerprint")[0]
    assert "_LOGGER.warning" in body
    assert "kostenboeking" in body
