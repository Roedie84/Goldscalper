"""Volume-indicatoren.

Waarschuwing die de moeite van het onthouden waard is: volumedata van
centrale exchanges is niet betrouwbaar op de manier waarop aandelenvolume dat
is. Wash trading is wijdverbreid, en volume verschilt fors per exchange voor
dezelfde coin. Deze indicatoren krijgen daarom bewust een lager gewicht dan
trend en momentum.
"""

from __future__ import annotations

from .core import (
    Series,
    clamp,
    ema,
    linreg_slope,
    safe_div,
    sma,
    zscore,
)
from .signals import CATEGORY_VOLUME, Candles, Signal


def obv(candles: Candles) -> Series:
    """On-Balance Volume."""
    out: Series = [None] * len(candles)
    if not len(candles):
        return out
    running = 0.0
    out[0] = 0.0
    for i in range(1, len(candles)):
        if candles.close[i] > candles.close[i - 1]:
            running += candles.volume[i]
        elif candles.close[i] < candles.close[i - 1]:
            running -= candles.volume[i]
        out[i] = running
    return out


def accumulation_distribution(candles: Candles) -> Series:
    out: Series = [None] * len(candles)
    running = 0.0
    for i in range(len(candles)):
        rng = candles.high[i] - candles.low[i]
        if rng == 0:
            multiplier = 0.0
        else:
            multiplier = (
                (candles.close[i] - candles.low[i]) - (candles.high[i] - candles.close[i])
            ) / rng
        running += multiplier * candles.volume[i]
        out[i] = running
    return out


def chaikin_money_flow(candles: Candles, period: int = 20) -> Series:
    n = len(candles)
    mfv: list[float] = [0.0] * n
    for i in range(n):
        rng = candles.high[i] - candles.low[i]
        if rng == 0:
            continue
        multiplier = (
            (candles.close[i] - candles.low[i]) - (candles.high[i] - candles.close[i])
        ) / rng
        mfv[i] = multiplier * candles.volume[i]
    out: Series = [None] * n
    for i in range(period - 1, n):
        vol_sum = sum(candles.volume[i - period + 1 : i + 1])
        out[i] = safe_div(sum(mfv[i - period + 1 : i + 1]), vol_sum)
    return out


def vwap(candles: Candles, period: int = 20) -> Series:
    """Rollende VWAP.

    Let op: dit is niet de session-VWAP die daytraders gebruiken. Crypto kent
    geen handelssessies, dus een rollend venster is hier de zinnigere variant.
    """
    tp = candles.hlc3
    n = len(candles)
    out: Series = [None] * n
    for i in range(period - 1, n):
        vols = candles.volume[i - period + 1 : i + 1]
        total = sum(vols)
        if total == 0:
            continue
        out[i] = sum(t * v for t, v in zip(tp[i - period + 1 : i + 1], vols)) / total
    return out


def force_index(candles: Candles, period: int = 13) -> Series:
    n = len(candles)
    raw: Series = [None] * n
    for i in range(1, n):
        raw[i] = (candles.close[i] - candles.close[i - 1]) * candles.volume[i]
    return ema(raw, period)


def ease_of_movement(candles: Candles, period: int = 14) -> Series:
    n = len(candles)
    raw: Series = [None] * n
    for i in range(1, n):
        distance = (candles.high[i] + candles.low[i]) / 2 - (
            candles.high[i - 1] + candles.low[i - 1]
        ) / 2
        box_ratio = safe_div(candles.volume[i] / 100000.0, candles.high[i] - candles.low[i])
        raw[i] = safe_div(distance, box_ratio)
    return sma(raw, period)


def volume_oscillator(candles: Candles, fast: int = 5, slow: int = 20) -> Series:
    f, s = sma(candles.volume, fast), sma(candles.volume, slow)
    return [
        None if (a is None or b in (None, 0)) else (a - b) / b * 100.0
        for a, b in zip(f, s)
    ]


def klinger(candles: Candles, fast: int = 34, slow: int = 55, signal: int = 13) -> tuple[Series, Series]:
    n = len(candles)
    vf: Series = [None] * n
    trend = 1
    cm = 0.0
    prev_dm = 0.0
    for i in range(1, n):
        hlc = candles.high[i] + candles.low[i] + candles.close[i]
        prev_hlc = candles.high[i - 1] + candles.low[i - 1] + candles.close[i - 1]
        new_trend = 1 if hlc > prev_hlc else -1
        dm = candles.high[i] - candles.low[i]
        if new_trend == trend:
            cm += dm
        else:
            cm = prev_dm + dm
            trend = new_trend
        prev_dm = dm
        ratio = abs(safe_div(2 * dm, cm) - 1.0) if cm else 0.0
        vf[i] = candles.volume[i] * ratio * trend * 100.0
    kvo: Series = [
        None if (a is None or b is None) else a - b
        for a, b in zip(ema(vf, fast), ema(vf, slow))
    ]
    return kvo, ema(kvo, signal)


