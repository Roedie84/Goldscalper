"""Pure-Python numeriek fundament voor de indicatoren.

Bewust geen numpy/pandas/ta-lib: Home Assistant custom components moeten licht
blijven en ta-lib vereist een C-build die op veel HA-installaties (met name
HA OS / Docker op ARM) niet beschikbaar is.

Conventie: elke functie geeft een lijst terug met dezelfde lengte als de input.
Posities die nog in de warmup-periode vallen bevatten ``None``. Dat maakt het
mogelijk om series onderling uit te lijnen op index, wat nodig is voor
crossover- en divergentiedetectie.
"""

from __future__ import annotations

import math
from typing import Sequence

Series = list[float | None]


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Deling die niet ontploft op nul. Crypto-volume kan echt 0 zijn."""
    if denominator == 0 or denominator is None:
        return default
    return numerator / denominator


def _clean(values: Sequence[float | None]) -> list[float]:
    return [v for v in values if v is not None]


def _first_valid(values: Sequence[float | None]) -> int | None:
    """Index van de eerste niet-None waarde, of None als de reeks leeg is.

    Nodig omdat afgeleide reeksen (DX voor de ADX, momentum voor de TSI) met
    een blok ``None`` beginnen. Seeden op de letterlijke eerste ``period``
    posities levert dan een volledig lege uitkomst op.
    """
    for i, v in enumerate(values):
        if v is not None:
            return i
    return None


def sma(values: Sequence[float | None], period: int) -> Series:
    """Simple moving average."""
    out: Series = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    window: list[float] = []
    for i, v in enumerate(values):
        if v is None:
            window.clear()
            running = 0.0
            continue
        window.append(v)
        running += v
        if len(window) > period:
            running -= window.pop(0)
        if len(window) == period:
            out[i] = running / period
    return out


def ema(values: Sequence[float | None], period: int) -> Series:
    """Exponential moving average, geseed met een SMA over de eerste periode."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    start = _first_valid(values)
    if start is None or start + period > len(values):
        return out
    prev = sum(values[start : start + period]) / period
    out[start + period - 1] = prev
    for i in range(start + period, len(values)):
        v = values[i]
        if v is None:
            out[i] = prev
            continue
        prev = (v - prev) * alpha + prev
        out[i] = prev
    return out


def rma(values: Sequence[float | None], period: int) -> Series:
    """Wilder's smoothing. Wordt gebruikt door RSI, ATR, ADX.

    Let op: dit is *niet* hetzelfde als een EMA met dezelfde periode. Een veel
    gemaakte fout is RSI met een EMA(14) berekenen; dat geeft structureel
    andere waarden dan wat TradingView en de meeste exchanges tonen.
    """
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    alpha = 1.0 / period
    start = _first_valid(values)
    if start is None or start + period > len(values):
        return out
    prev = sum(values[start : start + period]) / period
    out[start + period - 1] = prev
    for i in range(start + period, len(values)):
        v = values[i]
        if v is None:
            out[i] = prev
            continue
        prev = (v - prev) * alpha + prev
        out[i] = prev
    return out


