"""Simulator-venue: draait zonder broker, zonder account, zonder netwerk.

Bedoeld om de machinerie te valideren voordat je je ergens aanmeldt. Alles
werkt: sensoren vullen zich, de strategie evalueert, papertrades belanden in de
database, de exits schuiven stops op, het keuringsrapport wordt gevuld.

**Wat je hiermee níet kunt vaststellen: of de strategie werkt.** Synthetische
data heeft geen marktstructuur - geen nieuws, geen orderflow, geen deelnemers
die op elkaar reageren. Elke winst die hier ontstaat is een eigenschap van mijn
ruisgenerator, niet van goud. Daarom weigert ``LiveGate`` een simulatorrun
categorisch vrij te geven, en draagt het rapport een merkteken.

Het prijsmodel is fractale ruis: een som van gladgestreken willekeur op
verschillende tijdschalen. Dat geeft een continu pad dat er als een koers
uitziet, met de juiste orde van grootte aan beweging per minuut.

De belangrijkste eigenschap is dat ``price(t)`` een zuivere functie is van de
tijd en het zaad. Er wordt niets opgebouwd of onthouden. Dat is nodig omdat de
coordinator herhaaldelijk om dezelfde candles vraagt: een generator die bij elke
aanroep nieuwe willekeur produceert, zou een geschiedenis opleveren die
onderweg verandert, en dan meten de indicatoren onzin.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

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

SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


def _hash01(n: int, seed: int) -> float:
    """Deterministische pseudo-willekeur in [0,1) uit een geheel getal.

    Een eigen hash in plaats van ``random``, omdat er geen toestand mag zijn:
    dezelfde index moet altijd dezelfde waarde geven, ongeacht de volgorde
    waarin er gevraagd wordt.

    De ``int()``-conversies zijn geen overbodige voorzichtigheid: Home Assistant
    geeft elke NumberSelector-waarde terug als float, dus een zaad uit de
    config flow arriveert als 20260823.0 en dan faalt de bitwise operatie.
    """
    x = (int(n) * 0x9E3779B1 + int(seed) * 0x85EBCA77) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x2545F491) & 0xFFFFFFFF
    x ^= x >> 13
    x = (x * 0x27220A95) & 0xFFFFFFFF
    x ^= x >> 16
    return x / 0xFFFFFFFF


def _smooth_noise(t: float, seed: int) -> float:
    """Gladde ruis in [-1,1] via cosinus-interpolatie tussen roosterpunten."""
    i = math.floor(t)
    frac = t - i
    a, b = _hash01(i, seed), _hash01(i + 1, seed)
    weight = (1.0 - math.cos(frac * math.pi)) / 2.0
    return (a * (1.0 - weight) + b * weight) * 2.0 - 1.0


def _fractal(t: float, seed: int, octaves: int = 5) -> float:
    """Som van ruis op halverende tijdschalen; geeft een realistisch koerspad."""
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    norm = 0.0
    for octave in range(octaves):
        total += _smooth_noise(t * frequency, seed + octave * 7919) * amplitude
        norm += amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return total / norm


def _session_activity(hour_utc: float) -> float:
    """Volatiliteitsprofiel over de dag.

    Goud beweegt het meest tijdens de overlap Londen/New York (circa 13:00-17:00
    UTC) en ligt vrijwel stil in de Aziatische ochtend. Zonder dit profiel zou
    het handelsvenster in de strategie geen enkel verschil maken, en dan test je
    die filterlogica niet.
    """
    london = math.exp(-((hour_utc - 10.0) ** 2) / 18.0)
    newyork = math.exp(-((hour_utc - 15.0) ** 2) / 12.0)
    asia = 0.35 * math.exp(-((hour_utc - 2.0) ** 2) / 20.0)
    return 0.25 + 1.4 * max(london, newyork) + asia


class SimulatorVenue(ExecutionVenue):
    """Genereert goudkoersen lokaal. Geen account, geen netwerk."""

    name = "simulator"
    runs_in_process = True
    #: Bewust False: er valt hier niets echt te handelen, en de coordinator
    #: mag nooit denken dat dit een uitvoeringspad is.
    supports_trading = False
    #: Merkteken waar de poort en het rapport op controleren.
    is_simulated = True
    #: De spread wordt gegenereerd, niet gemeten bij een tegenpartij.
    has_real_spread = False

    def __init__(
        self,
        seed: int = 20260823,
        base_price: float = 3300.0,
        spread: float = 0.20,
        daily_range: float = 30.0,
        m1_atr: float = 0.35,
        balance: float = 10_000.0,
    ) -> None:
        # Alle numerieke instellingen komen mogelijk als float uit de config
        # flow. Coërceren gebeurt hier, op de grens, in plaats van verspreid
        # door de rekenkern.
        self.seed = int(seed)
        self.base_price = float(base_price)
        self.spread = float(spread)
        #: Typische afstand tussen dagelijkse high en low, in USD per ounce.
        #: Goud rond de 3300 doet doorgaans 25 tot 40 dollar op een dag.
        self.daily_range = float(daily_range)
        #: Doel-ATR op de minuutgrafiek. Dit is het getal dat bepaalt of de
        #: strategie überhaupt trades kan vinden, want de kostenpoort vergelijkt
        #: het winstdoel (een veelvoud van de ATR) met de spread.
        self.m1_atr = float(m1_atr)
        self.balance = float(balance)

    # -- prijsmodel --------------------------------------------------------- #

    def price_at(self, timestamp: float) -> float:
        """Midprijs op een tijdstip. Zuivere functie van tijd en zaad."""
        # Drie tijdschalen: dagen, uren, minuten. Samen geven ze zowel een
        # herkenbare dagtrend als de kleine bewegingen waar een scalper op mikt.
        #
        # De coëfficiënten zijn empirisch geijkt zodat de uitkomst de opgegeven
        # daily_range en m1_atr benadert. Zonder die ijking produceert fractale
        # ruis een pad dat er wel uitziet als een koers maar bewegingen heeft
        # van een verkeerde orde van grootte, en dan test je de kostenpoort met
        # onrealistische getallen.
        slow = _fractal(timestamp / 86400.0, self.seed, octaves=4) * self.daily_range * 2.10
        medium = _fractal(timestamp / 5400.0, self.seed + 101, octaves=4) * self.daily_range * 0.55
        fast = _fractal(timestamp / 150.0, self.seed + 202, octaves=5) * self.m1_atr * 4.75

        hour = (timestamp % 86400) / 3600.0
        activity = _session_activity(hour)
        return self.base_price + slow + (medium + fast) * activity

    def spread_at(self, timestamp: float) -> float:
        """Spread met een realistisch profiel: breder buiten de actieve uren."""
        hour = (timestamp % 86400) / 3600.0
        activity = _session_activity(hour)
        # Weinig activiteit betekent bredere spread; daarbovenop wat ruis en af
        # en toe een uitschieter, zoals rond nieuws.
        widening = 1.0 + 0.9 * max(0.0, 1.0 - activity)
        jitter = 0.85 + 0.3 * _hash01(int(timestamp // 60), self.seed + 303)
        spike = 2.6 if _hash01(int(timestamp // 900), self.seed + 404) > 0.97 else 1.0
        return round(self.spread * widening * jitter * spike, 4)

    def _is_market_open(self, moment: datetime) -> bool:
        """Goud handelt niet in het weekend. Vrijdag 21:00 tot zondag 22:00 UTC dicht."""
        weekday, hour = moment.weekday(), moment.hour
        if weekday == 5:
            return False
        if weekday == 4 and hour >= 21:
            return False
        if weekday == 6 and hour < 22:
            return False
        return True

    # -- interface ---------------------------------------------------------- #

    async def quote(self, symbol: str) -> VenueQuote:
        now = datetime.now(timezone.utc)
        timestamp = now.timestamp()
        mid = self.price_at(timestamp)
        half = self.spread_at(timestamp) / 2.0
        return VenueQuote(
            bid=round(mid - half, 3),
            ask=round(mid + half, 3),
            time=now,
            tradeable=self._is_market_open(now),
        )

    async def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        if timeframe not in SECONDS:
            raise VenueError(
                f"Simulator kent tijdsframe '{timeframe}' niet; kies uit {list(SECONDS)}"
            )
        step = SECONDS[timeframe]
        now = int(datetime.now(timezone.utc).timestamp())
        # Alleen afgesloten candles, net als een echte bron.
        last_closed = (now // step) * step - step

        ts, o, h, l, c, v = [], [], [], [], [], []
        for i in range(count - 1, -1, -1):
            start = last_closed - i * step
            # Vier sub-samples per candle geven een geloofwaardige high/low
            # zonder dat het duur wordt.
            samples = [self.price_at(start + step * f) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
            ts.append(start)
            o.append(round(samples[0], 3))
            c.append(round(samples[-1], 3))
            h.append(round(max(samples), 3))
            l.append(round(min(samples), 3))
            hour = (start % 86400) / 3600.0
            v.append(round(400 * _session_activity(hour) * (0.6 + _hash01(start, self.seed + 505)), 1))

        return Candles(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)

    async def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            balance=self.balance, equity=self.balance,
            margin_used=0.0, margin_available=self.balance,
            currency="EUR", open_position_count=0,
        )

    async def positions(self, symbol: str | None = None) -> list[VenuePosition]:
        # De simulator houdt zelf geen posities aan; die leven in de
        # paper-broker. Altijd leeg teruggeven houdt het afstemmen bij het
        # opstarten consistent.
        return []

    async def place_order(self, symbol, side, units, stop_loss=None,
                          take_profit=None, comment="") -> OrderResult:
        raise VenueError(
            "De simulator plaatst geen orders. Draai in papermodus; die "
            "simuleert de uitvoering inclusief alle kosten."
        )

    async def close(self, ticket: str, units: float | None = None) -> OrderResult:
        raise VenueError("De simulator heeft geen posities om te sluiten.")

    async def modify_stop(self, ticket: str, stop_loss: float) -> OrderResult:
        raise VenueError("De simulator heeft geen posities om aan te passen.")

    async def health(self) -> dict:
        quote = await self.quote("XAU_USD")
        return {
            "ok": True,
            "venue": self.name,
            "simulated": True,
            "price": quote.mid,
            "spread": round(quote.spread, 4),
            "market_open": quote.tradeable,
            "warning": (
                "Synthetische data. Geschikt om de installatie te controleren, "
                "ongeschikt om de strategie te beoordelen."
            ),
        }

    def describe(self) -> dict:
        base = super().describe()
        base.update({
            "simulated": True,
            "seed": self.seed,
            "base_price": self.base_price,
            "nominal_spread": self.spread,
            "daily_range": self.daily_range,
            "m1_atr_target": self.m1_atr,
        })
        return base