def evaluate(candles: Candles, cfg: dict) -> list[Signal]:
    out: list[Signal] = []
    price = candles.close[-1]

    if sum(candles.volume[-30:]) == 0:
        out.append(
            Signal(
                key="volume_missing",
                category=CATEGORY_VOLUME,
                label="Volume",
                value=None,
                score=0.0,
                weight=0.0,
                rationale="Deze databron levert geen bruikbaar volume; volume-indicatoren zijn overgeslagen",
            )
        )
        return out

    # --- OBV ------------------------------------------------------------------
    o = obv(candles)
    slope, r2 = linreg_slope(o, min(30, len(o)))
    if slope is not None:
        out.append(
            Signal(
                key="obv",
                category=CATEGORY_VOLUME,
                label="On-Balance Volume",
                value=round(o[-1], 2),
                score=clamp(slope / 3.0) * (r2 or 0.0),
                weight=0.8,
                rationale=(
                    f"OBV-trend {slope:+.2f}% per candle met R² {r2:.2f}"
                    if r2 is not None
                    else f"OBV-trend {slope:+.2f}% per candle"
                ),
            )
        )

    # --- Chaikin Money Flow ---------------------------------------------------
    cmf = chaikin_money_flow(candles)
    if cmf[-1] is not None:
        out.append(
            Signal(
                key="cmf",
                category=CATEGORY_VOLUME,
                label="Chaikin Money Flow",
                value=round(cmf[-1], 4),
                score=clamp(cmf[-1] * 4.0),
                weight=0.8,
                rationale=(
                    f"CMF {cmf[-1]:+.3f}: geld stroomt "
                    f"{'de markt in' if cmf[-1] > 0 else 'de markt uit'}"
                ),
            )
        )

    # --- Accumulation / Distribution ------------------------------------------
    ad = accumulation_distribution(candles)
    ad_slope, ad_r2 = linreg_slope(ad, min(30, len(ad)))
    if ad_slope is not None:
        out.append(
            Signal(
                key="accum_dist",
                category=CATEGORY_VOLUME,
                label="Accumulatie/Distributie",
                value=round(ad[-1], 2),
                score=clamp(ad_slope / 3.0) * (ad_r2 or 0.0),
                weight=0.6,
                rationale=f"A/D-lijn helt {ad_slope:+.2f}% per candle",
            )
        )

    # --- VWAP -----------------------------------------------------------------
    vw = vwap(candles, cfg.get("vwap_period", 20))
    if vw[-1] is not None:
        dist = safe_div(price - vw[-1], vw[-1]) * 100.0
        out.append(
            Signal(
                key="vwap",
                category=CATEGORY_VOLUME,
                label="VWAP 20",
                value=round(vw[-1], 6),
                score=clamp(dist / 4.0),
                weight=0.9,
                rationale=f"Koers ligt {dist:+.2f}% t.o.v. de volumegewogen prijs",
            )
        )

    # --- Force Index ----------------------------------------------------------
    fi = force_index(candles)
    if fi[-1] is not None:
        norm = safe_div(fi[-1], price * (sum(candles.volume[-13:]) / 13 or 1)) * 100.0
        out.append(
            Signal(
                key="force_index",
                category=CATEGORY_VOLUME,
                label="Force Index",
                value=round(fi[-1], 2),
                score=clamp(norm * 5.0),
                weight=0.5,
                rationale=f"Force Index genormaliseerd op {norm:+.3f}",
            )
        )

    # --- Ease of Movement -----------------------------------------------------
    eom = ease_of_movement(candles)
    if eom[-1] is not None:
        out.append(
            Signal(
                key="eom",
                category=CATEGORY_VOLUME,
                label="Ease of Movement",
                value=round(eom[-1], 4),
                score=clamp(eom[-1] / 100.0),
                weight=0.4,
                rationale=(
                    f"De koers beweegt {'makkelijk omhoog' if eom[-1] > 0 else 'makkelijk omlaag'} "
                    "ten opzichte van het volume"
                ),
            )
        )

    # --- Volume-uitschieter ---------------------------------------------------
    vz = zscore(candles.volume, min(30, len(candles)))
    if vz is not None:
        direction = 1.0 if candles.close[-1] >= candles.open[-1] else -1.0
        spike = vz > 2.0
        out.append(
            Signal(
                key="volume_spike",
                category=CATEGORY_VOLUME,
                label="Volume-uitschieter",
                value=round(vz, 2),
                score=direction * min(1.0, max(0.0, (vz - 1.0) / 2.0)),
                weight=0.7 if spike else 0.3,
                rationale=(
                    f"Volume zit {vz:+.1f} standaarddeviaties van het gemiddelde"
                    + (" — dit is een echte uitschieter" if spike else "")
                ),
            )
        )

    # --- Volume-oscillator ----------------------------------------------------
    vo = volume_oscillator(candles)
    if vo[-1] is not None:
        out.append(
            Signal(
                key="volume_oscillator",
                category=CATEGORY_VOLUME,
                label="Volume-oscillator",
                value=round(vo[-1], 1),
                score=0.0,
                weight=0.0,
                rationale=(
                    f"Kortetermijnvolume ligt {vo[-1]:+.0f}% t.o.v. het langere gemiddelde; "
                    "een trend zonder volumegroei is verdacht"
                ),
            )
        )

    # --- Klinger --------------------------------------------------------------
    kvo, kvo_sig = klinger(candles)
    if kvo[-1] is not None and kvo_sig[-1] is not None:
        out.append(
            Signal(
                key="klinger",
                category=CATEGORY_VOLUME,
                label="Klinger Oscillator",
                value=round(kvo[-1], 2),
                score=clamp(safe_div(kvo[-1] - kvo_sig[-1], abs(kvo_sig[-1]) or 1.0)),
                weight=0.4,
                rationale=(
                    f"KVO {'boven' if kvo[-1] > kvo_sig[-1] else 'onder'} zijn signaallijn"
                ),
            )
        )

    return out