def wma(values: Sequence[float | None], period: int) -> Series:
    """Weighted moving average met lineair oplopende gewichten."""
    out: Series = [None] * len(values)
    denom = period * (period + 1) / 2.0
    for i in range(len(values)):
        if i + 1 < period:
            continue
        window = values[i - period + 1 : i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(v * (j + 1) for j, v in enumerate(window)) / denom
    return out


def hma(values: Sequence[float | None], period: int) -> Series:
    """Hull moving average: sneller en gladder dan een EMA."""
    half = max(1, period // 2)
    sqrt_p = max(1, int(math.sqrt(period)))
    wma_half = wma(values, half)
    wma_full = wma(values, period)
    raw: Series = [
        None if (a is None or b is None) else 2 * a - b
        for a, b in zip(wma_half, wma_full)
    ]
    return wma(raw, sqrt_p)


def dema(values: Sequence[float | None], period: int) -> Series:
    """Double EMA."""
    e1 = ema(values, period)
    e2 = ema(e1, period)
    return [None if (a is None or b is None) else 2 * a - b for a, b in zip(e1, e2)]


def tema(values: Sequence[float | None], period: int) -> Series:
    """Triple EMA."""
    e1 = ema(values, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    return [
        None if (a is None or b is None or c is None) else 3 * a - 3 * b + c
        for a, b, c in zip(e1, e2, e3)
    ]


def kama(values: Sequence[float | None], period: int = 10, fast: int = 2, slow: int = 30) -> Series:
    """Kaufman Adaptive MA: versnelt in trends, vertraagt in ruis."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    prev = values[period]
    if prev is None:
        return out
    out[period] = prev
    for i in range(period + 1, len(values)):
        window = values[i - period : i + 1]
        if any(v is None for v in window):
            out[i] = prev
            continue
        change = abs(window[-1] - window[0])
        volatility = sum(abs(window[j] - window[j - 1]) for j in range(1, len(window)))
        er = safe_div(change, volatility)
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        prev = prev + sc * (values[i] - prev)
        out[i] = prev
    return out


def stdev(values: Sequence[float | None], period: int) -> Series:
    """Rollende populatie-standaarddeviatie."""
    out: Series = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < period:
            continue
        window = values[i - period + 1 : i + 1]
        if any(v is None for v in window):
            continue
        mean = sum(window) / period
        variance = sum((v - mean) ** 2 for v in window) / period
        out[i] = math.sqrt(variance)
    return out


def roc(values: Sequence[float | None], period: int) -> Series:
    """Rate of change in procenten."""
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        prev, cur = values[i - period], values[i]
        if prev in (None, 0) or cur is None:
            continue
        out[i] = (cur - prev) / prev * 100.0
    return out


def diff(values: Sequence[float | None]) -> Series:
    """Verschil met de vorige waarde."""
    out: Series = [None] * len(values)
    for i in range(1, len(values)):
        if values[i] is None or values[i - 1] is None:
            continue
        out[i] = values[i] - values[i - 1]
    return out


def highest(values: Sequence[float | None], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < period:
            continue
        window = _clean(values[i - period + 1 : i + 1])
        if len(window) == period:
            out[i] = max(window)
    return out


def lowest(values: Sequence[float | None], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < period:
            continue
        window = _clean(values[i - period + 1 : i + 1])
        if len(window) == period:
            out[i] = min(window)
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> Series:
    """True range: houdt rekening met gaps tussen candles."""
    out: Series = [None] * len(close)
    if not close:
        return out
    out[0] = high[0] - low[0]
    for i in range(1, len(close)):
        out[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return out


def crossover(a: Sequence[float | None], b: Sequence[float | None], index: int = -1) -> bool:
    """True als a op ``index`` b van onderaf kruist."""
    i = index if index >= 0 else len(a) + index
    if i < 1 or i >= len(a) or i >= len(b):
        return False
    if None in (a[i], b[i], a[i - 1], b[i - 1]):
        return False
    return a[i - 1] <= b[i - 1] and a[i] > b[i]


def crossunder(a: Sequence[float | None], b: Sequence[float | None], index: int = -1) -> bool:
    """True als a op ``index`` b van bovenaf kruist."""
    i = index if index >= 0 else len(a) + index
    if i < 1 or i >= len(a) or i >= len(b):
        return False
    if None in (a[i], b[i], a[i - 1], b[i - 1]):
        return False
    return a[i - 1] >= b[i - 1] and a[i] < b[i]


def linreg_slope(values: Sequence[float | None], period: int) -> tuple[float | None, float | None]:
    """Helling en R^2 van een kleinste-kwadraten fit over de laatste ``period``.

    De helling wordt genormaliseerd naar procent per candle, zodat hij
    vergelijkbaar is tussen coins met totaal verschillende prijsniveaus.
    """
    window = _clean(values[-period:])
    if len(window) < period or len(window) < 2:
        return None, None
    n = len(window)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(window) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window))
    if sxx == 0:
        return None, None
    slope = sxy / sxx
    syy = sum((y - mean_y) ** 2 for y in window)
    r2 = safe_div(sxy**2, sxx * syy) if syy > 0 else 0.0
    normalised = safe_div(slope, abs(mean_y)) * 100.0
    return normalised, r2


def zscore(values: Sequence[float | None], period: int) -> float | None:
    """Hoeveel standaarddeviaties de laatste waarde van het gemiddelde af zit."""
    window = _clean(values[-period:])
    if len(window) < period:
        return None
    mean = sum(window) / len(window)
    variance = sum((v - mean) ** 2 for v in window) / len(window)
    sd = math.sqrt(variance)
    if sd == 0:
        return 0.0
    return (window[-1] - mean) / sd


def hurst_exponent(values: Sequence[float | None], max_lag: int = 20) -> float | None:
    """Ruwe Hurst-schatting via rescaled range op verschillende lags.

    >0.5 duidt op trendgedrag, <0.5 op mean reversion, ~0.5 op een random walk.
    Dit is een indicatie, geen wetenschap: op korte reeksen is de schatting
    behoorlijk instabiel.
    """
    series = _clean(values)
    if len(series) < max_lag * 4:
        return None
    lags = range(2, max_lag)
    tau = []
    valid_lags = []
    for lag in lags:
        deltas = [series[i + lag] - series[i] for i in range(len(series) - lag)]
        if not deltas:
            continue
        mean = sum(deltas) / len(deltas)
        var = sum((d - mean) ** 2 for d in deltas) / len(deltas)
        sd = math.sqrt(var)
        if sd <= 0:
            continue
        tau.append(math.log(sd))
        valid_lags.append(math.log(lag))
    if len(tau) < 3:
        return None
    n = len(tau)
    mean_x = sum(valid_lags) / n
    mean_y = sum(tau) / n
    sxx = sum((x - mean_x) ** 2 for x in valid_lags)
    if sxx == 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(valid_lags, tau))
    return sxy / sxx


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def scale(value: float, low: float, high: float) -> float:
    """Map een waarde lineair van [low, high] naar [-1, 1], geclamped."""
    if high == low:
        return 0.0
    return clamp((value - low) / (high - low) * 2.0 - 1.0)
