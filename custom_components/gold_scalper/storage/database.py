"""SQLite-tradedatabase.

Deze database is de kern van de bewijsfase. Het ontwerpuitgangspunt is dat de
ledger *tegen* de strategie moet kunnen getuigen: elke kostenpost wordt apart
opgeslagen, zodat achteraf te zien is of een winstgevend ogende strategie
alleen winstgevend is omdat de kosten zijn weggemoffeld.

Daarom zijn ``gross_pnl`` en ``net_pnl`` gescheiden kolommen, en worden spread,
commissie, slippage en swap allemaal individueel bewaard. Bij scalping op goud
is de kostenpost bijna altijd groter dan de bruto marge, en dat moet zichtbaar
zijn in plaats van verstopt in één samengevat getal.

Er wordt sqlite3 in een executor gebruikt in plaats van aiosqlite: geen extra
dependency, en de schrijfvolumes zijn klein genoeg dat het niet uitmaakt.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MODE_PAPER = "paper"
MODE_LIVE = "live"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Eén rij per handelssessie/strategieversie, zodat resultaten van
-- verschillende strategieversies nooit op één hoop belanden.
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    mode              TEXT NOT NULL,
    strategy_version  TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    config_json       TEXT NOT NULL,
    starting_balance  REAL NOT NULL,
    note              TEXT,
    -- Hash van alles wat het handelsgedrag bepaalt. Gelijke vingerafdruk
    -- betekent: dezelfde opzet, dus dezelfde run voortzetten na een herstart.
    fingerprint       TEXT
);
-- De index op fingerprint staat bewust in _migrate() en niet hier: op een
-- bestaande database van vóór deze kolom zou CREATE INDEX falen omdat de
-- kolom pas door de migratie wordt toegevoegd.

CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES runs(id),
    mode              TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('buy','sell')),
    volume            REAL NOT NULL,

    open_time         TEXT NOT NULL,
    open_price        REAL NOT NULL,
    open_mid          REAL NOT NULL,
    open_spread       REAL NOT NULL,
    open_slippage     REAL NOT NULL DEFAULT 0,

    close_time        TEXT,
    close_price       REAL,
    close_mid         REAL,
    close_spread      REAL,
    close_slippage    REAL DEFAULT 0,
    close_reason      TEXT,

    stop_loss         REAL,
    take_profit       REAL,

    commission        REAL NOT NULL DEFAULT 0,
    swap              REAL NOT NULL DEFAULT 0,
    spread_cost       REAL NOT NULL DEFAULT 0,
    slippage_cost     REAL NOT NULL DEFAULT 0,
    total_cost        REAL NOT NULL DEFAULT 0,

    gross_pnl         REAL,
    net_pnl           REAL,
    return_pct        REAL,
    mae               REAL,           -- maximum adverse excursion
    mfe               REAL,           -- maximum favourable excursion
    duration_seconds  INTEGER,

    signal_score      REAL,
    signal_confidence REAL,
    regime            TEXT,
    open_reason       TEXT,
    -- Ticketnummers zijn niet numeriek. IG gebruikt sleutels als
    -- 'DIAAAAYCJETQ7A8'; alleen MetaTrader en OANDA werken met gehele
    -- getallen. De kolom heet nog mt5_ticket uit de eerste versie en is
    -- hernoemd naar broker_ticket.
    broker_ticket     TEXT,

    UNIQUE(broker_ticket, mode)
);

CREATE INDEX IF NOT EXISTS idx_trades_run   ON trades(run_id);
CREATE INDEX IF NOT EXISTS idx_trades_open  ON trades(open_time);
CREATE INDEX IF NOT EXISTS idx_trades_close ON trades(close_time);
CREATE INDEX IF NOT EXISTS idx_trades_mode  ON trades(mode);

-- Elke strategie-evaluatie, ook als er níet is gehandeld. Zonder deze tabel
-- kun je achteraf niet vaststellen of de filters te streng of te los stonden.
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    ts            TEXT NOT NULL,
    score         REAL NOT NULL,
    confidence    REAL NOT NULL,
    state         TEXT NOT NULL,
    regime        TEXT,
    spread        REAL,
    acted         INTEGER NOT NULL DEFAULT 0,
    reject_reason TEXT,
    detail_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_run ON signals(run_id, ts);

CREATE TABLE IF NOT EXISTS equity (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES runs(id),
    ts             TEXT NOT NULL,
    balance        REAL NOT NULL,
    equity         REAL NOT NULL,
    open_positions INTEGER NOT NULL DEFAULT 0,
    cumulative_cost REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_equity_run ON equity(run_id, ts);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Trade:
    """Eén positie, van opening tot sluiting."""

    run_id: int
    mode: str
    symbol: str
    side: str
    volume: float
    open_time: str
    open_price: float
    open_mid: float
    open_spread: float
    open_slippage: float = 0.0

    close_time: str | None = None
    close_price: float | None = None
    close_mid: float | None = None
    close_spread: float | None = None
    close_slippage: float = 0.0
    close_reason: str | None = None

    stop_loss: float | None = None
    take_profit: float | None = None

    commission: float = 0.0
    swap: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    total_cost: float = 0.0

    gross_pnl: float | None = None
    net_pnl: float | None = None
    return_pct: float | None = None
    mae: float | None = None
    mfe: float | None = None
    duration_seconds: int | None = None

    signal_score: float | None = None
    signal_confidence: float | None = None
    regime: str | None = None
    open_reason: str | None = None
    #: Ticketnummer bij de broker. Tekst, niet numeriek: IG gebruikt sleutels
    #: als 'DIAAAAYCJETQ7A8'.
    broker_ticket: str | None = None
    id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.close_time is None


class TradeDatabase:
    """Synchrone SQLite-laag. Aanroepen vanuit HA gaan via een executor job."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------- #

    def connect(self) -> None:
        self._conn = sqlite3.connect(
            self.path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()
        _LOGGER.debug("Tradedatabase geopend op %s", self.path)

    def _migrate(self) -> None:
        """Voeg kolommen toe die in latere versies zijn bijgekomen.

        Zonder dit zou een bestaande database na een update stukgaan op een
        ontbrekende kolom, en dat is precies de data die je niet kwijt wilt.
        """
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        trade_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(trades)").fetchall()
        }
        # Hernoemen van mt5_ticket naar broker_ticket, met behoud van de
        # bestaande waarden. SQLite kan een kolomtype niet wijzigen, maar
        # accepteert wel tekst in een INTEGER-kolom, dus kopiëren volstaat.
        if "mt5_ticket" in trade_columns and "broker_ticket" not in trade_columns:
            self._conn.execute("ALTER TABLE trades ADD COLUMN broker_ticket TEXT")
            self._conn.execute(
                "UPDATE trades SET broker_ticket = CAST(mt5_ticket AS TEXT) "
                "WHERE mt5_ticket IS NOT NULL"
            )
            _LOGGER.info("Database bijgewerkt: mt5_ticket -> broker_ticket")
        elif "broker_ticket" not in trade_columns:
            self._conn.execute("ALTER TABLE trades ADD COLUMN broker_ticket TEXT")

        if "fingerprint" not in existing:
            self._conn.execute("ALTER TABLE runs ADD COLUMN fingerprint TEXT")
            _LOGGER.info("Database bijgewerkt: kolom 'fingerprint' toegevoegd")
        # Pas ná de kolomtoevoeging; op een oude database zou dit anders falen.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_fingerprint ON runs(fingerprint)"
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database niet verbonden; roep connect() aan")
        return self._conn

    # -- runs --------------------------------------------------------------- #

    def find_matching_run(self, fingerprint: str) -> dict | None:
        """Zoek de meest recente open run met dezelfde opzet.

        Bestaat omdat elke herstart anders een nieuwe run begon en de teller
        op nul zette. Een bewijsfase van dertig dagen is dan onhaalbaar: één
        Home Assistant-update wist hem.
        """
        row = self.conn.execute(
            "SELECT * FROM runs WHERE fingerprint = ? AND ended_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return dict(row) if row else None

    def update_run_fingerprint(
        self, run_id: int, fingerprint: str, material: dict
    ) -> None:
        """Werk de vingerafdruk bij zonder de run te onderbreken.

        Nodig wanneer een run wordt voortgezet ondanks gewijzigde
        standaardwaarden: zonder bijwerken zou de volgende herstart dezelfde
        vergelijking opnieuw moeten maken.
        """
        row = self.conn.execute(
            "SELECT config_json FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        config = {}
        if row and row["config_json"]:
            try:
                config = json.loads(row["config_json"])
            except (TypeError, ValueError):
                config = {}
        config["fingerprint_material"] = material
        self.conn.execute(
            "UPDATE runs SET fingerprint=?, config_json=? WHERE id=?",
            (fingerprint, json.dumps(config, sort_keys=True, default=str), run_id),
        )
        self.conn.commit()

    def run_totals(self) -> list[dict]:
        """Samenvatting per run, zodat eerdere runs niet uit beeld verdwijnen."""
        rows = self.conn.execute(
            """SELECT r.id, r.started_at, r.ended_at, r.mode, r.strategy_version,
                      r.symbol, r.config_json, r.starting_balance,
                      COUNT(t.id) AS trades,
                      COALESCE(SUM(t.net_pnl), 0) AS net_pnl,
                      COALESCE(SUM(t.total_cost), 0) AS costs
               FROM runs r
               LEFT JOIN trades t ON t.run_id = r.id AND t.close_time IS NOT NULL
               GROUP BY r.id ORDER BY r.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def start_run(
        self,
        mode: str,
        strategy_version: str,
        symbol: str,
        config: dict,
        starting_balance: float,
        note: str | None = None,
        fingerprint: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO runs
               (started_at, mode, strategy_version, symbol, config_json,
                starting_balance, note, fingerprint)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                _now(),
                mode,
                strategy_version,
                symbol,
                json.dumps(config, sort_keys=True, default=str),
                starting_balance,
                note,
                fingerprint,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def end_run(self, run_id: int) -> None:
        self.conn.execute("UPDATE runs SET ended_at=? WHERE id=?", (_now(), run_id))
        self.conn.commit()

    def get_run(self, run_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- trades ------------------------------------------------------------- #

    def insert_trade(self, trade: Trade) -> int:
        data = asdict(trade)
        data.pop("id", None)
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cur = self.conn.execute(
            f"INSERT INTO trades ({columns}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        self.conn.commit()
        trade.id = int(cur.lastrowid)
        return trade.id

    def update_trade(self, trade: Trade) -> None:
        if trade.id is None:
            raise ValueError("Trade heeft geen id; eerst insert_trade aanroepen")
        data = asdict(trade)
        data.pop("id")
        assignments = ", ".join(f"{k}=?" for k in data)
        self.conn.execute(
            f"UPDATE trades SET {assignments} WHERE id=?",
            (*data.values(), trade.id),
        )
        self.conn.commit()

    def open_trades(self, run_id: int) -> list[Trade]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE run_id=? AND close_time IS NULL", (run_id,)
        ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def closed_trades(self, run_id: int | None = None, limit: int | None = None) -> list[Trade]:
        query = "SELECT * FROM trades WHERE close_time IS NOT NULL"
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id=?"
            params.append(run_id)
        query += " ORDER BY close_time ASC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return [self._row_to_trade(r) for r in self.conn.execute(query, params).fetchall()]

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> Trade:
        fields = {f for f in Trade.__dataclass_fields__}
        return Trade(**{k: row[k] for k in row.keys() if k in fields})

    # -- signals ------------------------------------------------------------ #

    def log_signal(
        self,
        run_id: int,
        score: float,
        confidence: float,
        state: str,
        regime: str | None,
        spread: float | None,
        acted: bool,
        reject_reason: str | None = None,
        detail: dict | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO signals
               (run_id, ts, score, confidence, state, regime, spread, acted,
                reject_reason, detail_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                _now(),
                score,
                confidence,
                state,
                regime,
                spread,
                int(acted),
                reject_reason,
                json.dumps(detail, default=str) if detail else None,
            ),
        )
        self.conn.commit()

    def signal_stats(self, run_id: int) -> dict:
        """Hoeveel signalen zijn er geweest, en waarom is er niet gehandeld."""
        total = self.conn.execute(
            "SELECT COUNT(*) c, SUM(acted) a FROM signals WHERE run_id=?", (run_id,)
        ).fetchone()
        rejects = self.conn.execute(
            """SELECT reject_reason, COUNT(*) c FROM signals
               WHERE run_id=? AND acted=0 AND reject_reason IS NOT NULL
               GROUP BY reject_reason ORDER BY c DESC""",
            (run_id,),
        ).fetchall()
        return {
            "evaluations": total["c"] or 0,
            "acted": total["a"] or 0,
            "rejections": {r["reject_reason"]: r["c"] for r in rejects},
        }

    # -- equity ------------------------------------------------------------- #

    def record_equity(
        self,
        run_id: int,
        balance: float,
        equity: float,
        open_positions: int,
        cumulative_cost: float,
    ) -> None:
        self.conn.execute(
            """INSERT INTO equity
               (run_id, ts, balance, equity, open_positions, cumulative_cost)
               VALUES (?,?,?,?,?,?)""",
            (run_id, _now(), balance, equity, open_positions, cumulative_cost),
        )
        self.conn.commit()

    def equity_curve(self, run_id: int, limit: int = 5000) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ts, balance, equity, cumulative_cost FROM equity "
            "WHERE run_id=? ORDER BY ts ASC LIMIT ?",
            (run_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- housekeeping ------------------------------------------------------- #

    def prune(self, keep_days: int = 365) -> int:
        """Verwijder oude signaalregels. Trades blijven altijd staan — die zijn
        het bewijsmateriaal en mogen nooit stilzwijgend verdwijnen."""
        cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        cur = self.conn.execute("DELETE FROM signals WHERE ts < ?", (cutoff_iso,))
        self.conn.commit()
        return cur.rowcount
