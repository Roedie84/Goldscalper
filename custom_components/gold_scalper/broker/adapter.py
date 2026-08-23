"""Abstracte uitvoeringslaag.

De reden dat deze abstractie er nu komt en niet eerder: zolang er één broker
was, was hij overbodig. Nu blijkt dat de keuze van broker bepaalt of het hele
systeem binnen Home Assistant kan draaien, en dan hoort die keuze een
inwisselbaar onderdeel te zijn in plaats van een aanname die overal doorheen
loopt.

Twee soorten venue:

``RestVenue``
    Praat rechtstreeks met een broker-API over HTTP. Draait overal waar Python
    draait, dus ook in Home Assistant op Linux of een Pi. Geen extra
    afhankelijkheden: ``aiohttp`` zit al in HA.

``BridgeVenue``
    Praat met de MT5-bridge op een Windows-machine. Nodig voor brokers die
    alleen MetaTrader aanbieden, zoals AvaTrade.

Alles boven deze laag - strategie, papersimulatie, database, risicobewaking,
exits, rapportage - kent alleen deze interface en verandert niet mee als je
van broker wisselt.

Let op de eenheden. MT5 rekent in lots (1 lot XAUUSD = 100 ounce); OANDA rekent
in units (1 unit = 1 ounce). Die vertaling gebeurt in de adapter, zodat de
strategie altijd in ounces denkt en er nooit per ongeluk een factor 100 fout
gaat. Dat is precies het soort fout dat je pas ontdekt als het geld al weg is.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ..analysis.signals import Candles

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class VenueQuote:
    bid: float
    ask: float
    time: datetime
    tradeable: bool = True

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(slots=True)
class VenuePosition:
    """Positie zoals de broker hem kent. Dit is de waarheid, niet onze database."""

    ticket: str
    symbol: str
    side: str
    units: float          # altijd in ounces
    open_price: float
    current_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    unrealised_pnl: float | None = None
    open_time: datetime | None = None


@dataclass(slots=True)
class OrderResult:
    success: bool
    ticket: str | None = None
    fill_price: float | None = None
    requested_price: float | None = None
    units: float | None = None
    latency_ms: float | None = None
    error: str | None = None

    @property
    def slippage(self) -> float | None:
        if self.fill_price is None or self.requested_price is None:
            return None
        return abs(self.fill_price - self.requested_price)


@dataclass(slots=True)
class AccountSnapshot:
    balance: float
    equity: float
    margin_used: float
    margin_available: float
    currency: str
    open_position_count: int


class VenueError(Exception):
    """Uitvoeringslaag faalde."""


class TradingDisabledError(VenueError):
    """Order geweigerd omdat handel niet is ingeschakeld."""


class ExecutionVenue(ABC):
    """Wat elke uitvoeringslaag moet kunnen."""

    name: str = "abstract"
    #: Draait deze venue binnen het Home Assistant-proces zelf?
    runs_in_process: bool = False
    #: Kan er daadwerkelijk gehandeld worden, of is dit read-only?
    supports_trading: bool = False

    @abstractmethod
    async def quote(self, symbol: str) -> VenueQuote:
        """Huidige bid/ask."""

    @abstractmethod
    async def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        """Historische candles, oplopend in tijd."""

    @abstractmethod
    async def account(self) -> AccountSnapshot:
        """Balans, equity en marge."""

    @abstractmethod
    async def positions(self, symbol: str | None = None) -> list[VenuePosition]:
        """Open posities volgens de broker."""

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        units: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
    ) -> OrderResult:
        """Plaats een marktorder. ``units`` in ounces."""

    @abstractmethod
    async def close(self, ticket: str, units: float | None = None) -> OrderResult:
        """Sluit een positie geheel of gedeeltelijk."""

    @abstractmethod
    async def modify_stop(self, ticket: str, stop_loss: float) -> OrderResult:
        """Verplaats de stop-loss. Nodig voor break-even en trailing."""

    async def close_all(self, symbol: str | None = None) -> list[OrderResult]:
        """Noodknop. Standaardimplementatie volstaat voor de meeste venues."""
        results = []
        for position in await self.positions(symbol):
            try:
                results.append(await self.close(position.ticket))
            except VenueError as err:
                results.append(OrderResult(success=False, ticket=position.ticket,
                                           error=str(err)))
        return results

    async def health(self) -> dict:
        """Is de verbinding bruikbaar. Standaard: probeer een account op te halen."""
        try:
            snapshot = await self.account()
            return {"ok": True, "balance": snapshot.balance, "venue": self.name}
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "error": str(err), "venue": self.name}

    def describe(self) -> dict:
        return {
            "venue": self.name,
            "runs_in_home_assistant": self.runs_in_process,
            "supports_trading": self.supports_trading,
            "requires_external_process": not self.runs_in_process,
        }
