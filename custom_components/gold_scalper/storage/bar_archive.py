"""Een archief van bars, zodat je niet elke keer opnieuw begint.

De beperking van dit project is niet het aantal ideeën maar het aantal
metingen. Bij vijfentwintig trades per dag kost één hypothese drie weken; tien
hypotheses kosten twee jaar. Dat maakt een wetenschappelijke aanpak in de
praktijk onmogelijk.

Wat dat verandert is historie. Met een jaar aan M15-bars is een hypothese in
een minuut te toetsen in plaats van in drie weken - dezelfde toets, andere
doorlooptijd.

Tot nu toe verzamelde de integratie bars in het geheugen en gooide ze weg bij
elke herstart. De backtest draaide daardoor op wat er toevallig stond, meestal
een paar dagen.

**Wat dit archief wel doet.** Bars bewaren, ontdubbelen, gaten zichtbaar maken,
en groeien met wat er binnenkomt - uit welke bron dan ook.

**Wat het niet doet.** De handelslus raken. Er wordt alleen geschreven wat er
toch al opgehaald werd; er gaat geen enkel extra verzoek naar de broker vanwege
dit archief.

**Waarom gaten belangrijker zijn dan volume.** Een backtest over een reeks met
onopgemerkte gaten meet iets anders dan hij denkt: de bar na een gat van drie
uur ziet eruit als een enorme beweging. Daarom worden gaten geteld en gemeld in
plaats van stilzwijgend overbrugd.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..analysis.signals import Candles

_LOGGER = logging.getLogger(__name__)

# Uit de aggregator: één definitie van hoe lang een bar duurt. Twee kopieën van
# hetzelfde getal kunnen uiteenlopen, en dan berekent de gatendetectie met een
# andere barlengte dan de bars zelf hebben.
from ..strategy.aggregator import BAR_SECONDS

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS bars (
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    source      TEXT,
    -- Eén bar per symbool, tijdsframe en tijdstip. Zonder deze sleutel
    -- levert elke herstart duplicaten op, en een backtest telt die dan
    -- gewoon mee alsof de markt twee keer hetzelfde deed.
    PRIMARY KEY (symbol, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_bars_lookup
    ON bars(symbol, timeframe, timestamp);
"""


@dataclass(slots=True)
class ArchiveStats:
    symbol: str
    timeframe: str
    bars: int
    first: str | None
    last: str | None
    span_days: float
    gaps: int
    largest_gap_bars: int
    coverage: float

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars": self.bars,
            "eerste": self.first,
            "laatste": self.last,
            "spanne_dagen": round(self.span_days, 1),
            "gaten": self.gaps,
            "grootste_gat_bars": self.largest_gap_bars,
            "dekking": round(self.coverage, 3),
        }


