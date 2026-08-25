"""IG en Capital.com: twee brokers, één API-vorm.

Capital.com heeft hun API gemodelleerd naar die van IG. Beide gebruiken een
sessie die je opent met een API-sleutel plus inloggegevens, en die vervolgens
twee headers teruggeeft - ``CST`` en ``X-SECURITY-TOKEN`` - die je bij elk
volgend verzoek meestuurt. De endpointvormen komen grotendeels overeen.

Daarom staat het gedeelde deel in ``IgStyleVenue`` en zijn de twee varianten
dun. Waar ze verschillen, verschillen ze echt:

**IG bevestigt orders in twee stappen.** Een order plaatsen levert een
``dealReference`` op, geen positie. Je moet daarna ``/confirms/{ref}`` opvragen
om te weten of hij is geaccepteerd en welk ``dealId`` eruit kwam. Dat lijkt
omslachtig maar is juist gunstig: het geeft een spoor dat na een verbroken
verbinding terug te vinden is.

**Capital.com laat sessies verlopen na tien minuten inactiviteit.** Bij een
verversingsinterval van twintig seconden speelt dat niet, maar na een pauze -
gesloten markt, noodstop - moet er opnieuw ingelogd worden. Beide adapters
loggen daarom automatisch opnieuw in bij een 401.

**Capital.com wil het API-sleutelwachtwoord, niet je accountwachtwoord.** Bij
het aanmaken van een sleutel stel je een apart wachtwoord in; dát hoort in het
``password``-veld. Je inlogwachtwoord invullen levert een 401 op die er precies
zo uitziet als verkeerde inloggegevens, en dan zoek je in de verkeerde richting.
IG gebruikt wél gewoon je accountwachtwoord.

Geen van beide is door mij tegen een echte verbinding getest. De parsing is
gebouwd op hun publieke documentatie en getoetst tegen nagebootste antwoorden.
Reken erop dat de eerste verbinding iets oplevert dat hier nog niet klopt; de
foutmeldingen zijn daarom zo specifiek mogelijk gemaakt.
"""

from __future__ import annotations

import asyncio
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

#: Timeouts per soort verzoek. Eén waarde voor alles deugt niet: een koers en
#: een order hebben een heel verschillende urgentie.
#:
#: **Koersen: kort.** Bij een pollinterval van tien seconden is een koers die
#: na veertien seconden binnenkomt al verouderd voordat je hem gebruikt. Beter
#: afbreken en de volgende cyclus afwachten dan de lus laten wachten op data
#: die je toch niet meer wilt.
QUOTE_TIMEOUT = ClientTimeout(total=6, connect=3)

#: **Orders: langer.** Hier is afbreken juist gevaarlijk: je weet dan niet of
#: de order is uitgevoerd. Liever wachten en een duidelijk antwoord krijgen.
ORDER_TIMEOUT = ClientTimeout(total=25, connect=5)

#: Alles daartussen: sessies, accountgegevens, historie.
TIMEOUT = ClientTimeout(total=15, connect=5)


