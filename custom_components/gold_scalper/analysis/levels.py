"""Steun- en weerstandsniveaus.

Deze module levert vooral *bruikbare getallen* voor je dashboard: waar liggen
de niveaus, hoe ver is de koers ervan af, en wat zou een ATR-gebaseerde stop
zijn. Slechts een klein deel wordt vertaald naar een richtingsscore.
"""

from __future__ import annotations

from .core import safe_div
from .signals import CATEGORY_STATISTICAL, Candles, Signal
from .volatility import atr


def classic_pivots(high: float, low: float, close: float) -> dict[str, float]:
    pivot = (high + low + close) / 3.0
    return {
        "pivot": pivot,
        "r1": 2 * pivot - low,
        "r2": pivot + (high - low),
        "r3": high + 2 * (pivot - low),
        "s1": 2 * pivot - high,
        "s2": pivot - (high - low),
        "s3": low - 2 * (high - pivot),
    }


def fibonacci_pivots(high: float, low: float, close: float) -> dict[str, float]:
    pivot = (high + low + close) / 3.0
    rng = high - low
    return {
        "pivot": pivot,
        "r1": pivot + 0.382 * rng,
        "r2": pivot + 0.618 * rng,
        "r3": pivot + 1.000 * rng,
        "s1": pivot - 0.382 * rng,
        "s2": pivot - 0.618 * rng,
        "s3": pivot - 1.000 * rng,
    }


def fibonacci_retracements(swing_high: float, swing_low: float) -> dict[str, float]:
    rng = swing_high - swing_low
    return {
        f"fib_{int(level * 1000)}": swing_high - level * rng
        for level in (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
    }


def swing_levels(
    candles: Candles, pivot: int = 5, lookback: int = 200, tolerance_pct: float = 0.6
) -> tuple[list[dict], list[dict]]:
    """Vind swing highs/lows en cluster ze tot niveaus.

    Een niveau dat drie keer is geraakt is interessanter dan een dat één keer
    is geraakt; ``touches`` maakt dat expliciet zodat je er in je dashboard op
    kunt filteren.
    """
    n = len(candles)
    start = max(pivot, n - lookback)
    raw_highs: list[float] = []
    raw_lows: list[float] = []
    for i in range(start, n - pivot):
        window_h = candles.high[i - pivot : i + pivot + 1]
        window_l = candles.low[i - pivot : i + pivot + 1]
        if candles.high[i] == max(window_h):
            raw_highs.append(candles.high[i])
        if candles.low[i] == min(window_l):
            raw_lows.append(candles.low[i])

    def _cluster(points: list[float]) -> list[dict]:
        clusters: list[list[float]] = []
        for p in sorted(points):
            if clusters and abs(p - clusters[-1][-1]) / max(p, 1e-12) * 100 <= tolerance_pct:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [
            {"level": sum(c) / len(c), "touches": len(c)}
            for c in clusters
        ]

    return _cluster(raw_highs), _cluster(raw_lows)


def nearest_levels(candles: Candles) -> dict:
    """Dichtstbijzijnde steun boven/onder de koers, plus ATR-stopsuggestie."""
    price = candles.close[-1]
    resistances, supports = swing_levels(candles)
    above = sorted(
        (r for r in resistances if r["level"] > price), key=lambda r: r["level"]
    )
    below = sorted(
        (s for s in supports if s["level"] < price), key=lambda s: s["level"], reverse=True
    )
    a = atr(candles, 14)
    atr_val = a[-1] or 0.0

    result = {
        "price": price,
        "atr": atr_val,
        "stop_suggestion_long": price - 2.0 * atr_val if atr_val else None,
        "stop_suggestion_short": price + 2.0 * atr_val if atr_val else None,
        "resistance": above[0] if above else None,
        "support": below[0] if below else None,
        "all_resistance": above[:4],
        "all_support": below[:4],
    }

    if above and below:
        span = above[0]["level"] - below[0]["level"]
        result["range_position"] = (
            safe_div(price - below[0]["level"], span) if span > 0 else 0.5
        )
        risk = price - below[0]["level"]
        reward = above[0]["level"] - price
        result["risk_reward_long"] = round(safe_div(reward, risk), 2) if risk > 0 else None
    return result


def evaluate(candles: Candles, cfg: dict) -> list[Signal]:
    out: list[Signal] = []
    info = nearest_levels(candles)
    price = info["price"]

    if info.get("range_position") is not None:
        pos = info["range_position"]
        rr = info.get("risk_reward_long")
        out.append(
            Signal(
                key="range_position",
                category=CATEGORY_STATISTICAL,
                label="Positie in de range",
                value=round(pos, 3),
                score=(0.5 - pos) * 1.2,
                weight=0.7,
                rationale=(
                    f"Koers zit op {pos * 100:.0f}% tussen steun "
                    f"({info['support']['level']:.6g}) en weerstand "
                    f"({info['resistance']['level']:.6g})"
                    + (f"; risk/reward voor long is {rr}:1" if rr else "")
                ),
                extra={
                    "support": round(info["support"]["level"], 8),
                    "resistance": round(info["resistance"]["level"], 8),
                    "risk_reward_long": rr,
                },
            )
        )

    if info["atr"]:
        out.append(
            Signal(
                key="stop_levels",
                category=CATEGORY_STATISTICAL,
                label="ATR-stopniveaus",
                value=round(info["stop_suggestion_long"], 8),
                score=0.0,
                weight=0.0,
                rationale=(
                    f"Een stop op 2×ATR ligt voor long op {info['stop_suggestion_long']:.6g} "
                    f"({safe_div(2 * info['atr'], price) * 100:.2f}% onder de koers)"
                ),
                extra={
                    "long": round(info["stop_suggestion_long"], 8),
                    "short": round(info["stop_suggestion_short"], 8),
                },
            )
        )

    # Daily pivots op basis van de laatste voltooide candle
    if len(candles) >= 2:
        piv = classic_pivots(candles.high[-2], candles.low[-2], candles.close[-2])
        dist = safe_div(price - piv["pivot"], piv["pivot"]) * 100.0
        out.append(
            Signal(
                key="pivot",
                category=CATEGORY_STATISTICAL,
                label="Pivot point",
                value=round(piv["pivot"], 8),
                score=max(-0.5, min(0.5, dist / 6.0)),
                weight=0.5,
                rationale=f"Koers staat {dist:+.2f}% t.o.v. het pivotpunt",
                extra={k: round(v, 8) for k, v in piv.items()},
            )
        )

    return out
