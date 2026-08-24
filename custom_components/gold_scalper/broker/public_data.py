"""Publieke marktdata: echte goudkoersen zonder account.

Haalt OHLC-data op bij Yahoo Finance. Geen sleutel, geen registratie, geen
broker. Bedoeld als tussenstap: je draait de strategie op wat goud werkelijk
doet, terwijl de uitvoering nog volledig op papier gebeurt.

Drie dingen die je moet weten voordat je conclusies trekt.

**Er is geen bied- en laatprijs.** Publieke bronnen leveren transactieprijzen,
niet de quote van een broker. De spread - je grootste kostenpost bij scalping -
moet dus *aangenomen* worden. Dat getal is een invoer van jou, geen meting. Bij
een aangenomen spread van nul is elk resultaat navenant fictief, en de
``LiveGate`` weigert zo'n run dan ook categorisch.

**De data is niet die van jouw broker.** Yahoo levert de futuresprijs of de
spotkoers uit interbancaire bronnen. Jouw broker quoteert daar omheen met een
eigen opslag. Verschillen van enkele tienden van een dollar zijn normaal, en
dat is precies de orde van grootte waar een scalpingstrategie op leeft.

**Het endpoint is ongedocumenteerd.** Yahoo publiceert deze API niet officieel.
Hij werkt al jaren, maar kan zonder aankondiging veranderen. Voor een
verkennende fase is dat aanvaardbaar; voor iets waar geld aan hangt niet.

Minuutdata reikt bij Yahoo ongeveer een week terug. Voor langere historie moet
je naar een hoger tijdsframe.
"""

from __future__ import annotations

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

#: Yahoo verdeelt de belasting over twee hosts. Faalt de een, dan is de ander
#: vaak wel bereikbaar; ze worden daarom achter elkaar geprobeerd.
HOSTS = (
    "https://query1.finance.yahoo.com/v8/finance/chart",
    "https://query2.finance.yahoo.com/v8/finance/chart",
)

#: Yahoo weigert verzoeken zonder herkenbare user-agent met HTTP 429.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

#: Yahoo begrenst intraday-historie tot circa 60 dagen, en minuutdata tot een
#: dag of zeven. Het bereik per interval is daarop gekozen.
INTERVALS = {
    "1m": ("1m", "5d"),
    "5m": ("5m", "1mo"),
    "15m": ("15m", "1mo"),
    "30m": ("30m", "1mo"),
    "1h": ("1h", "2y"),
    "1d": ("1d", "5y"),
}

#: Beschikbare goudsymbolen bij Yahoo.
SYMBOLS = {
    # COMEX-futures, doorlopend contract. Meestal de beste intraday-granulariteit.
    "GC=F": "Goud futures (COMEX)",
    # Spot XAU/USD uit interbancaire bronnen. Dichter bij wat een CFD-broker
    # quoteert, maar bij Yahoo soms schaarser op minuutniveau.
    "XAUUSD=X": "Goud spot (XAU/USD)",
}