class IgStyleVenue(ExecutionVenue):
    """Gedeelde laag voor IG en Capital.com."""

    runs_in_process = True
    is_simulated = False
    has_real_spread = True

    #: Per broker anders.
    base_urls: dict[str, str] = {}
    api_key_header = "X-CAP-API-KEY"
    #: Vertaling van onze tijdsframes naar de resolutie van de broker.
    resolutions: dict[str, str] = {}

    def __init__(
        self,
        session: ClientSession,
        api_key: str,
        identifier: str,
        password: str,
        environment: str = "demo",
        epic: str = "GOLD",
        trading_enabled: bool = False,
        max_units: float = 5.0,
    ) -> None:
        if environment not in self.base_urls:
            raise ValueError(
                f"Onbekende omgeving '{environment}'; kies uit {list(self.base_urls)}"
            )
        self._session = session
        self._api_key = self._clean(api_key)
        self._identifier = self._clean(identifier)
        # Wachtwoorden mogen bewust spaties bevatten; alleen onzichtbare
        # tekens eruit, niet trimmen.
        self._password = "".join(
            c for c in str(password)
            if c not in "\u200b\u200c\u200d\ufeff\u2060"
        )
        self._base = self.base_urls[environment]
        self.environment = environment
        self.epic = epic
        self.supports_trading = trading_enabled
        self.max_units = max_units

        self._cst: str | None = None
        self._token: str | None = None
        self._account_id: str | None = None
        self._lock = asyncio.Lock()
        #: Laatst bekende koers, om een gesloten markt te overbruggen zonder
        #: de integratie te laten falen.
        self._last_known_price: float | None = None
        self._last_known_spread: float = 0.0

    # -- sessie -------------------------------------------------------------- #

    def _headers(self, version: str = "1") -> dict:
        headers = {
            self.api_key_header: self._api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": version,
        }
        if self._cst:
            headers["CST"] = self._cst
        if self._token:
            headers["X-SECURITY-TOKEN"] = self._token
        return headers

    async def _login(self) -> None:
        """Open een sessie en bewaar de twee tokens."""
        # Vooraf controleren wat de broker anders met een cryptische code
        # afwijst. Dit scheelt een ronde waarin je moet raden welk veld fout is.
        if "@" in self._identifier:
            raise VenueError(
                f"'{self._identifier}' lijkt een e-mailadres. {self.name.upper()} "
                "wil je gebruikersnaam (login), zonder apenstaartje. Bij een "
                "demo-account is dat vaak je live-naam met een achtervoegsel; "
                "log in op het demo-platform en kijk welke naam daar staat."
            )
        if not self._identifier:
            raise VenueError("De gebruikersnaam is leeg.")
        if not self._api_key:
            raise VenueError("De API-sleutel is leeg.")

        url = f"{self._base}/session"
        body = {"identifier": self._identifier, "password": self._password}
        try:
            async with self._session.post(
                url, json=body, headers=self._headers(), timeout=TIMEOUT
            ) as response:
                payload = await response.json(content_type=None)
                if response.status == 401:
                    raise VenueError(
                        "Inloggen geweigerd. Controleer API-sleutel, gebruikersnaam "
                        "en wachtwoord, en of ze bij deze omgeving horen "
                        f"({self.environment})."
                    )
                if response.status >= 400:
                    # Nooit de foutcode van de broker weggooien: die zegt
                    # precies wat er mis is - vergrendeld account, sleutel
                    # uitgeschakeld, verkeerde omgeving - terwijl een eigen
                    # samenvatting je laat raden.
                    raise VenueError(
                        "Inloggen mislukte. "
                        + self._describe_error(response.status, payload)
                    )

                self._cst = response.headers.get("CST")
                self._token = response.headers.get("X-SECURITY-TOKEN")
                if not self._cst or not self._token:
                    raise VenueError(
                        "De broker gaf geen CST- of X-SECURITY-TOKEN-header terug; "
                        "zonder die twee is geen enkel vervolgverzoek mogelijk."
                    )
                self._account_id = self._extract_account_id(payload)
        except VenueError:
            raise
        except ClientError as err:
            raise VenueError(f"Netwerkfout bij inloggen: {type(err).__name__}: {err}") from err

    def _extract_account_id(self, payload: dict) -> str | None:
        return payload.get("currentAccountId") or payload.get("accountId")

    async def _request(
        self, method: str, path: str, version: str = "1",
        timeout: ClientTimeout | None = None, **kwargs,
    ):
        """Verzoek met automatische herlogin bij een verlopen sessie."""
        async with self._lock:
            if not self._cst:
                await self._login()

        for attempt in (1, 2):
            try:
                async with self._session.request(
                    method, f"{self._base}{path}",
                    headers=self._headers(version),
                    timeout=timeout or TIMEOUT, **kwargs,
                ) as response:
                    if response.status in (401, 403) and attempt == 1:
                        # Sessie verlopen. Capital.com doet dat na tien minuten
                        # inactiviteit; IG na langere tijd.
                        _LOGGER.debug("Sessie verlopen; opnieuw inloggen")
                        async with self._lock:
                            await self._login()
                        continue
                    payload = await response.json(content_type=None)
                    if response.status >= 400:
                        raise VenueError(self._describe_error(response.status, payload))
                    return payload
            except VenueError:
                raise
            except TimeoutError as err:
                # Zonder deze tak komt een timeout door als een kale
                # asyncio-fout, zonder te vertellen welk verzoek het betrof of
                # hoe lang hij heeft gewacht.
                limit = (timeout or TIMEOUT).total
                raise VenueError(
                    f"{self.name.upper()} antwoordde niet binnen {limit}s op "
                    f"{method} {path}. Bij koersen is dat geen ramp - de "
                    "volgende cyclus probeert opnieuw - maar bij herhaling wijst "
                    "het op een trage verbinding of drukte bij de broker."
                ) from err
            except ClientError as err:
                raise VenueError(
                    f"Netwerkfout richting broker: {type(err).__name__}: {err}"
                ) from err
        raise VenueError("Kon geen geldige sessie krijgen na opnieuw inloggen")

    #: Foutcodes van de broker naar iets waar je wat aan hebt.
    #:
    #: De ruwe codes zijn onbruikbaar voor wie de API niet kent.
    #: 'validation.pattern.invalid.authenticationRequest.identifier' zegt niet
    #: dat je je e-mailadres hebt ingevuld terwijl IG een gebruikersnaam wil,
    #: maar dat is bijna altijd wat er aan de hand is.
    ERROR_HINTS = {
        "validation.pattern.invalid.authenticationRequest.identifier": (
            "De gebruikersnaam wordt afgewezen op vorm. IG wil je LOGIN, niet je "
            "e-mailadres: geen apenstaartje, alleen letters en cijfers. Bij een "
            "demo-account is dat vaak je live-gebruikersnaam met een achtervoegsel. "
            "Log in op het demo-platform en kijk welke naam daar bovenaan staat."
        ),
        "validation.null-not-allowed.authenticationRequest.identifier": (
            "De gebruikersnaam is leeg aangekomen."
        ),
        "validation.pattern.invalid.authenticationRequest.password": (
            "Het wachtwoord wordt afgewezen op vorm; controleer op meegekopieerde "
            "spaties of tekens."
        ),
        "error.security.invalid-details": (
            "Gebruikersnaam of wachtwoord onjuist. Let op: het demo-account heeft "
            "eigen inloggegevens, los van je live account."
        ),
        "invalid.details": "Gebruikersnaam of wachtwoord onjuist.",
        "error.security.api-key-invalid": (
            "De API-sleutel wordt niet herkend. Een sleutel geldt voor één "
            "omgeving: een demo-sleutel werkt niet op live en omgekeerd."
        ),
        "error.security.api-key-disabled": "De API-sleutel staat op uitgeschakeld.",
        "error.security.api-key-revoked": "De API-sleutel is ingetrokken.",
        "error.security.account-locked": (
            "Het account is tijdelijk vergrendeld na te veel mislukte pogingen. "
            "Wacht een kwartier; log daarna eerst op het webplatform in om de "
            "vergrendeling op te heffen."
        ),
        "error.security.too-many-failed-attempts": (
            "Te veel mislukte inlogpogingen. Wacht een kwartier voordat je het "
            "opnieuw probeert; verder proberen verlengt de blokkade."
        ),
        "error.security.api-key-missing": "Er is geen API-sleutel meegestuurd.",
        "error.security.api-key-restricted": (
            "De sleutel is beperkt, bijvoorbeeld tot bepaalde IP-adressen."
        ),
        "error.public-api.key-missing": "Er is geen API-sleutel meegestuurd.",
        "error.security.oauth-token-invalid": "Sessietoken ongeldig.",
        "endpoint.unavailable.for.api-key": (
            "Dit endpoint is niet beschikbaar voor deze sleutel. Controleer of de "
            "sleutel bij de gekozen omgeving hoort: een live-sleutel werkt niet "
            "op demo en omgekeerd."
        ),
        "error.security.account-token-invalid": "Sessietoken ongeldig; opnieuw inloggen.",
        "error.public-api.exceeded-account-allowance": (
            "Rate limit bereikt. Wacht even; op demo liggen de limieten lager."
        ),
        "error.public-api.exceeded-api-key-allowance": "Rate limit van de sleutel bereikt.",
        "error.public-api.exceeded-account-historical-data-allowance": (
            "Het weekquotum voor historische koersen is op. IG rekent per "
            "opgehaald datapunt; op demo is dat quotum krap. Wacht tot de "
            "weekwissel of gebruik een hoger tijdsframe, dat kost minder punten."
        ),
        "error.price-history.io-error": "IG kon de koershistorie niet leveren.",
        "error.public-api.failure.encryption.required": (
            "Deze broker eist een versleuteld wachtwoord voor dit endpoint."
        ),
        "error.security.account-migrated": (
            "Het account is gemigreerd; log eerst in op het webplatform."
        ),
        "error.security.client-token-invalid": "Sleutel of sessie ongeldig.",
        "error.invalid.details": "Ongeldige inloggegevens.",
    }

    @classmethod
    def _describe_error(cls, status: int, payload) -> str:
        if isinstance(payload, dict):
            code = payload.get("errorCode") or payload.get("error") or ""
        else:
            code = str(payload)[:200]
        hint = cls.ERROR_HINTS.get(code, "")
        if not hint:
            # Onbekende code: geef in elk geval het patroon mee, dat helpt vaak
            # al om te zien welk veld de broker afwijst.
            for known, text in cls.ERROR_HINTS.items():
                if known.split(".")[-1] and known.split(".")[-1] in code:
                    hint = text
                    break
        return f"Broker gaf HTTP {status}: {code}. {hint}".strip()

    @staticmethod
    def _clean(value: str) -> str:
        """Verwijder onzichtbare tekens die bij kopiëren meekomen.

        Een niet-afbrekende ruimte of zero-width space is met het oog niet te
        zien maar laat elke patroonvalidatie falen. Zonder deze opschoning zoek
        je in de verkeerde richting, want de waarde ziet er goed uit.
        """
        invisible = "\u200b\u200c\u200d\ufeff\u00a0\u2060"
        cleaned = "".join(c for c in str(value) if c not in invisible)
        return cleaned.strip()

    # -- marktdata ----------------------------------------------------------- #

    #: Marktstatussen waarin er geen bied- en laatprijs is, maar er ook niets
    #: mis is. IG publiceert dan simpelweg geen quote.
    CLOSED_STATUSES = frozenset({
        "CLOSED", "EDITS_ONLY", "OFFLINE", "SUSPENDED",
        "AUCTION", "AUCTION_NO_EDIT", "ON_AUCTION", "ON_AUCTION_NO_EDITS",
    })

    async def quote(self, symbol: str | None = None) -> VenueQuote:
        epic = symbol or self.epic
        payload = await self._request(
            "GET", f"/markets/{epic}", version="3", timeout=QUOTE_TIMEOUT
        )
        snapshot = payload.get("snapshot") or {}
        status = str(snapshot.get("marketStatus", "TRADEABLE")).upper()

        bid = snapshot.get("bid")
        ask = snapshot.get("offer") or snapshot.get("ask")

        if bid is None or ask is None:
            # Bij een gesloten markt levert IG een snapshot zonder prijzen. Dat
            # is geen storing: goud sluit dagelijks kort en het hele weekend.
            # Hier een fout gooien zou de integratie 's avonds laten falen en
            # 's ochtends handmatig herstel vereisen.
            last = snapshot.get("netChange") is not None or status in self.CLOSED_STATUSES
            if status in self.CLOSED_STATUSES or last:
                fallback = self._last_known_price
                if fallback is None:
                    raise VenueError(
                        f"De markt voor '{epic}' is gesloten ({status}) en er is nog "
                        "geen eerdere koers bekend. Probeer het opnieuw zodra de "
                        "handel opent."
                    )
                half = self._last_known_spread / 2.0
                return VenueQuote(
                    bid=round(fallback - half, 3), ask=round(fallback + half, 3),
                    time=datetime.now(timezone.utc), tradeable=False,
                )
            raise VenueError(
                f"Geen bied- of laatprijs voor '{epic}' terwijl de markt op "
                f"'{status}' staat. Waarschijnlijk klopt de epic niet voor dit "
                "account. Zoek de juiste met de zoekfunctie van de adapter."
            )

        self._last_known_price = (float(bid) + float(ask)) / 2.0
        self._last_known_spread = float(ask) - float(bid)
        return VenueQuote(
            bid=float(bid), ask=float(ask),
            time=datetime.now(timezone.utc),
            tradeable=status not in self.CLOSED_STATUSES,
        )

    async def search_markets(self, term: str = "gold") -> list[dict]:
        """Zoek instrumenten bij de broker.

        Bestaat omdat epics niet te raden zijn en per account kunnen
        verschillen. In plaats van codes uit een documentatiepagina over te
        typen kun je zo vragen wat jóuw account werkelijk kent.
        """
        payload = await self._request(
            "GET", "/markets", version="1", params={"searchTerm": term}
        )
        out = []
        for market in payload.get("markets", []):
            out.append({
                "epic": market.get("epic"),
                "name": market.get("instrumentName"),
                "type": market.get("instrumentType"),
                "status": market.get("marketStatus"),
                "bid": market.get("bid"),
                "offer": market.get("offer"),
                "expiry": market.get("expiry"),
            })
        return out

    async def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        if timeframe not in self.resolutions:
            raise VenueError(
                f"Tijdsframe '{timeframe}' niet beschikbaar; kies uit "
                f"{list(self.resolutions)}"
            )
        epic = symbol or self.epic
        payload = await self._request(
            "GET", f"/prices/{epic}", version="3",
            params={
                "resolution": self.resolutions[timeframe],
                "max": min(count, 1000),
                # Zonder pageSize pagineert IG met een standaard van 20 stuks,
                # ongeacht wat je bij max opgeeft. Nul zet paginering uit en
                # levert de volledige reeks in één antwoord. Zonder deze
                # parameter kreeg de analyse er nooit meer dan twintig, en die
                # heeft er minstens zestig nodig.
                "pageSize": 0,
            },
        )
        rows = []
        for price in payload.get("prices", []):
            try:
                moment = self._parse_time(price.get("snapshotTimeUTC") or price["snapshotTime"])
                # Mid uit bied en laat: de analyse mag geen halve spread
                # vertekening oplopen.
                o = self._mid(price["openPrice"])
                h = self._mid(price["highPrice"])
                l = self._mid(price["lowPrice"])
                c = self._mid(price["closePrice"])
            except (KeyError, TypeError, ValueError):
                continue
            if None in (o, h, l, c):
                continue
            volume = float(price.get("lastTradedVolume") or 0)
            rows.append([int(moment.timestamp()), o, h, l, c, volume])

        if not rows:
            raise VenueError(
                f"Geen bruikbare candles voor '{epic}' op {timeframe}. Buiten "
                "handelsuren levert de broker soms een lege reeks."
            )
        rows.sort(key=lambda r: r[0])
        return Candles(
            timestamp=[r[0] for r in rows], open=[r[1] for r in rows],
            high=[r[2] for r in rows], low=[r[3] for r in rows],
            close=[r[4] for r in rows], volume=[r[5] for r in rows],
        )

    @staticmethod
    def _mid(level) -> float | None:
        """Beide brokers geven per candle een bid- en ask-waarde."""
        if isinstance(level, dict):
            bid, ask = level.get("bid"), level.get("ask") or level.get("offer")
            if bid is None or ask is None:
                return float(bid or ask) if (bid or ask) else None
            return (float(bid) + float(ask)) / 2.0
        return float(level) if level is not None else None

    @staticmethod
    def _parse_time(value: str) -> datetime:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(value[:26], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Onbekend tijdformaat: {value}")

    # -- account en posities -------------------------------------------------- #

    async def account(self) -> AccountSnapshot:
        payload = await self._request("GET", "/accounts")
        accounts = payload.get("accounts") or []
        if not accounts:
            raise VenueError("De broker gaf geen accounts terug")
        chosen = next(
            (a for a in accounts if a.get("accountId") == self._account_id), accounts[0]
        )
        balance = chosen.get("balance") or {}
        return AccountSnapshot(
            balance=float(balance.get("balance", 0)),
            equity=float(balance.get("balance", 0)) + float(balance.get("profitLoss", 0)),
            margin_used=float(balance.get("deposit", 0)),
            margin_available=float(balance.get("available", 0)),
            currency=chosen.get("currency", "EUR"),
            open_position_count=0,
        )

    async def positions(self, symbol: str | None = None) -> list[VenuePosition]:
        payload = await self._request("GET", "/positions")
        out = []
        wanted = symbol or self.epic
        for item in payload.get("positions", []):
            position = item.get("position") or {}
            market = item.get("market") or {}
            epic = market.get("epic")
            if wanted and epic and epic != wanted:
                continue
            direction = str(position.get("direction", "")).upper()
            out.append(VenuePosition(
                ticket=str(position.get("dealId")),
                symbol=epic or wanted,
                side="buy" if direction == "BUY" else "sell",
                units=float(position.get("size", 0)),
                open_price=float(position.get("level", 0)),
                current_price=float(market.get("bid") or 0) or None,
                stop_loss=(
                    float(position["stopLevel"]) if position.get("stopLevel") else None
                ),
                take_profit=(
                    float(position["limitLevel"]) if position.get("limitLevel") else None
                ),
                unrealised_pnl=(
                    float(position["upl"]) if position.get("upl") is not None else None
                ),
                comment=position.get("dealReference"),
            ))
        return out

    # -- handelen ------------------------------------------------------------- #

    def _guard(self, units: float) -> None:
        if not self.supports_trading:
            raise TradingDisabledError(
                "Handel staat uit voor deze venue. Schakel dit bewust in, en pas "
                "nadat de bewijsfase is geslaagd."
            )
        if units <= 0 or units > self.max_units:
            raise VenueError(
                f"Ordergrootte {units} buiten het toegestane bereik (0, {self.max_units}]."
            )

    async def place_order(
        self, symbol, side, units, stop_loss=None, take_profit=None, comment="",
    ) -> OrderResult:
        if side not in ("buy", "sell"):
            raise VenueError(f"Ongeldige richting: {side}")
        self._guard(units)

        epic = symbol or self.epic
        quote = await self.quote(epic)
        if not quote.tradeable:
            return OrderResult(success=False, error="Markt is niet verhandelbaar")
        requested = quote.ask if side == "buy" else quote.bid

        body = self._order_body(epic, side, units, stop_loss, take_profit, comment)
        sent = time.perf_counter()
        payload = await self._request(
            "POST", self._order_path, version="2", json=body,
            timeout=ORDER_TIMEOUT,
        )
        latency = (time.perf_counter() - sent) * 1000

        return await self._confirm(payload, requested, latency, comment)

    def _order_body(self, epic, side, units, stop_loss, take_profit, comment) -> dict:
        body = {
            "epic": epic,
            "direction": side.upper(),
            "size": units,
            "orderType": "MARKET",
            "guaranteedStop": False,
            "forceOpen": True,
            "currencyCode": "USD",
        }
        if stop_loss is not None:
            body["stopLevel"] = round(stop_loss, 2)
        if take_profit is not None:
            body["limitLevel"] = round(take_profit, 2)
        return body

    _order_path = "/positions"

    async def _confirm(
        self, payload: dict, requested: float, latency: float, comment: str
    ) -> OrderResult:
        """Standaard: de broker bevestigt direct. IG overschrijft dit."""
        reference = payload.get("dealReference")
        return OrderResult(
            success=bool(reference), ticket=reference,
            requested_price=requested, latency_ms=round(latency, 2),
            error=None if reference else "Geen dealReference ontvangen",
        )

    async def close(self, ticket: str, units: float | None = None) -> OrderResult:
        if not self.supports_trading:
            raise TradingDisabledError("Handel staat uit voor deze venue.")
        payload = await self._request(
            "DELETE", f"{self._order_path}/{ticket}", version="1",
            timeout=ORDER_TIMEOUT,
        )
        reference = payload.get("dealReference")
        return OrderResult(success=bool(reference), ticket=ticket)

    async def modify_stop(self, ticket: str, stop_loss: float) -> OrderResult:
        if not self.supports_trading:
            raise TradingDisabledError("Handel staat uit voor deze venue.")
        payload = await self._request(
            "PUT", f"{self._order_path}/{ticket}", version="2",
            json={"stopLevel": round(stop_loss, 2)}, timeout=ORDER_TIMEOUT,
        )
        return OrderResult(
            success=bool(payload.get("dealReference")), ticket=ticket,
            error=None if payload.get("dealReference") else "Stop niet aangepast",
        )

    async def modify_target(self, ticket: str, take_profit: float) -> OrderResult:
        """Zet of verplaats het winstdoel. Bij IG heet dat veld limitLevel."""
        if not self.supports_trading:
            raise TradingDisabledError("Handel staat uit voor deze venue.")
        payload = await self._request(
            "PUT", f"{self._order_path}/{ticket}", version="2",
            json={"limitLevel": round(take_profit, 2)}, timeout=ORDER_TIMEOUT,
        )
        return OrderResult(
            success=bool(payload.get("dealReference")), ticket=ticket,
            error=None if payload.get("dealReference") else "Doel niet aangepast",
        )

    async def health(self) -> dict:
        base = await super().health()
        base["environment"] = self.environment
        base["epic"] = self.epic
        base["trading_enabled"] = self.supports_trading
        return base

    def describe(self) -> dict:
        base = super().describe()
        base.update({
            "environment": self.environment, "epic": self.epic,
            "real_prices": True, "real_spread": True, "simulated": False,
        })
        return base


class IgVenue(IgStyleVenue):
    """IG Group. Order plaatsen is twee stappen: referentie, dan bevestiging."""

    name = "ig"
    api_key_header = "X-IG-API-KEY"
    base_urls = {
        "demo": "https://demo-api.ig.com/gateway/deal",
        "live": "https://api.ig.com/gateway/deal",
    }
    resolutions = {
        "1m": "MINUTE", "5m": "MINUTE_5", "15m": "MINUTE_15",
        "30m": "MINUTE_30", "1h": "HOUR", "4h": "HOUR_4", "1d": "DAY",
    }
    _order_path = "/positions/otc"

    def _extract_account_id(self, payload: dict) -> str | None:
        return payload.get("currentAccountId")

    async def _confirm(
        self, payload: dict, requested: float, latency: float, comment: str
    ) -> OrderResult:
        """Haal de bevestiging op bij ``/confirms/{dealReference}``.

        IG accepteert een order niet direct: je krijgt een referentie en moet
        daarna vragen wat ermee gebeurd is. Dat is extra werk, maar het levert
        een spoor op dat na een verbroken verbinding terug te vinden is - en
        dat is precies wat je nodig hebt om geen tweede order te versturen.
        """
        reference = payload.get("dealReference")
        if not reference:
            return OrderResult(
                success=False, requested_price=requested,
                latency_ms=round(latency, 2), error="Geen dealReference ontvangen",
            )

        # Kort wachten: de bevestiging is niet altijd meteen beschikbaar.
        for delay in (0.0, 0.3, 0.7):
            if delay:
                await asyncio.sleep(delay)
            try:
                confirm = await self._request("GET", f"/confirms/{reference}")
            except VenueError:
                continue
            status = str(confirm.get("dealStatus", "")).upper()
            if status == "ACCEPTED":
                level = confirm.get("level")
                return OrderResult(
                    success=True,
                    ticket=str(confirm.get("dealId") or reference),
                    fill_price=float(level) if level else None,
                    requested_price=requested,
                    units=float(confirm.get("size") or 0) or None,
                    latency_ms=round(latency, 2),
                )
            if status == "REJECTED":
                return OrderResult(
                    success=False, requested_price=requested,
                    latency_ms=round(latency, 2),
                    error=f"Order afgewezen: {confirm.get('reason', 'onbekende reden')}",
                )

        return OrderResult(
            success=False, ticket=reference, requested_price=requested,
            latency_ms=round(latency, 2),
            error=(
                f"Geen bevestiging voor {reference}. De order kan alsnog uitgevoerd "
                "zijn; posities worden nagekeken voordat er iets nieuws gebeurt."
            ),
        )

    def _order_body(self, epic, side, units, stop_loss, take_profit, comment) -> dict:
        body = super()._order_body(epic, side, units, stop_loss, take_profit, comment)
        body["expiry"] = "-"
        # IG accepteert een eigen referentie; hierop rust de bescherming tegen
        # dubbele orders na een verbroken verbinding.
        if comment:
            body["dealReference"] = "".join(
                c for c in comment if c.isalnum() or c in "-_"
            )[:30]
        return body


class CapitalVenue(IgStyleVenue):
    """Capital.com. Zelfde vorm als IG, maar bevestigt direct."""

    name = "capital"
    api_key_header = "X-CAP-API-KEY"
    base_urls = {
        "demo": "https://demo-api-capital.backend-capital.com/api/v1",
        "live": "https://api-capital.backend-capital.com/api/v1",
    }
    resolutions = {
        "1m": "MINUTE", "5m": "MINUTE_5", "15m": "MINUTE_15",
        "30m": "MINUTE_30", "1h": "HOUR", "4h": "HOUR_4", "1d": "DAY",
    }
    _order_path = "/positions"

    def _order_body(self, epic, side, units, stop_loss, take_profit, comment) -> dict:
        # Capital.com kent 'expiry' en 'currencyCode' niet op deze manier.
        body = {
            "epic": epic,
            "direction": side.upper(),
            "size": units,
            "guaranteedStop": False,
        }
        if stop_loss is not None:
            body["stopLevel"] = round(stop_loss, 2)
        if take_profit is not None:
            body["profitLevel"] = round(take_profit, 2)
        return body
