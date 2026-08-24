"""OANDA v20 REST-uitvoeringslaag.

Draait volledig binnen het Home Assistant-proces. Gebruikt alleen ``aiohttp``,
dat al onderdeel van Home Assistant is, dus er komt geen enkele nieuwe
afhankelijkheid bij en er hoeft geen tweede machine of tweede proces te draaien.

Instellen:

1. Open een OANDA-account (demo of live).
2. Accountportaal -> My Services -> Manage API Access -> token genereren.
3. Noteer je account-ID (formaat ``001-004-1234567-001``).

Twee dingen die anders werken dan bij MetaTrader en waar je op moet letten.

**Eenheden.** OANDA rekent XAU_USD in units, waarbij één unit één troy ounce
is. MT5 rekent in lots van 100 ounce. Deze adapter praat naar buiten toe altijd
in ounces, zodat er nergens in de strategie een factor 100 kan wegvallen.

**Richting via het teken.** Een short is bij OANDA geen aparte ordersoort maar
een negatief aantal units. Dat is compacter maar ook makkelijker om per ongeluk
fout te doen, dus de vertaling gebeurt op één plek.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from aiohttp import ClientError, ClientSession, ClientTimeout

from ..analysis.signals import Candles
from .adapter import (
    AccountSnapshot,
    ExecutionVenue,
    OrderResult,
    TradingDisabledError,
    VenueError,
    VenuePosition,
    VenueQuote,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = ClientTimeout(total=15)

#: Onze generieke tijdsframes naar OANDA-granulariteiten.
GRANULARITY = {
    "5s": "S5", "10s": "S10", "30s": "S30",
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
}

ENVIRONMENTS = {
    "practice": ("https://api-fxpractice.oanda.com", "https://stream-fxpractice.oanda.com"),
    "live": ("https://api-fxtrade.oanda.com", "https://stream-fxtrade.oanda.com"),
}


class OandaVenue(ExecutionVenue):
    """Praat rechtstreeks met OANDA v20 vanuit Home Assistant."""

    name = "oanda"
    runs_in_process = True

    def __init__(
        self,
        session: ClientSession,
        token: str,
        account_id: str,
        environment: str = "practice",
        trading_enabled: bool = False,
        max_units: float = 10.0,
    ) -> None:
        if environment not in ENVIRONMENTS:
            raise ValueError(f"Onbekende omgeving '{environment}'; kies practice of live")
        self._session = session
        self._token = token
        self._account = account_id
        self._base, self._stream_base = ENVIRONMENTS[environment]
        self.environment = environment
        self.supports_trading = trading_enabled
        #: Hard plafond in ounces. Een strategiebug die 1000 ounce probeert te
        #: kopen hoort hier te stranden, niet bij de broker.
        self.max_units = max_units

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    async def _request(self, method: str, path: str, **kwargs):
        url = f"{self._base}{path}"
        try:
            async with self._session.request(
                method, url, headers=self._headers, timeout=TIMEOUT, **kwargs
            ) as response:
                payload = await response.json()
                if response.status == 401:
                    raise VenueError("OANDA weigert het token. Controleer je API-token.")
                if response.status == 403:
                    raise VenueError(
                        "OANDA weigert toegang tot dit account. Controleer het account-ID "
                        "en of het token bij dezelfde omgeving hoort (practice of live)."
                    )
                if response.status >= 400:
                    message = (
                        payload.get("errorMessage")
                        or payload.get("rejectReason")
                        or str(payload)[:200]
                    )
                    raise VenueError(f"OANDA HTTP {response.status}: {message}")
                return payload
        except VenueError:
            raise
        except ClientError as err:
            raise VenueError(f"Netwerkfout richting OANDA: {err}") from err

    @staticmethod
    def _instrument(symbol: str) -> str:
        """XAUUSD, XAU/USD en XAU_USD accepteren we allemaal."""
        cleaned = symbol.upper().replace("/", "").replace("_", "").replace("-", "")
        if len(cleaned) == 6:
            return f"{cleaned[:3]}_{cleaned[3:]}"
        return symbol.upper()

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    # -- marktdata ---------------------------------------------------------- #

    async def quote(self, symbol: str) -> VenueQuote:
        payload = await self._request(
            "GET",
            f"/v3/accounts/{self._account}/pricing",
            params={"instruments": self._instrument(symbol)},
        )
        prices = payload.get("prices") or []
        if not prices:
            raise VenueError(f"OANDA gaf geen koers voor {symbol}")
        price = prices[0]
        bids, asks = price.get("bids") or [], price.get("asks") or []
        if not bids or not asks:
            raise VenueError(f"Geen bied- of laatprijs voor {symbol}; markt mogelijk dicht")
        return VenueQuote(
            bid=float(bids[0]["price"]),
            ask=float(asks[0]["price"]),
            time=self._parse_time(price["time"]),
            tradeable=price.get("tradeable", True),
        )

    async def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        if timeframe not in GRANULARITY:
            raise VenueError(
                f"OANDA kent tijdsframe '{timeframe}' niet; kies uit {list(GRANULARITY)}"
            )
        payload = await self._request(
            "GET",
            f"/v3/instruments/{self._instrument(symbol)}/candles",
            params={
                "granularity": GRANULARITY[timeframe],
                "count": min(count, 5000),
                # M = midprijs. Voor de analyse willen we mid, niet bid of ask:
                # anders krijgt elke indicator een halve spread aan vertekening.
                "price": "M",
            },
        )
        rows = []
        for candle in payload.get("candles", []):
            if not candle.get("complete"):
                # Onvoltooide candles overslaan. Handelen op een lopende candle
                # betekent handelen op een waarde die nog verandert.
                continue
            mid = candle["mid"]
            rows.append([
                int(self._parse_time(candle["time"]).timestamp()),
                float(mid["o"]), float(mid["h"]), float(mid["l"]), float(mid["c"]),
                float(candle.get("volume", 0)),
            ])
        if not rows:
            raise VenueError(f"Geen voltooide candles voor {symbol} op {timeframe}")
        rows.sort(key=lambda r: r[0])
        return Candles(
            timestamp=[r[0] for r in rows],
            open=[r[1] for r in rows],
            high=[r[2] for r in rows],
            low=[r[3] for r in rows],
            close=[r[4] for r in rows],
            volume=[r[5] for r in rows],
        )

    # -- account ------------------------------------------------------------ #

    async def account(self) -> AccountSnapshot:
        payload = await self._request("GET", f"/v3/accounts/{self._account}/summary")
        summary = payload["account"]
        return AccountSnapshot(
            balance=float(summary["balance"]),
            equity=float(summary["NAV"]),
            margin_used=float(summary.get("marginUsed", 0)),
            margin_available=float(summary.get("marginAvailable", 0)),
            currency=summary.get("currency", "EUR"),
            open_position_count=int(summary.get("openTradeCount", 0)),
        )

    async def positions(self, symbol: str | None = None) -> list[VenuePosition]:
        payload = await self._request("GET", f"/v3/accounts/{self._account}/openTrades")
        out = []
        wanted = self._instrument(symbol) if symbol else None
        for trade in payload.get("trades", []):
            if wanted and trade["instrument"] != wanted:
                continue
            units = float(trade["currentUnits"])
            out.append(VenuePosition(
                ticket=str(trade["id"]),
                symbol=trade["instrument"],
                side="buy" if units > 0 else "sell",
                units=abs(units),
                open_price=float(trade["price"]),
                stop_loss=(
                    float(trade["stopLossOrder"]["price"])
                    if trade.get("stopLossOrder") else None
                ),
                take_profit=(
                    float(trade["takeProfitOrder"]["price"])
                    if trade.get("takeProfitOrder") else None
                ),
                unrealised_pnl=float(trade.get("unrealizedPL", 0)),
                open_time=self._parse_time(trade["openTime"]),
                comment=(trade.get("clientExtensions") or {}).get("comment"),
            ))
        return out

    # -- handelen ----------------------------------------------------------- #

    def _guard(self, units: float) -> None:
        if not self.supports_trading:
            raise TradingDisabledError(
                "Handel staat uit voor deze venue. Schakel dit bewust in, en pas "
                "nadat de bewijsfase is geslaagd."
            )
        if units <= 0 or units > self.max_units:
            raise VenueError(
                f"Ordergrootte {units} ounce buiten het toegestane bereik "
                f"(0, {self.max_units}]."
            )

    async def place_order(
        self,
        symbol: str,
        side: str,
        units: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
    ) -> OrderResult:
        if side not in ("buy", "sell"):
            raise VenueError(f"Ongeldige richting: {side}")
        self._guard(units)

        instrument = self._instrument(symbol)
        quote = await self.quote(symbol)
        if not quote.tradeable:
            return OrderResult(success=False, error="Markt is momenteel niet verhandelbaar")
        requested = quote.ask if side == "buy" else quote.bid

        order: dict = {
            "type": "MARKET",
            "instrument": instrument,
            # Richting zit in het teken: positief is long, negatief is short.
            "units": str(units if side == "buy" else -units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
        }
        if stop_loss is not None:
            order["stopLossOnFill"] = {"price": f"{stop_loss:.3f}", "timeInForce": "GTC"}
        if take_profit is not None:
            order["takeProfitOnFill"] = {"price": f"{take_profit:.3f}", "timeInForce": "GTC"}
        if comment:
            order["clientExtensions"] = {"comment": comment[:128], "tag": "gold_scalper"}

        sent = time.perf_counter()
        payload = await self._request(
            "POST", f"/v3/accounts/{self._account}/orders", json={"order": order}
        )
        latency = (time.perf_counter() - sent) * 1000

        fill = payload.get("orderFillTransaction")
        if not fill:
            reject = payload.get("orderRejectTransaction") or {}
            return OrderResult(
                success=False,
                requested_price=requested,
                latency_ms=round(latency, 2),
                error=reject.get("rejectReason") or "Order niet uitgevoerd",
            )

        return OrderResult(
            success=True,
            ticket=str(fill.get("tradeOpened", {}).get("tradeID") or fill.get("id")),
            fill_price=float(fill["price"]),
            requested_price=requested,
            units=abs(float(fill["units"])),
            latency_ms=round(latency, 2),
        )

    async def close(self, ticket: str, units: float | None = None) -> OrderResult:
        if not self.supports_trading:
            raise TradingDisabledError("Handel staat uit voor deze venue.")
        body = {"units": "ALL" if units is None else str(abs(units))}
        sent = time.perf_counter()
        payload = await self._request(
            "PUT", f"/v3/accounts/{self._account}/trades/{ticket}/close", json=body
        )
        latency = (time.perf_counter() - sent) * 1000
        fill = payload.get("orderFillTransaction")
        if not fill:
            return OrderResult(
                success=False, ticket=ticket, latency_ms=round(latency, 2),
                error="Sluiten niet uitgevoerd",
            )
        return OrderResult(
            success=True,
            ticket=ticket,
            fill_price=float(fill["price"]),
            units=abs(float(fill["units"])),
            latency_ms=round(latency, 2),
        )

    async def modify_stop(self, ticket: str, stop_loss: float) -> OrderResult:
        """Verplaats de stop. Draagt break-even en trailing.

        OANDA plaatst de stop server-side, dus hij blijft staan als Home
        Assistant, je netwerk of je hele machine wegvalt. Dat is precies de
        bescherming die je wilt bij onbeheerd draaien.
        """
        if not self.supports_trading:
            raise TradingDisabledError("Handel staat uit voor deze venue.")
        payload = await self._request(
            "PUT",
            f"/v3/accounts/{self._account}/trades/{ticket}/orders",
            json={"stopLoss": {"price": f"{stop_loss:.3f}", "timeInForce": "GTC"}},
        )
        created = payload.get("stopLossOrderTransaction")
        return OrderResult(
            success=bool(created),
            ticket=ticket,
            error=None if created else "Stop niet aangepast",
        )

    async def health(self) -> dict:
        base = await super().health()
        base["environment"] = self.environment
        base["trading_enabled"] = self.supports_trading
        return base