class PublicDataVenue(ExecutionVenue):
    """Echte goudkoersen, papierhandel, geen account."""

    name = "public_data"
    runs_in_process = True
    supports_trading = False
    is_simulated = False
    #: Wel echte prijzen, maar geen echte quote: de spread is een aanname.
    has_real_spread = False

    def __init__(
        self,
        session: ClientSession,
        symbol: str = "GC=F",
        assumed_spread: float = 0.0,
    ) -> None:
        self._session = session
        self.symbol = symbol
        #: Aangenomen spread in USD per ounce. Nul betekent: kosten uitgeschakeld.
        self.assumed_spread = float(assumed_spread)
        self._last_meta: dict = {}

    @property
    def costs_disabled(self) -> bool:
        return self.assumed_spread <= 0.0

    async def _fetch_one(self, base: str, symbol: str, interval: str, rng: str) -> dict:
        """Eén poging bij één host. Faalt met een specifieke reden."""
        url = f"{base}/{symbol}"
        try:
            async with self._session.get(
                url,
                params={"interval": interval, "range": rng, "includePrePost": "false"},
                headers=HEADERS,
                timeout=TIMEOUT,
            ) as response:
                if response.status == 429:
                    raise VenueError(
                        "Yahoo knijpt af (HTTP 429). Verhoog het verversingsinterval "
                        "of wacht een kwartier."
                    )
                if response.status in (401, 403):
                    raise VenueError(
                        f"Yahoo weigert de aanvraag (HTTP {response.status}). Dit "
                        "gebeurt als Yahoo een cookie of crumb eist voor jouw regio."
                    )
                if response.status == 404:
                    raise VenueError(
                        f"Symbool '{symbol}' onbekend bij Yahoo. Kies uit: "
                        f"{', '.join(SYMBOLS)}"
                    )
                if response.status >= 400:
                    raise VenueError(f"Yahoo antwoordde met HTTP {response.status}")

                # Yahoo stuurt Europese bezoekers regelmatig door naar een
                # cookie-toestemmingspagina. Dan komt er HTML terug in plaats van
                # JSON, en zonder deze controle zie je alleen een vage
                # netwerkfout in plaats van de werkelijke oorzaak.
                content_type = response.headers.get("Content-Type", "")
                if "json" not in content_type.lower():
                    host = str(response.url.host or "")
                    if "consent" in host or "guce" in host:
                        raise VenueError(
                            "Yahoo leidt door naar een cookie-toestemmingspagina "
                            f"({host}). Deze bron is vanaf jouw netwerk niet "
                            "bruikbaar zonder toestemming te geven."
                        )
                    raise VenueError(
                        f"Yahoo gaf {content_type or 'onbekend formaat'} terug in "
                        "plaats van JSON; waarschijnlijk een tussenpagina."
                    )

                payload = await response.json(content_type=None)
        except VenueError:
            raise
        except TimeoutError as err:
            raise VenueError(f"Yahoo antwoordde niet binnen {TIMEOUT.total}s") from err
        except ClientError as err:
            raise VenueError(
                f"Netwerkfout richting Yahoo: {type(err).__name__}: {err}"
            ) from err

        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise VenueError(
                f"Yahoo: {chart['error'].get('description', 'onbekende fout')}"
            )
        results = chart.get("result") or []
        if not results:
            raise VenueError(f"Yahoo gaf geen data voor {symbol}")
        return results[0]

    async def _fetch(self, symbol: str, interval: str, rng: str) -> dict:
        """Probeer beide hosts; meld de laatste fout als beide falen."""
        errors: list[str] = []
        for base in HOSTS:
            try:
                return await self._fetch_one(base, symbol, interval, rng)
            except VenueError as err:
                errors.append(f"{base.split('/')[2]}: {err}")
                _LOGGER.debug("Yahoo-host faalde: %s", errors[-1])
        raise VenueError(" | ".join(errors))

    # -- marktdata ---------------------------------------------------------- #

    async def quote(self, symbol: str | None = None) -> VenueQuote:
        """Laatste koers, met een *aangenomen* spread eromheen.

        Belangrijk: bid en ask worden hier geconstrueerd, niet gemeten. Bij een
        aangenomen spread van nul vallen ze samen met de midprijs en kost een
        round trip niets - wat in de echte markt nooit het geval is.
        """
        result = await self._fetch(symbol or self.symbol, "1m", "1d")
        meta = result.get("meta") or {}
        self._last_meta = meta

        price = meta.get("regularMarketPrice")
        if price is None:
            raise VenueError("Yahoo leverde geen actuele koers")
        price = float(price)

        timestamp = meta.get("regularMarketTime")
        moment = (
            datetime.fromtimestamp(int(timestamp), timezone.utc)
            if timestamp else datetime.now(timezone.utc)
        )
        half = self.assumed_spread / 2.0
        return VenueQuote(
            bid=round(price - half, 3),
            ask=round(price + half, 3),
            time=moment,
            # marketState is de enige aanwijzing die Yahoo geeft. Buiten de
            # handelsuren is regularMarketTime uren oud; zonder deze vlag zou
            # de risicobewaking dat als een dode verbinding zien en een
            # noodstop slaan die handmatig hervat moet worden.
            tradeable=str(meta.get("marketState", "")).upper()
            not in ("CLOSED", "POSTPOST", "PREPRE"),
        )

    async def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        if timeframe not in INTERVALS:
            raise VenueError(
                f"Tijdsframe '{timeframe}' niet beschikbaar; kies uit {list(INTERVALS)}"
            )
        interval, rng = INTERVALS[timeframe]
        result = await self._fetch(symbol or self.symbol, interval, rng)

        timestamps = result.get("timestamp") or []
        quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote_data.get("open") or []
        highs = quote_data.get("high") or []
        lows = quote_data.get("low") or []
        closes = quote_data.get("close") or []
        volumes = quote_data.get("volume") or []

        rows = []
        for i, ts in enumerate(timestamps):
            # Yahoo levert null voor candles zonder handel; die overslaan in
            # plaats van interpoleren, want een verzonnen candle vervuilt elke
            # indicator die erop volgt.
            try:
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            except IndexError:
                continue
            if None in (o, h, l, c):
                continue
            volume = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            rows.append([int(ts), float(o), float(h), float(l), float(c), float(volume)])

        if not rows:
            raise VenueError(
                f"Geen bruikbare candles voor {symbol} op {timeframe}. "
                "Buiten handelsuren levert Yahoo soms een lege reeks."
            )

        rows.sort(key=lambda r: r[0])
        # De laatste candle is doorgaans nog in wording; die laten we weg.
        if len(rows) > 1:
            rows = rows[:-1]
        rows = rows[-count:]

        return Candles(
            timestamp=[r[0] for r in rows],
            open=[r[1] for r in rows],
            high=[r[2] for r in rows],
            low=[r[3] for r in rows],
            close=[r[4] for r in rows],
            volume=[r[5] for r in rows],
        )

    # -- niet van toepassing ------------------------------------------------- #

    async def account(self) -> AccountSnapshot:
        raise VenueError(
            "Deze databron kent geen account. Het saldo wordt door de "
            "paper-broker bijgehouden."
        )

    async def positions(self, symbol: str | None = None) -> list[VenuePosition]:
        return []

    async def place_order(self, symbol, side, units, stop_loss=None,
                          take_profit=None, comment="") -> OrderResult:
        raise VenueError(
            "Publieke marktdata kan niet handelen. Draai in papermodus; die "
            "simuleert de uitvoering."
        )

    async def close(self, ticket: str, units: float | None = None) -> OrderResult:
        raise VenueError("Publieke marktdata heeft geen posities.")

    async def modify_stop(self, ticket: str, stop_loss: float) -> OrderResult:
        raise VenueError("Publieke marktdata heeft geen posities.")

    async def health(self) -> dict:
        try:
            quote = await self.quote()
        except VenueError as err:
            return {"ok": False, "venue": self.name, "error": str(err)}
        return {
            "ok": True,
            "venue": self.name,
            "symbol": self.symbol,
            "price": quote.mid,
            "assumed_spread": self.assumed_spread,
            "costs_disabled": self.costs_disabled,
            "market_state": self._last_meta.get("marketState"),
            "warning": (
                "Kosten staan uit; elk resultaat is fictief."
                if self.costs_disabled
                else "Spread is aangenomen, niet gemeten bij een broker."
            ),
        }

    def describe(self) -> dict:
        base = super().describe()
        base.update({
            "simulated": False,
            "real_prices": True,
            "real_spread": False,
            "symbol": self.symbol,
            "assumed_spread": self.assumed_spread,
            "costs_disabled": self.costs_disabled,
            "source": "Yahoo Finance (ongedocumenteerd endpoint)",
        })
        return base
