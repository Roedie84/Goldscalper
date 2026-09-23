"""Statistische maten.

Deze module bevat de indicatoren die het minst 'technische analyse' zijn en
het meest gewoon statistiek. Ze zijn nuttig als tegenwicht: als de Hurst-
exponent onder 0.5 zit is de markt mean-reverting en zijn trendindicatoren
minder betrouwbaar, ongeacht hoe overtuigend hun signaal eruitziet.
"""

from __future__ import annotations

import math

from .core import clamp, hurst_exponent, linreg_slope, safe_div, stdev, zscore
from .signals import CATEGORY_STATISTICAL, Candles, Signal


def max_drawdown(close: list[float], lookback: int = 90) -> tuple[float, float]:
    """Geeft (max drawdown %, huidige drawdown vanaf de piek %)."""
    window = close[-lookback:]
    if not window:
        return 0.0, 0.0
    peak = window[0]
    max_dd = 0.0
    for price in window:
        peak = max(peak, price)
        dd = safe_div(peak - price, peak) * 100.0
        max_dd = max(max_dd, dd)
    current_peak = max(window)
    current_dd = safe_div(current_peak - window[-1], current_peak) * 100.0
    return max_dd, current_dd


def sharpe_like(close: list[float], lookback: int = 90, periods_per_year: int = 365) -> float | None:
    """Return/risico-verhouding op basis van log-returns, zonder risicovrije voet.

    Geen echte Sharpe ratio dus. Bruikbaar als relatieve maat tussen coins,
    niet als absoluut kwaliteitscijfer.
    """
    window = close[-lookback:]
    if len(window) < 20:
        return None
    returns = [
        math.log(window[i] / window[i - 1])
        for i in range(1, len(window))
        if window[i] > 0 and window[i - 1] > 0
    ]
    if len(returns) < 10:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    sd = math.sqrt(variance)
    if sd == 0:
        return None
    return (mean / sd) * math.sqrt(periods_per_year)


def evaluate(candles: Candles, cfg: dict) -> list[Signal]:
    close = candles.close
    out: list[Signal] = []

    # --- Z-score van de koers -------------------------------------------------
    period = min(cfg.get("zscore_period", 50), len(close))
    z = zscore(close, period)
    if z is not None:
        out.append(
            Signal(
                key="price_zscore",
                category=CATEGORY_STATISTICAL,
                label="Koers z-score",
                value=round(z, 2),
                score=clamp(-z / 2.5),
                weight=0.8,
                rationale=(
                    f"Koers zit {z:+.2f} standaarddeviaties van het {period}-candle gemiddelde"
                ),
            )
        )

    # --- Regressiekanaal ------------------------------------------------------
    slope, r2 = linreg_slope(close, min(cfg.get("regression_period", 50), len(close)))
    if slope is not None and r2 is not None:
        out.append(
            Signal(
                key="regression",
                category=CATEGORY_STATISTICAL,
                label="Regressiehelling",
                value=round(slope, 4),
                score=clamp(slope / 1.0) * r2,
                weight=0.9,
                rationale=(
                    f"Lineaire trend {slope:+.3f}% per candle met R² {r2:.2f} "
                    f"({'strak' if r2 > 0.7 else 'rommelig'} verloop)"
                ),
                extra={"r_squared": round(r2, 3)},
            )
        )

    # --- Hurst ----------------------------------------------------------------
    hurst = hurst_exponent(close)
    if hurst is not None:
        if hurst > 0.55:
            note = "markt vertoont trendgedrag; trendvolgende signalen zijn betrouwbaarder"
        elif hurst < 0.45:
            note = "markt is mean-reverting; uitbraken falen vaker dan gemiddeld"
        else:
            note = "markt gedraagt zich als een random walk; wees extra sceptisch"
        out.append(
            Signal(
                key="hurst",
                category=CATEGORY_STATISTICAL,
                label="Hurst-exponent",
                value=round(hurst, 3),
                score=0.0,
                weight=0.0,
                rationale=f"Hurst {hurst:.2f}: {note}",
            )
        )

    # --- Drawdown -------------------------------------------------------------
    max_dd, cur_dd = max_drawdown(close, cfg.get("drawdown_period", 90))
    out.append(
        Signal(
            key="drawdown",
            category=CATEGORY_STATISTICAL,
            label="Drawdown",
            value=round(cur_dd, 2),
            score=0.0,
            weight=0.0,
            rationale=(
                f"Nu {cur_dd:.1f}% onder de recente piek; de diepste terugval in deze "
                f"periode was {max_dd:.1f}%"
            ),
            extra={"max_drawdown_pct": round(max_dd, 2)},
        )
    )

    # --- Risico/rendement -----------------------------------------------------
    sharpe = sharpe_like(close, periods_per_year=cfg.get("periods_per_year", 365))
    if sharpe is not None:
        out.append(
            Signal(
                key="risk_adjusted_return",
                category=CATEGORY_STATISTICAL,
                label="Risicogewogen rendement",
                value=round(sharpe, 2),
                score=0.0,
                weight=0.0,
                rationale=(
                    f"Rendement/risico-verhouding {sharpe:+.2f} "
                    "(geen echte Sharpe: zonder risicovrije voet)"
                ),
            )
        )

    return out
