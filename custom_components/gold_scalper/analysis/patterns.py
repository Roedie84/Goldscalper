"""Candlestickpatronen.

Realistische verwachting: losse candlestickpatronen hebben in de meeste
studies een nauwelijks van toeval te onderscheiden edge. Ze zijn hier
opgenomen omdat je erom vroeg en omdat ze als *context* bij een ander signaal
wel iets toevoegen, maar hun gewicht is bewust laag. Een patroon telt alleen
mee als het op een relevante plek in de structuur valt — zie ``_location_bonus``.
"""

from __future__ import annotations

from .core import safe_div
from .signals import CATEGORY_PATTERN, Candles, Signal
from .volatility import atr


def _body(candles: Candles, i: int) -> float:
    return abs(candles.close[i] - candles.open[i])


def _range(candles: Candles, i: int) -> float:
    return candles.high[i] - candles.low[i]


def _upper_wick(candles: Candles, i: int) -> float:
    return candles.high[i] - max(candles.open[i], candles.close[i])


def _lower_wick(candles: Candles, i: int) -> float:
    return min(candles.open[i], candles.close[i]) - candles.low[i]


def _bullish(candles: Candles, i: int) -> bool:
    return candles.close[i] > candles.open[i]


def _location_bonus(candles: Candles, direction: int, lookback: int = 20) -> float:
    """Schaal een patroon op basis van waar het in de recente range optreedt.

    Een hammer halverwege een range betekent niets. Dezelfde hammer op een
    20-candle low is een ander verhaal. Deze factor loopt van 0.3 tot 1.0.
    """
    window_high = max(candles.high[-lookback:])
    window_low = min(candles.low[-lookback:])
    rng = window_high - window_low
    if rng == 0:
        return 0.3
    position = (candles.close[-1] - window_low) / rng
    if direction > 0:
        return 0.3 + 0.7 * (1.0 - position)
    return 0.3 + 0.7 * position


def detect(candles: Candles) -> list[tuple[str, str, int, float]]:
    """Geeft (key, label, richting, ruwe sterkte 0-1) voor de laatste candle."""
    n = len(candles)
    if n < 5:
        return []
    found: list[tuple[str, str, int, float]] = []
    i = n - 1
    a = atr(candles, 14)
    avg_range = a[-1] if a[-1] else (sum(_range(candles, j) for j in range(n - 14, n)) / 14)
    if not avg_range:
        return []

    body = _body(candles, i)
    rng = _range(candles, i)
    upper, lower = _upper_wick(candles, i), _lower_wick(candles, i)
    prev_body = _body(candles, i - 1)

    # Doji
    if rng > 0 and body / rng < 0.1:
        found.append(("doji", "Doji", 0, 0.4))

    # Marubozu
    if rng > 0 and body / rng > 0.9 and body > avg_range * 0.8:
        found.append(
            ("marubozu", "Marubozu", 1 if _bullish(candles, i) else -1, 0.6)
        )

    # Hammer / Hanging man
    if rng > 0 and lower > body * 2 and upper < body * 0.6 and body / rng < 0.35:
        found.append(("hammer", "Hammer", 1, 0.6))

    # Inverted hammer / Shooting star
    if rng > 0 and upper > body * 2 and lower < body * 0.6 and body / rng < 0.35:
        found.append(("shooting_star", "Shooting star", -1, 0.6))

    # Engulfing
    if prev_body > 0 and body > prev_body * 1.1:
        prev_bull = _bullish(candles, i - 1)
        cur_bull = _bullish(candles, i)
        if cur_bull != prev_bull:
            engulfs = (
                max(candles.open[i], candles.close[i]) >= max(candles.open[i - 1], candles.close[i - 1])
                and min(candles.open[i], candles.close[i]) <= min(candles.open[i - 1], candles.close[i - 1])
            )
            if engulfs:
                found.append(
                    (
                        "engulfing",
                        "Bullish engulfing" if cur_bull else "Bearish engulfing",
                        1 if cur_bull else -1,
                        0.75,
                    )
                )

    # Piercing line / Dark cloud cover
    if prev_body > avg_range * 0.5:
        midpoint = (candles.open[i - 1] + candles.close[i - 1]) / 2
        if (
            not _bullish(candles, i - 1)
            and _bullish(candles, i)
            and candles.open[i] < candles.low[i - 1]
            and candles.close[i] > midpoint
        ):
            found.append(("piercing", "Piercing line", 1, 0.65))
        if (
            _bullish(candles, i - 1)
            and not _bullish(candles, i)
            and candles.open[i] > candles.high[i - 1]
            and candles.close[i] < midpoint
        ):
            found.append(("dark_cloud", "Dark cloud cover", -1, 0.65))

    # Morning / Evening star
    first_body = _body(candles, i - 2)
    mid_body = _body(candles, i - 1)
    if first_body > avg_range * 0.6 and mid_body < first_body * 0.4 and body > avg_range * 0.5:
        if (
            not _bullish(candles, i - 2)
            and _bullish(candles, i)
            and candles.close[i] > (candles.open[i - 2] + candles.close[i - 2]) / 2
        ):
            found.append(("morning_star", "Morning star", 1, 0.8))
        if (
            _bullish(candles, i - 2)
            and not _bullish(candles, i)
            and candles.close[i] < (candles.open[i - 2] + candles.close[i - 2]) / 2
        ):
            found.append(("evening_star", "Evening star", -1, 0.8))

    # Three white soldiers / Three black crows
    if all(_bullish(candles, j) for j in (i - 2, i - 1, i)) and all(
        candles.close[j] > candles.close[j - 1] for j in (i - 1, i)
    ):
        found.append(("three_soldiers", "Three white soldiers", 1, 0.7))
    if all(not _bullish(candles, j) for j in (i - 2, i - 1, i)) and all(
        candles.close[j] < candles.close[j - 1] for j in (i - 1, i)
    ):
        found.append(("three_crows", "Three black crows", -1, 0.7))

    # Inside bar / outside bar
    if candles.high[i] < candles.high[i - 1] and candles.low[i] > candles.low[i - 1]:
        found.append(("inside_bar", "Inside bar", 0, 0.3))

    return found


def evaluate(candles: Candles, cfg: dict) -> list[Signal]:
    out: list[Signal] = []
    for key, label, direction, strength in detect(candles):
        if direction == 0:
            out.append(
                Signal(
                    key=f"pattern_{key}",
                    category=CATEGORY_PATTERN,
                    label=label,
                    value=label,
                    score=0.0,
                    weight=0.0,
                    rationale=f"{label} herkend: besluiteloosheid, geen richting",
                )
            )
            continue
        bonus = _location_bonus(candles, direction)
        out.append(
            Signal(
                key=f"pattern_{key}",
                category=CATEGORY_PATTERN,
                label=label,
                value=label,
                score=direction * strength * bonus,
                weight=cfg.get("pattern_weight", 0.5),
                rationale=(
                    f"{label} herkend op een "
                    f"{'gunstige' if bonus > 0.7 else 'middelmatige' if bonus > 0.45 else 'ongunstige'} "
                    f"plek in de recente range (factor {bonus:.2f})"
                ),
            )
        )
    return out
