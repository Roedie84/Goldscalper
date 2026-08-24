"""Een bewijsfase moet herstarts overleven.

Elke herstart begon een nieuwe run, waardoor de teller op nul sprong. Bij een
eis van 500 trades over 30 dagen betekent dat: onhaalbaar, want één Home
Assistant-update wist de voortgang. In de diagnostiek was dat zichtbaar als
run_id 4, 6, 8, 10, 12 over enkele herstarts heen.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.storage.database import TradeDatabase, Trade


@pytest.fixture
def db(tmp_path):
    database = TradeDatabase(tmp_path / "runs.db")
    database.connect()
    return database


def test_same_fingerprint_continues_the_run(db):
    first = db.start_run("paper", "v1", "XAU_USD", {}, 1000.0, None, "abc")
    found = db.find_matching_run("abc")
    assert found is not None
    assert found["id"] == first


def test_different_fingerprint_starts_fresh(db):
    db.start_run("paper", "v1", "XAU_USD", {}, 1000.0, None, "abc")
    assert db.find_matching_run("xyz") is None


def test_ended_run_is_not_resumed(db):
    first = db.start_run("paper", "v1", "XAU_USD", {}, 1000.0, None, "abc")
    db.end_run(first)
    assert db.find_matching_run("abc") is None


def test_trades_survive_across_restarts(db):
    """De kern: na een herstart tellen de eerdere trades gewoon mee."""
    run = db.start_run("paper", "v1", "XAU_USD", {}, 1000.0, None, "abc")
    for i in range(5):
        db.insert_trade(Trade(
            run_id=run, mode="paper", symbol="XAU_USD", side="buy", volume=0.01,
            open_time=f"2026-08-24T1{i}:00:00+00:00", open_price=3300.0,
            open_mid=3300.0, open_spread=0.2,
            close_time=f"2026-08-24T1{i}:05:00+00:00", net_pnl=1.0, gross_pnl=1.2,
            total_cost=0.2,
        ))
    resumed = db.find_matching_run("abc")
    assert len(db.closed_trades(resumed["id"])) == 5


def test_earlier_runs_stay_visible(db):
    """Bij een echte wijziging begint een nieuwe run, maar de oude data blijft."""
    old = db.start_run("paper", "v1", "XAU_USD", {"venue": "simulator"}, 1000.0, None, "a")
    db.insert_trade(Trade(
        run_id=old, mode="paper", symbol="XAU_USD", side="buy", volume=0.01,
        open_time="2026-08-24T10:00:00+00:00", open_price=3300.0, open_mid=3300.0,
        open_spread=0.2, close_time="2026-08-24T10:05:00+00:00",
        net_pnl=2.0, gross_pnl=2.2, total_cost=0.2,
    ))
    new = db.start_run("paper", "v1", "XAU_USD", {"venue": "public_data"}, 1000.0, None, "b")

    totals = {r["id"]: r for r in db.run_totals()}
    assert totals[old]["trades"] == 1
    assert totals[old]["net_pnl"] == pytest.approx(2.0)
    assert totals[new]["trades"] == 0


def test_old_database_gets_the_new_column(tmp_path):
    """Een bestaande database mag niet stukgaan op een ontbrekende kolom;
    dat is precies de data die je niet kwijt wilt."""
    import sqlite3
    path = tmp_path / "oud.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """CREATE TABLE runs (
             id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
             ended_at TEXT, mode TEXT NOT NULL, strategy_version TEXT NOT NULL,
             symbol TEXT NOT NULL, config_json TEXT NOT NULL,
             starting_balance REAL NOT NULL, note TEXT);"""
    )
    legacy.execute(
        "INSERT INTO runs (started_at, mode, strategy_version, symbol, "
        "config_json, starting_balance) VALUES ('2026-08-01','paper','v1',"
        "'XAU_USD','{}',1000.0)"
    )
    legacy.commit(); legacy.close()

    database = TradeDatabase(path)
    database.connect()
    columns = {
        r["name"] for r in database.conn.execute("PRAGMA table_info(runs)").fetchall()
    }
    assert "fingerprint" in columns
    assert len(database.list_runs()) == 1  # bestaande rij behouden


def test_fingerprint_ignores_risk_limits():
    """Risicolimieten begrenzen de schade maar veranderen de signalen niet;
    ze aanpassen mag de bewijsfase niet resetten."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text()
    body = source.split("def _fingerprint(")[1].split("\n    async def ")[0]
    for excluded in ("max_daily_loss", "equity_floor", "max_trades_per_day",
                     "consecutive_losses", "starting_balance"):
        assert excluded not in body, f"{excluded} hoort niet in de vingerafdruk"
    for included in ("venue", "symbol", "timeframe", "entry_threshold",
                     "assumed_spread", "regime_switching"):
        assert included in body, f"{included} hoort wél in de vingerafdruk"
