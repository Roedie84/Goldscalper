"""Indicatorwaarden op het instapmoment vastleggen.

Deze werden berekend, gebruikt voor de beslissing en weggegooid. Zonder ze is
geen correlatieanalyse mogelijk: er is niets om de uitkomst tegen af te zetten.

Een vraag als "welke marktomstandigheden zijn winstgevend" is dan
onbeantwoordbaar - niet vanwege te weinig trades, maar omdat de gegevens
ontbreken. Van de zestien kenmerken die een edge-analyse vraagt, werden er zes
niet bewaard, en dat waren precies de indicatorwaarden.
"""
import asyncio
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles
from gold_scalper.broker.simulator import SimulatorVenue
from gold_scalper.storage.database import Trade, TradeDatabase
from gold_scalper.strategy.scalping import ScalpConfig, evaluate

GEVRAAGD = (
    "entry_atr", "entry_adx", "entry_rsi",
    "entry_ema_dist", "entry_trend", "entry_momentum",
)


def _signal():
    cfg = ScalpConfig(
        entry_threshold=0.0, quiet_floor=0.0, min_edge_multiple=0.01,
        max_spread=9.0, max_spread_atr_ratio=1.0,
        commission_per_lot_per_side=0.0, volume=0.10,
    )
    venue = SimulatorVenue(seed=42)
    candles = asyncio.run(venue.candles("XAU_USD", "15m", 700))
    prijs = candles.close[-1]
    return evaluate(candles, prijs - 0.3, prijs + 0.3, cfg, 12, 0)


def test_the_signal_carries_raw_values():
    """De scores lopen van -1 tot 1 en zijn niet te vergelijken tussen markten
    of periodes. Voor een correlatieanalyse zijn de ruwe waarden nodig: een RSI
    van 28 zegt iets anders dan een score van -0,72, ook al komt het tweede uit
    het eerste."""
    comp = _signal().components or {}
    assert comp.get("atr") and comp["atr"] > 0
    assert "ema_dist" in comp
    assert comp.get("adx") is not None


def test_the_columns_exist():
    db_pad = ":memory:"
    db = TradeDatabase(db_pad)
    db.connect()
    kolommen = {
        r["name"]
        for r in db.conn.execute("PRAGMA table_info(trades)").fetchall()
    }
    ontbreekt = [k for k in GEVRAAGD if k not in kolommen]
    assert ontbreekt == [], f"kolommen ontbreken: {ontbreekt}"


def test_the_values_survive_a_round_trip(tmp_path):
    db = TradeDatabase(tmp_path / "f.db")
    db.connect()
    run = db.start_run("demo", "v1", "GOLD", {}, 10000.0, None, "fp")

    trade = Trade(
        run_id=run, mode="demo", symbol="GOLD", side="buy", volume=0.016,
        open_time="2026-09-20T10:00:00+00:00", open_price=4300.0,
        open_mid=4299.7, open_spread=0.6,
        entry_atr=7.05, entry_adx=40.6, entry_rsi=0.01,
        entry_ema_dist=-0.083, entry_trend=0.786, entry_momentum=-0.01,
    )
    db.insert_trade(trade)

    terug = db.open_trades(run)[0]
    assert terug.entry_atr == pytest.approx(7.05)
    assert terug.entry_adx == pytest.approx(40.6)
    assert terug.entry_trend == pytest.approx(0.786)
    assert terug.entry_ema_dist == pytest.approx(-0.083)


def test_an_existing_database_gets_the_columns(tmp_path):
    """Zonder migratie zou een bestaande database na een update stukgaan op een
    ontbrekende kolom, en dat is precies de data die je niet kwijt wilt."""
    pad = tmp_path / "oud.db"
    db = TradeDatabase(pad)
    db.connect()
    db.close()

    # De nieuwe kolommen weghalen om een oudere database na te bootsen.
    conn = sqlite3.connect(pad)
    kolommen = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    houden = [k for k in kolommen if not k.startswith("entry_")]
    conn.execute(
        "CREATE TABLE t2 AS SELECT " + ", ".join(houden) + " FROM trades"
    )
    conn.execute("DROP TABLE trades")
    conn.execute("ALTER TABLE t2 RENAME TO trades")
    conn.commit()
    conn.close()

    db = TradeDatabase(pad)
    db.connect()
    kolommen = {
        r["name"]
        for r in db.conn.execute("PRAGMA table_info(trades)").fetchall()
    }
    assert all(k in kolommen for k in GEVRAAGD)


def test_recording_them_changes_no_decision():
    """Puur observatie. Vangt een toekomstige poging om hier een filter van te
    maken."""
    from pathlib import Path

    bron = (Path(__file__).resolve().parent.parent / "custom_components"
            / "gold_scalper" / "strategy" / "scalping.py").read_text(encoding="utf-8")
    # Alleen het blok dat de waarden vastlegt, tot de eerste bestaande
    # weigering. Die op een nul-ATR stond er al en hoort er: zonder ATR zijn
    # geen doelen te bepalen.
    blok = bron.split('components["atr"]')[1].split("if atr_value <= 0:")[0]
    for verboden in ("return reject", "should_trade", "= False"):
        assert verboden not in blok, (
            f"het vastleggen van kenmerken beinvloedt een beslissing: {verboden}"
        )
