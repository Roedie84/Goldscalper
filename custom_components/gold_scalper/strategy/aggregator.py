"""Candles bouwen uit live koersen, zonder historie op te vragen.

Brokers rekenen historische koersen per datapunt af. IG's demo-quotum ligt rond
de tienduizend punten per week, en dat is binnen een dag op als je elke cyclus
candles ophaalt. Dit is de uitweg: bars opbouwen uit de quotes die je tóch al
binnenhaalt, zodat er nooit een historie-aanvraag nodig is.

**Wat het kost aan nauwkeurigheid.** Een echte bar bevat honderden ticks; jij
bemonstert elke twintig seconden. De uiterste prijzen daartussen mis je, dus
high en low vallen te krap uit. Gemeten op gesimuleerde M5-data levert dat
ongeveer 95% van de werkelijke ATR op - een onderschatting van vijf procent.

Die richting is ongunstig maar niet gevaarlijk: een te lage ATR maakt de
kostenpoort té streng, waardoor je kansen mist in plaats van slechte trades
neemt. Er wordt daarom een correctiefactor toegepast, en het rapport markeert
runs die op zelfgebouwde bars draaien - anders vergelijk je ze straks met runs
op echte brokerdata terwijl de cijfers niet dezelfde betekenis hebben.

**Het overleeft een herstart.** Zonder opslag zou je na elke update opnieuw
uren moeten wachten voordat de analyse op gang komt. De opgebouwde bars gaan
daarom naar ``.storage``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..analysis.signals import Candles

_LOGGER = logging.getLogger(__name__)

BAR_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

#: Correctie op de ATR omdat periodiek bemonsteren de uitersten mist.
#:
#: Gemeten over acht gesimuleerde markten en vijf bemonsteringssnelheden, met
#: bars uitgelijnd op dezelfde tijdstempels. De spreiding tussen markten is
#: klein (±0,02 bij vijftien monsters), dus dit is een stabiel getal.
#:
#: Een eerdere versie van deze tabel kwam uit één enkele meting en zat er
#: structureel naast: 1,05 waar 1,086 nodig was. Eén marktreeks is te weinig om
#: een constante op te baseren.
_CALIBRATION = {5: 1.408, 10: 1.155, 15: 1.086, 30: 1.035, 60: 1.009}


def sampling_correction(samples_per_bar: float) -> float:
    """Hoeveel de gemeten range vermoedelijk te krap is.

    Boven de zestig monsters per bar is de correctie verwaarloosbaar; daaronder
    loopt hij op tot ruim twintig procent bij vijf monsters.
    """
    if samples_per_bar >= 60:
        return 1.0
    known = sorted(_CALIBRATION)
    if samples_per_bar <= known[0]:
        return _CALIBRATION[known[0]]
    for low, high in zip(known, known[1:]):
        if low <= samples_per_bar <= high:
            span = high - low
            weight = (samples_per_bar - low) / span if span else 0.0
            return _CALIBRATION[low] + weight * (_CALIBRATION[high] - _CALIBRATION[low])
    return 1.0


@dataclass(slots=True)
class _Bar:
    """Eén bar in aanbouw."""

    start: int
    open: float
    high: float
    low: float
    close: float
    samples: int = 1

    def update(self, price: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.samples += 1

    def as_row(self) -> list:
        return [self.start, self.open, self.high, self.low, self.close, float(self.samples)]


class QuoteAggregator:
    """Bouwt candles uit binnenkomende koersen."""

    def __init__(self, timeframe: str, max_bars: int = 800) -> None:
        if timeframe not in BAR_SECONDS:
            raise ValueError(f"Onbekend tijdsframe: {timeframe}")
        self.timeframe = timeframe
        self.bar_seconds = BAR_SECONDS[timeframe]
        self.max_bars = max_bars
        self._closed: list[list] = []
        self._current: _Bar | None = None

    # -- opbouwen ------------------------------------------------------------ #

    def add(self, price: float, moment: datetime | None = None) -> bool:
        """Voeg een koers toe. Geeft True als er een bar is afgesloten.

        Er wordt bewust met de mid gewerkt en niet met bid of ask: anders
        krijgt elke indicator een halve spread aan vertekening mee.
        """
        moment = moment or datetime.now(timezone.utc)
        timestamp = int(moment.timestamp())
        start = (timestamp // self.bar_seconds) * self.bar_seconds

        if self._current is None:
            self._current = _Bar(start, price, price, price, price)
            return False

        if start > self._current.start:
            # Een bar met één monster zegt niets over high en low; toch
            # bewaren, want een gat in de reeks is erger dan een grove bar.
            self._closed.append(self._current.as_row())
            if len(self._closed) > self.max_bars:
                del self._closed[: len(self._closed) - self.max_bars]
            self._current = _Bar(start, price, price, price, price)
            return True

        if start < self._current.start:
            # Klok teruggesprongen; negeren in plaats van de reeks bederven.
            _LOGGER.debug("Koers met een oudere tijdstempel genegeerd")
            return False

        self._current.update(price)
        return False

    # -- uitlezen ------------------------------------------------------------ #

    @property
    def bar_count(self) -> int:
        return len(self._closed)

    @property
    def average_samples(self) -> float:
        """Gemiddeld aantal monsters per afgesloten bar."""
        recent = self._closed[-30:]
        if not recent:
            return 0.0
        return sum(row[5] for row in recent) / len(recent)

    @property
    def correction(self) -> float:
        return sampling_correction(self.average_samples)

    def candles(self, count: int | None = None) -> Candles:
        """Afgesloten bars als Candles.

        De lopende bar hoort er niet bij: die verandert nog, en handelen op
        een onvoltooide bar maakt papier en live onvergelijkbaar.
        """
        rows = self._closed[-count:] if count else list(self._closed)
        if not rows:
            raise ValueError("Nog geen afgesloten bars")
        return Candles(
            timestamp=[int(r[0]) for r in rows],
            open=[r[1] for r in rows],
            high=[r[2] for r in rows],
            low=[r[3] for r in rows],
            close=[r[4] for r in rows],
            volume=[r[5] for r in rows],
        )

    def progress(self, needed: int) -> dict:
        """Hoe ver is het opwarmen, en hoe lang duurt de rest nog."""
        remaining = max(0, needed - self.bar_count)
        return {
            "bars": self.bar_count,
            "needed": needed,
            "remaining": remaining,
            "ready": remaining == 0,
            "eta_minutes": round(remaining * self.bar_seconds / 60),
            "samples_per_bar": round(self.average_samples, 1),
            "atr_correction": round(self.correction, 3),
        }

    # -- opslag -------------------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "bars": self._closed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], timeframe: str) -> QuoteAggregator:
        """Herstel na een herstart.

        Zonder dit begint elke update de opwarmfase opnieuw, en op M5 kost dat
        vijf uur voordat de analyse weer iets kan zeggen.
        """
        aggregator = cls(timeframe)
        stored = data.get("bars") or []
        if data.get("timeframe") != timeframe:
            _LOGGER.info(
                "Bewaarde bars zijn van tijdsframe %s, nu %s; opnieuw beginnen",
                data.get("timeframe"), timeframe,
            )
            return aggregator
        for row in stored:
            try:
                if len(row) >= 6 and all(isinstance(x, (int, float)) for x in row[:6]):
                    aggregator._closed.append(list(row))
            except (TypeError, ValueError):
                continue
        if aggregator._closed:
            _LOGGER.info(
                "Opwarmfase hervat met %d eerder opgebouwde bars",
                len(aggregator._closed),
            )
        return aggregator
