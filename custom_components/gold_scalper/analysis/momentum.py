"""Momentum-oscillatoren en divergentiedetectie."""

from __future__ import annotations

from .core import (
    Series,
    clamp,
    ema,
    highest,
    lowest,
    rma,
    roc,
    safe_div,
    scale,
    sma,
)
from .signals import CATEGORY_MOMENTUM, Candles, Signal


def rsi(values: list[float], period: int = 14) -> Series:
    """Relative Strength Index volgens Wilder."""
    n = len(values)
    gains: Series = [None] * n
    losses: Series = [None] * n
    for i in range(1, n):
        change = values[i] - values[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)
    avg_gain = rma(gains[1:], period)
    avg_loss = rma(losses[1:], period)
    out: Series = [None] * n
    for i, (g, l) in enumerate(zip(avg_gain, avg_loss), start=1):
        if g is None or l is None:
            continue
        if l == 0:
            out[i] = 100.0
        else:
            rs = g / l
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def stochastic(
    candles: Candles, k_period: int = 14, k_smooth: int = 3, d_period: int = 3
) -> tuple[Series, Series]:
    hh = highest(candles.high, k_period)
    ll = lowest(candles.low, k_period)
    raw_k: Series = [None] * len(candles)
    for i in range(len(candles)):
        if hh[i] is None or ll[i] is None:
            continue
        rng = hh[i] - ll[i]
        raw_k[i] = 50.0 if rng == 0 else (candles.close[i] - ll[i]) / rng * 100.0
    k = sma(raw_k, k_smooth)
    return k, sma(k, d_period)


def stoch_rsi(
    close: list[float], rsi_period: int = 14, stoch_period: int = 14, smooth: int = 3
) -> tuple[Series, Series]:
    """Stochastic van de RSI. Reageert veel sneller dan RSI zelf, met
    navenant meer valse signalen; wordt daarom licht gewogen."""
    r = rsi(close, rsi_period)
    hh = highest(r, stoch_period)
    ll = lowest(r, stoch_period)
    raw: Series = [None] * len(close)
    for i in range(len(close)):
        if r[i] is None or hh[i] is None or ll[i] is None:
            continue
        rng = hh[i] - ll[i]
        raw[i] = 50.0 if rng == 0 else (r[i] - ll[i]) / rng * 100.0
    k = sma(raw, smooth)
    return k, sma(k, smooth)


def cci(candles: Candles, period: int = 20) -> Series:
    """Commodity Channel Index op basis van de mean absolute deviation."""
    tp = candles.hlc3
    ma = sma(tp, period)
    out: Series = [None] * len(tp)
    for i in range(len(tp)):
        if ma[i] is None:
            continue
        window = tp[i - period + 1 : i + 1]
        mad = sum(abs(v - ma[i]) for v in window) / period
        out[i] = safe_div(tp[i] - ma[i], 0.015 * mad)
    return out


def williams_r(candles: Candles, period: int = 14) -> Series:
    hh = highest(candles.high, period)
    ll = lowest(candles.low, period)
    out: Series = [None] * len(candles)
    for i in range(len(candles)):
        if hh[i] is None or ll[i] is None:
            continue
        rng = hh[i] - ll[i]
        out[i] = -50.0 if rng == 0 else (hh[i] - candles.close[i]) / rng * -100.0
    return out


def money_flow_index(candles: Candles, period: int = 14) -> Series:
    """MFI: RSI gewogen met volume."""
    tp = candles.hlc3
    n = len(candles)
    out: Series = [None] * n
    pos: list[float] = [0.0] * n
    neg: list[float] = [0.0] * n
    for i in range(1, n):
        flow = tp[i] * candles.volume[i]
        if tp[i] > tp[i - 1]:
            pos[i] = flow
        elif tp[i] < tp[i - 1]:
            neg[i] = flow
    for i in range(period, n):
        p = sum(pos[i - period + 1 : i + 1])
        ng = sum(neg[i - period + 1 : i + 1])
        if ng == 0:
            out[i] = 100.0 if p > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + p / ng)
    return out


