"""Volatiliteitsindicatoren.

Volatiliteit heeft zelf nauwelijks richting. Deze module levert daarom vooral
*context*: is dit een goed moment om überhaupt op signalen te handelen, en hoe
groot is het risico. Alleen mean-reversion aan de banden krijgt een echte
richtingsscore, en die wordt door de engine gedempt in trendmarkten.
"""

from __future__ import annotations

import math

from .core import (
    Series,
    clamp,
    ema,
    highest,
    lowest,
    rma,
    safe_div,
    sma,
    stdev,
    true_range,
)
from .signals import CATEGORY_VOLATILITY, Candles, Signal


def bollinger(
    close: list[float], period: int = 20, mult: float = 2.0
) -> tuple[Series, Series, Series, Series, Series]:
    """Geeft (upper, middle, lower, %B, bandwidth)."""
    middle = sma(close, period)
    sd = stdev(close, period)
    upper: Series = [None] * len(close)
    lower: Series = [None] * len(close)
    percent_b: Series = [None] * len(close)
    bandwidth: Series = [None] * len(close)
    for i in range(len(close)):
        if middle[i] is None or sd[i] is None:
            continue
        upper[i] = middle[i] + mult * sd[i]
        lower[i] = middle[i] - mult * sd[i]
        rng = upper[i] - lower[i]
        percent_b[i] = 0.5 if rng == 0 else (close[i] - lower[i]) / rng
        bandwidth[i] = safe_div(rng, middle[i]) * 100.0
    return upper, middle, lower, percent_b, bandwidth


def atr(candles: Candles, period: int = 14) -> Series:
    return rma(true_range(candles.high, candles.low, candles.close), period)


def keltner(
    candles: Candles, period: int = 20, mult: float = 2.0
) -> tuple[Series, Series, Series]:
    middle = ema(candles.close, period)
    a = atr(candles, period)
    upper: Series = [
        None if (m is None or v is None) else m + mult * v for m, v in zip(middle, a)
    ]
    lower: Series = [
        None if (m is None or v is None) else m - mult * v for m, v in zip(middle, a)
    ]
    return upper, middle, lower


def donchian(candles: Candles, period: int = 20) -> tuple[Series, Series, Series]:
    upper = highest(candles.high, period)
    lower = lowest(candles.low, period)
    middle: Series = [
        None if (u is None or l is None) else (u + l) / 2.0 for u, l in zip(upper, lower)
    ]
    return upper, middle, lower


def historical_volatility(close: list[float], period: int = 30, periods_per_year: int = 365) -> Series:
    """Geannualiseerde volatiliteit op log-returns."""
    n = len(close)
    log_ret: Series = [None] * n
    for i in range(1, n):
        if close[i - 1] <= 0 or close[i] <= 0:
            continue
        log_ret[i] = math.log(close[i] / close[i - 1])
    sd = stdev(log_ret, period)
    return [None if v is None else v * math.sqrt(periods_per_year) * 100.0 for v in sd]


def squeeze(candles: Candles, period: int = 20) -> list[bool | None]:
    """TTM-squeeze: Bollinger Bands volledig binnen de Keltner Channels.

    Een squeeze zegt niets over richting, alleen dat er een uitbraak aan zit
    te komen. Handelen op richting tijdens een squeeze is meestal een slecht
    idee, en de engine verlaagt de confidence dan ook.
    """
    upper_bb, _, lower_bb, _, _ = bollinger(candles.close, period)
    upper_kc, _, lower_kc = keltner(candles, period)
    out: list[bool | None] = [None] * len(candles)
    for i in range(len(candles)):
        if None in (upper_bb[i], lower_bb[i], upper_kc[i], lower_kc[i]):
            continue
        out[i] = upper_bb[i] < upper_kc[i] and lower_bb[i] > lower_kc[i]
    return out


