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
    body = source.split("def _fingerprint_material(")[1].split("\n    @staticmethod")[0]
    for excluded in ("max_daily_loss", "equity_floor", "max_trades_per_day",
                     "consecutive_losses", "starting_balance"):
        assert excluded not in body, f"{excluded} hoort niet in de vingerafdruk"
    for included in ("venue", "symbol", "timeframe", "entry_threshold",
                     "assumed_spread", "regime_switching"):
        assert included in body, f"{included} hoort wél in de vingerafdruk"


def test_fingerprint_includes_the_new_spread_settings():
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    body = source.split("def _fingerprint_material(")[1].split("\n    @staticmethod")[0]
    assert "max_spread" in body
    assert "max_spread_atr_ratio" in body


def test_option_mapping_covers_every_tunable_field():
    """Elk veld in de vingerafdruk dat de gebruiker kan instellen moet in de
    afbeelding staan; anders wordt een gewijzigde standaardwaarde ten onrechte
    als jouw keuze gezien en reset de bewijsfase."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    body = source.split("def _fingerprint_material(")[1].split("\n    @staticmethod")[0]
    mapping = source.split("_OPTION_FOR = {")[1].split("}")[0]

    tunable = [
        "entry_threshold", "regime_switching", "min_edge_multiple",
        "max_spread", "max_spread_atr_ratio", "units", "assumed_spread",
    ]
    for field in tunable:
        assert f'"{field}"' in body, f"{field} ontbreekt in de vingerafdruk"
        assert f'"{field}"' in mapping, f"{field} ontbreekt in _OPTION_FOR"


def test_adoption_only_when_user_did_not_choose():
    """De kern: een gewijzigde standaardwaarde mag de teller niet resetten,
    een instelling die jij zelf zette wél."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    body = source.split("async def _adoptable_run")[1].split("\n    #: Van ")[0]
    assert "self.entry.options" in body, "er wordt niet gekeken wat jij zelf zette"
    assert "user_chosen" in body
    assert "structural" in body


def test_fingerprint_update_preserves_run(db):
    """Voortzetten mag de run niet afsluiten of de trades kwijtraken."""
    run = db.start_run("paper", "v1", "GOLD", {"a": 1}, 1000.0, None, "oud")
    db.insert_trade(Trade(
        run_id=run, mode="paper", symbol="GOLD", side="buy", volume=0.01,
        open_time="2026-08-24T10:00:00+00:00", open_price=4642.0,
        open_mid=4642.0, open_spread=0.6,
        close_time="2026-08-24T10:06:00+00:00", net_pnl=1.0, gross_pnl=1.6,
        total_cost=0.6,
    ))
    db.update_run_fingerprint(run, "nieuw", {"max_spread": 3.0})

    assert db.find_matching_run("nieuw")["id"] == run
    assert db.find_matching_run("oud") is None
    assert len(db.closed_trades(run)) == 1
    import json
    stored = json.loads(db.get_run(run)["config_json"])
    assert stored["fingerprint_material"] == {"max_spread": 3.0}
    assert stored["a"] == 1          # bestaande configuratie behouden


def test_mode_is_part_of_the_fingerprint():
    """Papertrades hebben gemodelleerde kosten, demotrades gemeten. Die in één
    bewijsfase mengen zou de uitkomst waardeloos maken - juist het verschil
    tussen die twee is wat je wilt meten."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    body = source.split("def _fingerprint_material(")[1].split("\n    @staticmethod")[0]
    assert '"mode"' in body


def test_mode_change_is_reported_as_your_choice():
    """Van paper naar demo is jouw beslissing, dus hoort de nieuwe run
    benoemd te worden in plaats van stil te beginnen."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    mapping = source.split("_OPTION_FOR = {")[1].split("}")[0]
    assert '"mode": "mode"' in mapping


def test_existing_database_migrates_tickets(tmp_path):
    """Ticketnummers zijn niet numeriek: IG gebruikt sleutels als
    'DIAAAAYCJETQ7A8'. De kolom stond op INTEGER en heette mt5_ticket, een
    overblijfsel uit de MetaTrader-versie. De migratie mag geen trades kosten.
    """
    import sqlite3

    path = tmp_path / "oud.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """CREATE TABLE runs (
             id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
             ended_at TEXT, mode TEXT NOT NULL, strategy_version TEXT NOT NULL,
             symbol TEXT NOT NULL, config_json TEXT NOT NULL,
             starting_balance REAL NOT NULL, note TEXT);
           CREATE TABLE trades (
             id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
             mode TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
             volume REAL NOT NULL, open_time TEXT NOT NULL,
             open_price REAL NOT NULL, open_mid REAL NOT NULL,
             open_spread REAL NOT NULL, open_slippage REAL,
             close_time TEXT, close_price REAL, close_mid REAL,
             close_spread REAL, close_slippage REAL, close_reason TEXT,
             gross_pnl REAL, net_pnl REAL, total_cost REAL, commission REAL,
             swap REAL, mae REAL, mfe REAL, duration_seconds INTEGER,
             stop_loss REAL, take_profit REAL, signal_score REAL,
             regime TEXT, mt5_ticket INTEGER);"""
    )
    legacy.execute(
        "INSERT INTO runs (started_at, mode, strategy_version, symbol, "
        "config_json, starting_balance) VALUES "
        "('2026-08-01','paper','v1','GOLD','{}',1000.0)"
    )
    legacy.execute(
        "INSERT INTO trades (run_id, mode, symbol, side, volume, open_time, "
        "open_price, open_mid, open_spread, net_pnl, mt5_ticket) VALUES "
        "(1,'paper','GOLD','buy',0.01,'2026-08-01T10:00:00',4640,4640,0.6,1.2,98765)"
    )
    legacy.commit()
    legacy.close()

    db = TradeDatabase(path)
    db.connect()

    columns = {
        r["name"] for r in db.conn.execute("PRAGMA table_info(trades)").fetchall()
    }
    assert "broker_ticket" in columns

    row = db.conn.execute("SELECT broker_ticket FROM trades").fetchone()
    assert row["broker_ticket"] == "98765", "bestaande tickets zijn niet meegenomen"


def test_alphanumeric_ticket_can_be_stored(tmp_path):
    db = TradeDatabase(tmp_path / "n.db")
    db.connect()
    run = db.start_run("demo", "v1", "GOLD", {}, 1000.0, None, "fp")
    trade = Trade(
        run_id=run, mode="demo", symbol="GOLD", side="buy", volume=0.01,
        open_time="2026-08-25T15:00:00+00:00", open_price=4640.0,
        open_mid=4640.0, open_spread=0.8, broker_ticket="DIAAAAYCJETQ7A8",
    )
    db.insert_trade(trade)
    assert db.open_trades(run)[0].broker_ticket == "DIAAAAYCJETQ7A8"
