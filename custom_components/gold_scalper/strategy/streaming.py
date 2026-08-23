"""Incrementele indicatoren met O(1) update per tick.

Het probleem met ``analysis/`` is dat elke evaluatie alle indicatoren opnieuw
berekent over het volledige venster. Bij 300 candles en ~40 indicatoren is dat
tienduizenden operaties per tick, terwijl er maar één nieuw datapunt is
bijgekomen. Dat is O(n) werk voor O(1) nieuwe informatie.

Deze module houdt in plaats daarvan toestand bij. Een EMA is per definitie
incrementeel; een RSI en ATR ook, mits je de Wilder-smoothing als lopende
waarde bewaart. Rolling min/max gebruiken een monotone deque, wat geamortiseerd
O(1) is in plaats van O(n) per venster.

De uitkomsten zijn numeriek identiek aan de batch-versie in ``analysis/``,
en dat wordt in de tests ook afgedwongen. Snelheid die de cijfers verandert is
geen optimalisatie maar een bug.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


class RollingSum:
    """Vensteroptelling met O(1) toevoegen.

    Let op de periodieke hertelling: door herhaald optellen en aftrekken van
    floats stapelt drijvende-kommafout zich op. Bij honderden ticks per minuut
    over een handelsdag loopt die zichtbaar op, dus wordt de som elke 10.000
    updates opnieuw berekend uit het venster.
    """

    __slots__ = ("period", "_values", "_sum", "_ops")

    def __init__(self, period: int) -> None:
        self.period = period
        self._values: deque[float] = deque(maxlen=period)
        self._sum = 0.0
        self._ops = 0

    def push(self, value: float) -> float | None:
        if len(self._values) == self.period:
            self._sum -= self._values[0]
        self._values.append(value)
        self._sum += value
        self._ops += 1
        if self._ops >= 10_000:
            self._sum = math.fsum(self._values)
            self._ops = 0
        return self._sum if len(self._values) == self.period else None

    @property
    def value(self) -> float | None:
        return self._sum if len(self._values) == self.period else None

    @property
    def mean(self) -> float | None:
        return self._sum / self.period if len(self._values) == self.period else None

    @property
    def ready(self) -> bool:
        return len(self._values) == self.period


class RollingStdev:
    """Standaarddeviatie via som en som-van-kwadraten, O(1) per update.

    Numeriek gevoeliger dan een tweepassige berekening: bij grote waarden met
    kleine spreiding (precies wat goud rond 3300 doet) kan ``E[x^2] - E[x]^2``
    catastrofale afronding geven. Daarom wordt er gecentreerd rond de eerste
    waarde in het venster, wat de magnitudes klein houdt.
    """

    __slots__ = ("period", "_values", "_sum", "_sum_sq", "_offset", "_ops")

    def __init__(self, period: int) -> None:
        self.period = period
        self._values: deque[float] = deque(maxlen=period)
        self._sum = 0.0
        self._sum_sq = 0.0
        self._offset: float | None = None
        self._ops = 0

    def push(self, value: float) -> float | None:
        if self._offset is None:
            self._offset = value
        centred = value - self._offset
        if len(self._values) == self.period:
            old = self._values[0] - self._offset
            self._sum -= old
            self._sum_sq -= old * old
        self._values.append(value)
        self._sum += centred
        self._sum_sq += centred * centred
        self._ops += 1
        if self._ops >= 10_000:
            self._recompute()
        return self.value

    def _recompute(self) -> None:
        self._offset = self._values[0]
        centred = [v - self._offset for v in self._values]
        self._sum = math.fsum(centred)
        self._sum_sq = math.fsum(c * c for c in centred)
        self._ops = 0

    @property
    def value(self) -> float | None:
        n = len(self._values)
        if n < self.period:
            return None
        mean = self._sum / n
        variance = max(0.0, self._sum_sq / n - mean * mean)
        return math.sqrt(variance)

    @property
    def mean(self) -> float | None:
        if len(self._values) < self.period or self._offset is None:
            return None
        return self._sum / self.period + self._offset


class RollingExtreme:
    """Rolling max of min via monotone deque; geamortiseerd O(1).

    De naïeve variant scant het hele venster bij elke update. Voor een
    Donchian-kanaal van 20 op tickfrequentie scheelt dat een factor 20.
    """

    __slots__ = ("period", "_deque", "_index", "_maximum")

    def __init__(self, period: int, maximum: bool = True) -> None:
        self.period = period
        self._maximum = maximum
        self._deque: deque[tuple[int, float]] = deque()
        self._index = 0

    def push(self, value: float) -> float | None:
        while self._deque and (
            self._deque[-1][1] <= value if self._maximum else self._deque[-1][1] >= value
        ):
            self._deque.pop()
        self._deque.append((self._index, value))
        while self._deque[0][0] <= self._index - self.period:
            self._deque.popleft()
        self._index += 1
        return self._deque[0][1] if self._index >= self.period else None

    @property
    def value(self) -> float | None:
        return self._deque[0][1] if self._index >= self.period else None


class IncrementalEMA:
    """EMA met SMA-seeding, identiek aan ``analysis.core.ema``."""

    __slots__ = ("period", "_alpha", "_value", "_seed", "_count")

    def __init__(self, period: int) -> None:
        self.period = period
        self._alpha = 2.0 / (period + 1.0)
        self._value: float | None = None
        self._seed = 0.0
        self._count = 0

    def push(self, value: float) -> float | None:
        if self._value is None:
            self._seed += value
            self._count += 1
            if self._count == self.period:
                self._value = self._seed / self.period
            return self._value
        self._value += (value - self._value) * self._alpha
        return self._value

    @property
    def value(self) -> float | None:
        return self._value


class IncrementalRMA:
    """Wilder-smoothing. Basis voor RSI en ATR."""

    __slots__ = ("period", "_alpha", "_value", "_seed", "_count")

    def __init__(self, period: int) -> None:
        self.period = period
        self._alpha = 1.0 / period
        self._value: float | None = None
        self._seed = 0.0
        self._count = 0

    def push(self, value: float) -> float | None:
        if self._value is None:
            self._seed += value
            self._count += 1
            if self._count == self.period:
                self._value = self._seed / self.period
            return self._value
        self._value += (value - self._value) * self._alpha
        return self._value

    @property
    def value(self) -> float | None:
        return self._value


class IncrementalRSI:
    """RSI(period) volgens Wilder, O(1) per update."""

    __slots__ = ("_gain", "_loss", "_prev")

    def __init__(self, period: int = 14) -> None:
        self._gain = IncrementalRMA(period)
        self._loss = IncrementalRMA(period)
        self._prev: float | None = None

    def push(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        change = close - self._prev
        self._prev = close
        self._gain.push(max(change, 0.0))
        self._loss.push(max(-change, 0.0))
        return self.value

    @property
    def value(self) -> float | None:
        gain, loss = self._gain.value, self._loss.value
        if gain is None or loss is None:
            return None
        if loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)


class IncrementalATR:
    """ATR via true range en Wilder-smoothing."""

    __slots__ = ("_rma", "_prev_close")

    def __init__(self, period: int = 14) -> None:
        self._rma = IncrementalRMA(period)
        self._prev_close: float | None = None

    def push(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        self._rma.push(tr)
        return self._rma.value

    @property
    def value(self) -> float | None:
        return self._rma.value


class IncrementalBollinger:
    """Bollinger Bands met %B, O(1) per update."""

    __slots__ = ("_stdev", "mult")

    def __init__(self, period: int = 20, mult: float = 2.0) -> None:
        self._stdev = RollingStdev(period)
        self.mult = mult

    def push(self, close: float) -> float | None:
        self._stdev.push(close)
        return self.percent_b(close)

    def percent_b(self, close: float) -> float | None:
        sd, mean = self._stdev.value, self._stdev.mean
        if sd is None or mean is None:
            return None
        upper, lower = mean + self.mult * sd, mean - self.mult * sd
        span = upper - lower
        return 0.5 if span == 0 else (close - lower) / span

    @property
    def middle(self) -> float | None:
        return self._stdev.mean


class IncrementalLinReg:
    """Helling en R^2 over een schuivend venster.

    Omdat de x-waarden altijd 0..n-1 zijn, zijn ``sum(x)`` en ``sum(x^2)``
    constant. Alleen ``sum(y)`` en ``sum(x*y)`` hoeven bijgehouden te worden,
    en die laatste kan incrementeel via de verschuivingsidentiteit:
    als het venster één positie opschuift, geldt
    ``sum(xy)_nieuw = sum(xy)_oud - (sum(y)_oud - y_uit) + (n-1)*y_in``.
    """

    __slots__ = ("period", "_values", "_sum_y", "_sum_xy", "_sum_yy", "_ops")

    def __init__(self, period: int) -> None:
        self.period = period
        self._values: deque[float] = deque(maxlen=period)
        self._sum_y = 0.0
        self._sum_xy = 0.0
        self._ops = 0

    def push(self, value: float) -> None:
        n = self.period
        if len(self._values) == n:
            y_out = self._values[0]
            self._sum_xy = self._sum_xy - (self._sum_y - y_out) + (n - 1) * value
            self._sum_y = self._sum_y - y_out + value
        else:
            self._sum_xy += len(self._values) * value
            self._sum_y += value
        self._values.append(value)
        self._ops += 1
        if self._ops >= 10_000:
            self._recompute()

    def _recompute(self) -> None:
        self._sum_y = math.fsum(self._values)
        self._sum_xy = math.fsum(i * v for i, v in enumerate(self._values))
        self._ops = 0

    def result(self) -> tuple[float | None, float | None]:
        """Geeft (helling in % per candle, R^2)."""
        n = len(self._values)
        if n < self.period or n < 2:
            return None, None
        mean_x = (n - 1) / 2.0
        mean_y = self._sum_y / n
        sxx = n * (n * n - 1) / 12.0
        sxy = self._sum_xy - n * mean_x * mean_y
        if sxx == 0:
            return None, None
        slope = sxy / sxx
        syy = math.fsum((v - mean_y) ** 2 for v in self._values)
        r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
        normalised = (slope / abs(mean_y) * 100.0) if mean_y else 0.0
        return normalised, r2


@dataclass(slots=True)
class StreamState:
    """Alle indicatortoestand voor één symbool op één tijdsframe.

    Eén object per tijdsframe, dat per afgesloten candle wordt bijgewerkt.
    Vervangt het herberekenen van het volledige venster.
    """

    ema_fast: IncrementalEMA = field(default_factory=lambda: IncrementalEMA(9))
    ema_slow: IncrementalEMA = field(default_factory=lambda: IncrementalEMA(21))
    rsi: IncrementalRSI = field(default_factory=lambda: IncrementalRSI(7))
    atr: IncrementalATR = field(default_factory=lambda: IncrementalATR(14))
    bollinger: IncrementalBollinger = field(default_factory=lambda: IncrementalBollinger(20, 2.0))
    linreg: IncrementalLinReg = field(default_factory=lambda: IncrementalLinReg(15))
    atr_history: deque[float] = field(default_factory=lambda: deque(maxlen=60))
    bars: int = 0

    def push_candle(self, o: float, h: float, l: float, c: float, v: float = 0.0) -> None:
        self.ema_fast.push(c)
        self.ema_slow.push(c)
        self.rsi.push(c)
        atr = self.atr.push(h, l, c)
        self.bollinger.push(c)
        self.linreg.push(c)
        if atr is not None:
            self.atr_history.append(atr)
        self.bars += 1

    @property
    def ready(self) -> bool:
        """Genoeg historie voor een betrouwbaar oordeel."""
        return self.bars >= 60 and self.atr.value is not None

    def atr_ratio(self) -> float | None:
        """Huidige ATR ten opzichte van de mediaan van de laatste 60."""
        if not self.atr_history or self.atr.value is None:
            return None
        ordered = sorted(self.atr_history)
        median = ordered[len(ordered) // 2]
        return self.atr.value / median if median else None

    def warm_up(self, candles) -> None:
        """Vul de toestand met historie na een herstart.

        Zonder dit begint elke herstart met een blinde periode van 60 candles.
        Bij M1 is dat een uur waarin de bot niets doet.
        """
        for i in range(len(candles)):
            self.push_candle(
                candles.open[i], candles.high[i], candles.low[i],
                candles.close[i], candles.volume[i],
            )