def evaluate(candles: Candles, cfg: dict) -> list[Signal]:
    close = candles.close
    price = close[-1]
    out: list[Signal] = []

    # --- Bollinger ------------------------------------------------------------
    bb_period = cfg.get("bb_period", 20)
    upper, middle, lower, pct_b, bandwidth = bollinger(close, bb_period, cfg.get("bb_mult", 2.0))
    if pct_b[-1] is not None:
        b = pct_b[-1]
        if b > 1.0:
            note = "Koers breekt boven de bovenste band uit"
        elif b < 0.0:
            note = "Koers zakt onder de onderste band"
        else:
            note = f"Koers zit op {b * 100:.0f}% van de bandbreedte"
        out.append(
            Signal(
                key="bollinger",
                category=CATEGORY_VOLATILITY,
                label=f"Bollinger %B ({bb_period})",
                value=round(b, 3),
                score=clamp((0.5 - b) * 2.0),
                weight=1.0,
                rationale=note,
                extra={
                    "upper": round(upper[-1], 6),
                    "middle": round(middle[-1], 6),
                    "lower": round(lower[-1], 6),
                },
            )
        )

    if bandwidth[-1] is not None:
        hist = [v for v in bandwidth[-120:] if v is not None]
        percentile = (
            sum(1 for v in hist if v < bandwidth[-1]) / len(hist) * 100.0 if hist else 50.0
        )
        out.append(
            Signal(
                key="bb_bandwidth",
                category=CATEGORY_VOLATILITY,
                label="Bollinger-bandbreedte",
                value=round(bandwidth[-1], 2),
                score=0.0,
                weight=0.0,  # puur context, geen richting
                rationale=(
                    f"Bandbreedte {bandwidth[-1]:.2f}%, dat is het "
                    f"{percentile:.0f}e percentiel van de laatste 120 candles"
                ),
                extra={"percentile": round(percentile, 1)},
            )
        )

    # --- ATR ------------------------------------------------------------------
    a = atr(candles, cfg.get("atr_period", 14))
    if a[-1] is not None:
        atr_pct = safe_div(a[-1], price) * 100.0
        out.append(
            Signal(
                key="atr",
                category=CATEGORY_VOLATILITY,
                label="ATR 14",
                value=round(a[-1], 6),
                score=0.0,
                weight=0.0,
                rationale=(
                    f"Gemiddelde candle-range is {atr_pct:.2f}% van de koers; "
                    f"een stop van minder dan {atr_pct * 1.5:.2f}% wordt statistisch "
                    "gezien snel geraakt door ruis"
                ),
                extra={"atr_pct": round(atr_pct, 3)},
            )
        )

    # --- Keltner --------------------------------------------------------------
    k_upper, k_mid, k_lower = keltner(candles)
    if k_upper[-1] is not None and k_lower[-1] is not None:
        rng = k_upper[-1] - k_lower[-1]
        pos = 0.5 if rng == 0 else (price - k_lower[-1]) / rng
        out.append(
            Signal(
                key="keltner",
                category=CATEGORY_VOLATILITY,
                label="Keltner Channel",
                value=round(pos, 3),
                score=clamp((0.5 - pos) * 1.6),
                weight=0.6,
                rationale=f"Koers op {pos * 100:.0f}% van het Keltner-kanaal",
            )
        )

    # --- Donchian -------------------------------------------------------------
    d_upper, d_mid, d_lower = donchian(candles, cfg.get("donchian_period", 20))
    if d_upper[-1] is not None and d_lower[-1] is not None:
        breakout_up = price >= d_upper[-1]
        breakout_down = price <= d_lower[-1]
        score = 0.9 if breakout_up else -0.9 if breakout_down else 0.0
        note = (
            "Uitbraak boven de 20-candle high" if breakout_up
            else "Doorbraak onder de 20-candle low" if breakout_down
            else "Koers binnen het Donchian-kanaal"
        )
        out.append(
            Signal(
                key="donchian",
                category=CATEGORY_VOLATILITY,
                label="Donchian-uitbraak",
                value=round(d_upper[-1], 6),
                score=score,
                weight=0.9,
                rationale=note,
                extra={"upper": round(d_upper[-1], 6), "lower": round(d_lower[-1], 6)},
            )
        )

    # --- Historische volatiliteit ---------------------------------------------
    hv = historical_volatility(close, cfg.get("hv_period", 30), cfg.get("periods_per_year", 365))
    if hv[-1] is not None:
        out.append(
            Signal(
                key="historical_volatility",
                category=CATEGORY_VOLATILITY,
                label="Historische volatiliteit",
                value=round(hv[-1], 1),
                score=0.0,
                weight=0.0,
                rationale=f"Geannualiseerde volatiliteit rond de {hv[-1]:.0f}%",
            )
        )

    # --- Squeeze --------------------------------------------------------------
    sq = squeeze(candles)
    if sq[-1] is not None:
        released = len(sq) > 1 and sq[-2] is True and sq[-1] is False
        out.append(
            Signal(
                key="squeeze",
                category=CATEGORY_VOLATILITY,
                label="TTM Squeeze",
                value="actief" if sq[-1] else ("vrijgelaten" if released else "geen"),
                score=0.0,
                weight=0.0,
                rationale=(
                    "Volatiliteit is samengeknepen; een uitbraak komt eraan maar de "
                    "richting is nog onbekend"
                    if sq[-1]
                    else "Squeeze is zojuist losgelaten; de uitbraak is begonnen"
                    if released
                    else "Geen squeeze actief"
                ),
                extra={"active": sq[-1], "released": released},
            )
        )

    return out