class BarArchive:
    """Bewaart bars over herstarts heen."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Archief is niet geopend")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- schrijven ---------------------------------------------------------- #

    def store(
        self, symbol: str, timeframe: str, candles: Candles,
        source: str = "onbekend",
    ) -> int:
        """Voeg bars toe. Geeft terug hoeveel er nieuw waren.

        Bestaande bars worden overschreven, niet overgeslagen: een latere
        ophaling van dezelfde bar bij de broker is nauwkeuriger dan een bar die
        uit losse koersen is opgebouwd.
        """
        if not len(candles):
            return 0

        voor = self.count(symbol, timeframe)
        rijen = [
            (symbol, timeframe, int(candles.timestamp[i]), candles.open[i],
             candles.high[i], candles.low[i], candles.close[i],
             candles.volume[i], source)
            for i in range(len(candles))
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO bars "
            "(symbol, timeframe, timestamp, open, high, low, close, volume, "
            " source) VALUES (?,?,?,?,?,?,?,?,?)",
            rijen,
        )
        self.conn.commit()
        return self.count(symbol, timeframe) - voor

    # -- lezen -------------------------------------------------------------- #

    def count(self, symbol: str, timeframe: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM bars WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
        return int(row["n"])

    def load(
        self, symbol: str, timeframe: str,
        start: int | None = None, end: int | None = None,
        limit: int | None = None,
    ) -> Candles:
        """Haal bars op, op volgorde van tijd."""
        vraag = "SELECT * FROM bars WHERE symbol=? AND timeframe=?"
        args: list = [symbol, timeframe]
        if start is not None:
            vraag += " AND timestamp >= ?"
            args.append(int(start))
        if end is not None:
            vraag += " AND timestamp <= ?"
            args.append(int(end))
        vraag += " ORDER BY timestamp"
        if limit:
            # Bij een limiet de LAATSTE bars, want die zijn relevant.
            vraag = (
                f"SELECT * FROM ({vraag.replace('ORDER BY timestamp', '')} "
                f"ORDER BY timestamp DESC LIMIT {int(limit)}) ORDER BY timestamp"
            )

        rijen = self.conn.execute(vraag, args).fetchall()
        if not rijen:
            raise ValueError(
                f"Geen bars voor {symbol} op {timeframe} in het archief."
            )
        return Candles(
            timestamp=[int(r["timestamp"]) for r in rijen],
            open=[r["open"] for r in rijen],
            high=[r["high"] for r in rijen],
            low=[r["low"] for r in rijen],
            close=[r["close"] for r in rijen],
            volume=[r["volume"] for r in rijen],
        )

    # -- beoordelen --------------------------------------------------------- #

    def stats(self, symbol: str, timeframe: str) -> ArchiveStats:
        """Hoeveel is er, en hoe volledig?

        De dekking is het aandeel bars dat er zou moeten zijn als de markt
        onafgebroken open was. Bij goud ligt die van nature rond de 0,75:
        weekenden en de dagelijkse onderbreking horen erbij. Een veel lagere
        waarde wijst op ontbrekende data.
        """
        rijen = self.conn.execute(
            "SELECT timestamp FROM bars WHERE symbol=? AND timeframe=? "
            "ORDER BY timestamp",
            (symbol, timeframe),
        ).fetchall()

        if not rijen:
            return ArchiveStats(symbol, timeframe, 0, None, None, 0.0, 0, 0, 0.0)

        stamps = [int(r["timestamp"]) for r in rijen]
        stap = BAR_SECONDS.get(timeframe, 900)

        gaten = 0
        grootste = 0
        for eerder, later in zip(stamps, stamps[1:]):
            ontbreekt = (later - eerder) // stap - 1
            if ontbreekt > 0:
                gaten += 1
                grootste = max(grootste, int(ontbreekt))

        spanne = stamps[-1] - stamps[0]
        verwacht = spanne // stap + 1 if spanne else 1

        return ArchiveStats(
            symbol=symbol,
            timeframe=timeframe,
            bars=len(stamps),
            first=datetime.fromtimestamp(stamps[0], timezone.utc).isoformat(),
            last=datetime.fromtimestamp(stamps[-1], timezone.utc).isoformat(),
            span_days=spanne / 86400,
            gaps=gaten,
            largest_gap_bars=grootste,
            coverage=len(stamps) / verwacht if verwacht else 0.0,
        )

    def available(self) -> list[dict]:
        """Wat zit er in het archief?"""
        rijen = self.conn.execute(
            "SELECT symbol, timeframe, COUNT(*) AS n FROM bars "
            "GROUP BY symbol, timeframe ORDER BY n DESC"
        ).fetchall()
        return [
            {"symbol": r["symbol"], "timeframe": r["timeframe"],
             "bars": int(r["n"])}
            for r in rijen
        ]

    def prune(self, keep_days: int = 1095) -> int:
        """Ruim bars op die ouder zijn dan drie jaar.

        Ruim genomen: een backtest over meerdere marktregimes is juist wat je
        wilt, en een bar kost ongeveer honderd bytes.
        """
        grens = int(datetime.now(timezone.utc).timestamp()) - keep_days * 86400
        cur = self.conn.execute("DELETE FROM bars WHERE timestamp < ?", (grens,))
        self.conn.commit()
        return cur.rowcount