def ultimate_oscillator(
    candles: Candles, short: int = 7, mid: int = 14, long: int = 28
) -> Series:
    n = len(candles)
    bp: Series = [None] * n
    tr: Series = [None] * n
    for i in range(1, n):
        low_or_prev = min(candles.low[i], candles.close[i - 1])
        high_or_prev = max(candles.high[i], candles.close[i - 1])
        bp[i] = candles.close[i] - low_or_prev
        tr[i] = high_or_prev - low_or_prev
    out: Series = [None] * n
    for i in range(long, n):
        avgs = []
        for period, weight in ((short, 4.0), (mid, 2.0), (long, 1.0)):
            bp_sum = sum(v for v in bp[i - period + 1 : i + 1] if v is not None)
            tr_sum = sum(v for v in tr[i - period + 1 : i + 1] if v is not None)
            avgs.append(weight * safe_div(bp_sum, tr_sum))
        out[i] = 100.0 * sum(avgs) / 7.0
    return out


def awesome_oscillator(candles: Candles) -> Series:
    hl2 = candles.hl2
    fast, slow = sma(hl2, 5), sma(hl2, 34)
    return [None if (f is None or s is None) else f - s for f, s in zip(fast, slow)]


def chande_momentum(close: list[float], period: int = 14) -> Series:
    n = len(close)
    out: Series = [None] * n
    for i in range(period, n):
        up = down = 0.0
        for j in range(i - period + 1, i + 1):
            change = close[j] - close[j - 1]
            if change > 0:
                up += change
            else:
                down -= change
        out[i] = safe_div(up - down, up + down) * 100.0
    return out


def ppo(close: list[float], fast: int = 12, slow: int = 26) -> Series:
    """Percentage Price Oscillator: MACD genormaliseerd, dus vergelijkbaar
    tussen coins met verschillende prijsniveaus."""
    f, s = ema(close, fast), ema(close, slow)
    return [
        None if (a is None or b in (None, 0)) else (a - b) / b * 100.0
        for a, b in zip(f, s)
    ]


def true_strength_index(close: list[float], long: int = 25, short: int = 13) -> Series:
    n = len(close)
    momentum: Series = [None] * n
    abs_momentum: Series = [None] * n
    for i in range(1, n):
        momentum[i] = close[i] - close[i - 1]
        abs_momentum[i] = abs(momentum[i])
    smooth = ema(ema(momentum, long), short)
    abs_smooth = ema(ema(abs_momentum, long), short)
    return [
        None if (a is None or b in (None, 0)) else 100.0 * a / b
        for a, b in zip(smooth, abs_smooth)
    ]


def find_divergence(
    price: list[float], oscillator: Series, lookback: int = 40, pivot: int = 5
) -> tuple[str | None, str]:
    """Zoek reguliere bullish/bearish divergentie tussen koers en oscillator.

    Werkt met bevestigde pivots: een punt telt pas als pivot als er ``pivot``
    candles aan beide kanten liggen. Dat betekent dat een divergentie altijd
    met vertraging wordt herkend. Dat is geen bug maar de prijs van niet
    voortdurend fantoomdivergenties melden op de laatste candle.
    """
    n = len(price)
    start = max(pivot, n - lookback)
    highs: list[int] = []
    lows: list[int] = []
    for i in range(start, n - pivot):
        window = price[i - pivot : i + pivot + 1]
        if price[i] == max(window):
            highs.append(i)
        if price[i] == min(window):
            lows.append(i)

    def _osc(idx: int) -> float | None:
        return oscillator[idx] if idx < len(oscillator) else None

    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        oa, ob = _osc(a), _osc(b)
        if oa is not None and ob is not None and price[b] < price[a] and ob > oa:
            return "bullish", (
                f"Koers zette een lagere bodem terwijl de oscillator hoger draaide "
                f"({oa:.1f} → {ob:.1f})"
            )
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        oa, ob = _osc(a), _osc(b)
        if oa is not None and ob is not None and price[b] > price[a] and ob < oa:
            return "bearish", (
                f"Koers zette een hogere top terwijl de oscillator lager draaide "
                f"({oa:.1f} → {ob:.1f})"
            )
    return None, "Geen divergentie gevonden"


