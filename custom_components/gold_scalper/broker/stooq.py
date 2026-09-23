"""Stooq als tweede publieke databron.

Yahoo stuurt Europese bezoekers regelmatig naar een cookie-toestemmingspagina,
en weigert soms met 401 of 429. Stooq is een Poolse financiële site die platte
CSV serveert zonder sleutel, zonder cookies en zonder toestemmingsscherm - juist
daarom bruikbaar als de eerste bron dichtzit.

Beperkingen die je moet kennen voordat je hem kiest.

**Alleen dagkoersen in de historie.** Stooq's gratis historie-endpoint levert
daggegevens. Er is geen minuut- of uurhistorie. Dat maakt hem ongeschikt voor
scalping en bruikbaar voor tijdsframes vanaf een dag.

**Eén actuele koers zonder bied- en laatprijs.** Net als bij Yahoo is de spread
een aanname.

Dit is dus geen vervanging van Yahoo maar een uitwijkmogelijkheid, en een
prima bron als je toch al concludeert dat M1-scalpen bij jouw spread niet
haalbaar is en je naar een hoger tijdsframe wilt.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

from aiohttp import ClientError, ClientSession, ClientTimeout

from ..analysis.signals import Candles
from .adapter import (
    AccountSnapshot,
    ExecutionVenue,
    OrderResult,
    VenueError,
    VenuePosition,
    VenueQuote,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = ClientTimeout(total=20)

HISTORY_URL = "https://stooq.com/q/d/l/"
QUOTE_URL = "https://stooq.com/q/l/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant gold_scalper)",
    "Accept": "text/csv,text/plain,*/*",
}

#: Stooq-symbolen. Kleine letters, geen achtervoegsel.
SYMBOLS = {
    "xauusd": "Goud spot in USD",
    "xaueur": "Goud spot in EUR",
}

#: Alleen dagelijks; Stooq biedt geen gratis intraday-historie.
INTERVALS = {"1d": "d", "1w": "w", "1mo": "m"}


class StooqVenue(ExecutionVenue):
    """Daggegevens voor goud, zonder account en zonder toestemmingspagina."""

    name = "stooq"
    runs_in_process = True
    supports_trading = False
    is_simulated = False
    has_real_spread = False

    def __init__(
        self,
        session: ClientSession,
        symbol: str = "xauusd",
        assumed_spread: float = 0.0,
    ) -> None:
        self._session = session
        self.symbol = symbol.lower()
        self.assumed_spread = float(assumed_spread)

    @property
    def costs_disabled(self) -> bool:
        return self.assumed_spread <= 0.0

    async def _get_csv(self, url: str, params: dict) -> list[dict]:
        try:
            async with self._session.get(
                url, params=params, headers=HEADERS, timeout=TIMEOUT
            ) as response:
                if response.status >= 400:
                    raise VenueError(f"Stooq antwoordde met HTTP {response.status}")
                text = await response.text()
        except VenueError:
            raise
        except TimeoutError as err:
            raise VenueError(f"Stooq antwoordde niet binnen {TIMEOUT.total}s") from err
        except ClientError as err:
            raise VenueError(
                f"Netwerkfout richting Stooq: {type(err).__name__}: {err}"
            ) from err

        stripped = text.strip()
        # Stooq geeft bij een onbekend symbool gewoon deze tekst terug, met
        # HTTP 200. Zonder deze controle zou de CSV-parser een lege reeks
        # opleveren en zou de fout verderop opduiken als "geen candles".
        if not stripped or stripped.lower().startswith("no data"):
            raise VenueError(
                f"Stooq kent symbool '{self.symbol}' niet, of heeft er geen data "
                f"voor. Kies uit: {', '.join(SYMBOLS)}"
            )
        return list(csv.DictReader(io.StringIO(stripped)))

    async def quote(self, symbol: str | None = None) -> VenueQuote:
        rows = await self._get_csv(
            QUOTE_URL, {"s": symbol or self.symbol, "f": "sd2t2ohlc", "h": "", "e": "csv"}
        )
        if not rows:
            raise VenueError("Stooq gaf geen actuele koers")
        row = rows[0]
        try:
            price = float(row["Close"])
        except (KeyError, ValueError) as err:
            raise VenueError(f"Onverwacht Stooq-formaat: {row}") from err

        moment = datetime.now(timezone.utc)
        stamp = f"{row.get('Date','')} {row.get('Time','')}".strip()
        if stamp:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    moment = datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

        half = self.assumed_spread / 2.0
        # Stooq meldt geen marktstatus. Een dagkoers is per definitie oud, dus
        # 'verhandelbaar' hier hard op True zetten zou de staleness-controle
        # zinloos maken; op weekdagen aannemen dat de markt open is, is het
        # beste dat deze bron toelaat.
        weekday_open = moment.weekday() < 5
        return VenueQuote(
            bid=round(price - half, 3),
            ask=round(price + half, 3),
            time=moment,
            tradeable=weekday_open,
        )

    async def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        if timeframe not in INTERVALS:
            raise VenueError(
                f"Stooq biedt geen intraday-historie. Beschikbaar: "
                f"{', '.join(INTERVALS)}"
            )
        rows = await self._get_csv(
            HISTORY_URL, {"s": symbol or self.symbol, "i": INTERVALS[timeframe]}
        )

        parsed = []
        for row in rows:
            try:
                moment = datetime.strptime(row["Date"], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                o, h, l, c = (
                    float(row["Open"]), float(row["High"]),
                    float(row["Low"]), float(row["Close"]),
                )
            except (KeyError, ValueError):
                continue
            volume = 0.0
            if row.get("Volume"):
                try:
                    volume = float(row["Volume"])
                except ValueError:
                    volume = 0.0
            parsed.append([int(moment.timestamp()), o, h, l, c, volume])

        if not parsed:
            raise VenueError(f"Geen bruikbare rijen in de Stooq-data voor {symbol}")
        parsed.sort(key=lambda r: r[0])
        parsed = parsed[-count:]

        return Candles(
            timestamp=[r[0] for r in parsed],
            open=[r[1] for r in parsed],
            high=[r[2] for r in parsed],
            low=[r[3] for r in parsed],
            close=[r[4] for r in parsed],
            volume=[r[5] for r in parsed],
        )

    async def account(self) -> AccountSnapshot:
        raise VenueError(
            "Deze databron kent geen account; het saldo wordt door de "
            "paper-broker bijgehouden."
        )

    async def positions(self, symbol: str | None = None) -> list[VenuePosition]:
        return []

    async def place_order(self, symbol, side, units, stop_loss=None,
                          take_profit=None, comment="") -> OrderResult:
        raise VenueError("Stooq levert alleen data; draai in papermodus.")

    async def close(self, ticket: str, units: float | None = None) -> OrderResult:
        raise VenueError("Stooq heeft geen posities.")

    async def modify_stop(self, ticket: str, stop_loss: float) -> OrderResult:
        raise VenueError("Stooq heeft geen posities.")

    def describe(self) -> dict:
        base = super().describe()
        base.update({
            "simulated": False,
            "real_prices": True,
            "real_spread": False,
            "symbol": self.symbol,
            "assumed_spread": self.assumed_spread,
            "costs_disabled": self.costs_disabled,
            "source": "Stooq (CSV, alleen daggegevens)",
            "intraday": False,
        })
        return base