def _oscillator_score(value: float, oversold: float, overbought: float) -> float:
    """Vertaal een begrensde oscillator naar een score.

    Bewust contrair: laag = koopsignaal. Maar níet lineair doorgetrokken naar
    het extreme, want in een sterke trend blijft een oscillator lang extreem
    staan en dan is 'oversold' geen koopsignaal maar een trendbevestiging.
    De engine dempt dit verder via de regime-weging.
    """
    mid = (oversold + overbought) / 2.0
    if value <= oversold:
        return clamp(0.6 + 0.4 * (oversold - value) / max(oversold, 1e-9))
    if value >= overbought:
        return clamp(-0.6 - 0.4 * (value - overbought) / max(100.0 - overbought, 1e-9))
    return clamp((mid - value) / (mid - oversold) * 0.5)


def evaluate(candles: Candles, cfg: dict) -> list[Signal]:
    close = candles.close
    out: list[Signal] = []

    # --- RSI ------------------------------------------------------------------
    rsi_period = cfg.get("rsi_period", 14)
    oversold = cfg.get("rsi_oversold", 30.0)
    overbought = cfg.get("rsi_overbought", 70.0)
    r = rsi(close, rsi_period)
    if r[-1] is not None:
        state = (
            "oversold" if r[-1] <= oversold
            else "overbought" if r[-1] >= overbought
            else "neutraal"
        )
        out.append(
            Signal(
                key="rsi",
                category=CATEGORY_MOMENTUM,
                label=f"RSI {rsi_period}",
                value=round(r[-1], 2),
                score=_oscillator_score(r[-1], oversold, overbought),
                weight=1.3,
                rationale=f"RSI staat op {r[-1]:.1f} ({state})",
            )
        )
        direction, note = find_divergence(close, r)
        if direction:
            out.append(
                Signal(
                    key="rsi_divergence",
                    category=CATEGORY_MOMENTUM,
                    label="RSI-divergentie",
                    value=direction,
                    score=0.85 if direction == "bullish" else -0.85,
                    weight=1.1,
                    rationale=note,
                )
            )

    # --- Stochastic -----------------------------------------------------------
    k, d = stochastic(candles)
    if k[-1] is not None and d[-1] is not None:
        out.append(
            Signal(
                key="stochastic",
                category=CATEGORY_MOMENTUM,
                label="Stochastic",
                value=round(k[-1], 2),
                score=_oscillator_score(k[-1], 20.0, 80.0),
                weight=0.9,
                rationale=f"%K {k[-1]:.1f} en %D {d[-1]:.1f}",
                extra={"d": round(d[-1], 2)},
            )
        )

    sk, sd = stoch_rsi(close)
    if sk[-1] is not None:
        out.append(
            Signal(
                key="stoch_rsi",
                category=CATEGORY_MOMENTUM,
                label="Stochastic RSI",
                value=round(sk[-1], 2),
                score=_oscillator_score(sk[-1], 20.0, 80.0),
                weight=0.6,
                rationale=f"StochRSI %K op {sk[-1]:.1f}",
            )
        )

    # --- CCI ------------------------------------------------------------------
    c = cci(candles)
    if c[-1] is not None:
        out.append(
            Signal(
                key="cci",
                category=CATEGORY_MOMENTUM,
                label="CCI 20",
                value=round(c[-1], 1),
                score=clamp(-c[-1] / 200.0),
                weight=0.8,
                rationale=f"CCI op {c[-1]:+.0f}",
            )
        )

    # --- Williams %R ----------------------------------------------------------
    wr = williams_r(candles)
    if wr[-1] is not None:
        out.append(
            Signal(
                key="williams_r",
                category=CATEGORY_MOMENTUM,
                label="Williams %R",
                value=round(wr[-1], 2),
                score=_oscillator_score(wr[-1] + 100.0, 20.0, 80.0),
                weight=0.7,
                rationale=f"Williams %R op {wr[-1]:.1f}",
            )
        )

    # --- MFI ------------------------------------------------------------------
    mfi = money_flow_index(candles)
    if mfi[-1] is not None:
        out.append(
            Signal(
                key="mfi",
                category=CATEGORY_MOMENTUM,
                label="Money Flow Index",
                value=round(mfi[-1], 2),
                score=_oscillator_score(mfi[-1], 20.0, 80.0),
                weight=0.9,
                rationale=f"MFI op {mfi[-1]:.1f} (RSI inclusief volume)",
            )
        )

    # --- Ultimate Oscillator --------------------------------------------------
    uo = ultimate_oscillator(candles)
    if uo[-1] is not None:
        out.append(
            Signal(
                key="ultimate",
                category=CATEGORY_MOMENTUM,
                label="Ultimate Oscillator",
                value=round(uo[-1], 2),
                score=_oscillator_score(uo[-1], 30.0, 70.0),
                weight=0.6,
                rationale=f"UO op {uo[-1]:.1f}",
            )
        )

    # --- Awesome Oscillator ---------------------------------------------------
    ao = awesome_oscillator(candles)
    if ao[-1] is not None:
        norm = safe_div(ao[-1], close[-1]) * 100.0
        out.append(
            Signal(
                key="awesome",
                category=CATEGORY_MOMENTUM,
                label="Awesome Oscillator",
                value=round(ao[-1], 6),
                score=clamp(norm * 15.0),
                weight=0.6,
                rationale=f"AO op {norm:+.3f}% van de koers",
            )
        )

    # --- Chande Momentum ------------------------------------------------------
    cmo = chande_momentum(close)
    if cmo[-1] is not None:
        out.append(
            Signal(
                key="cmo",
                category=CATEGORY_MOMENTUM,
                label="Chande Momentum",
                value=round(cmo[-1], 1),
                score=clamp(cmo[-1] / 60.0),
                weight=0.5,
                rationale=f"CMO op {cmo[-1]:+.1f}",
            )
        )

    # --- PPO ------------------------------------------------------------------
    p = ppo(close)
    if p[-1] is not None:
        out.append(
            Signal(
                key="ppo",
                category=CATEGORY_MOMENTUM,
                label="PPO",
                value=round(p[-1], 3),
                score=scale(p[-1], -4, 4),
                weight=0.6,
                rationale=f"PPO op {p[-1]:+.2f}%",
            )
        )

    # --- TSI ------------------------------------------------------------------
    tsi = true_strength_index(close)
    if tsi[-1] is not None:
        out.append(
            Signal(
                key="tsi",
                category=CATEGORY_MOMENTUM,
                label="True Strength Index",
                value=round(tsi[-1], 2),
                score=clamp(tsi[-1] / 40.0),
                weight=0.6,
                rationale=f"TSI op {tsi[-1]:+.1f}",
            )
        )

    # --- Rate of change -------------------------------------------------------
    for period in cfg.get("roc_periods", (7, 14, 30)):
        rc = roc(close, period)
        if rc[-1] is None:
            continue
        out.append(
            Signal(
                key=f"roc_{period}",
                category=CATEGORY_MOMENTUM,
                label=f"ROC {period}",
                value=round(rc[-1], 2),
                score=clamp(rc[-1] / 15.0),
                weight=0.4,
                rationale=f"Koers {rc[-1]:+.2f}% over {period} candles",
            )
        )

    return out
