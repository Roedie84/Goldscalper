"""Trendindicatoren."""

from __future__ import annotations

from .core import (
    Series,
    clamp,
    crossover,
    crossunder,
    ema,
    highest,
    hma,
    kama,
    lowest,
    rma,
    safe_div,
    scale,
    sma,
    true_range,
)
from .signals import CATEGORY_TREND, Candles, Signal


def macd(
    close: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9
) -> tuple[Series, Series, Series]:
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line: Series = [
        None if (f is None or s is None) else f - s for f, s in zip(fast_ema, slow_ema)
    ]
    signal_line = ema(macd_line, signal_period)
    histogram: Series = [
        None if (m is None or s is None) else m - s
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def adx(candles: Candles, period: int = 14) -> tuple[Series, Series, Series]:
    """Average Directional Index met +DI en -DI.

    ADX meet trendsterkte, niet trendrichting. Dat onderscheid wordt vaak
    verkeerd gebruikt: een hoge ADX bij dalende koers is een sterke *down*trend.
    """
    n = len(candles)
    plus_dm: Series = [None] * n
    minus_dm: Series = [None] * n
    for i in range(1, n):
        up = candles.high[i] - candles.high[i - 1]
        down = candles.low[i - 1] - candles.low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    tr = true_range(candles.high, candles.low, candles.close)
    atr_s = rma(tr, period)
    plus_smooth = rma(plus_dm, period)
    minus_smooth = rma(minus_dm, period)

    plus_di: Series = [None] * n
    minus_di: Series = [None] * n
    dx: Series = [None] * n
    for i in range(n):
        if atr_s[i] in (None, 0) or plus_smooth[i] is None or minus_smooth[i] is None:
            continue
        plus_di[i] = 100.0 * plus_smooth[i] / atr_s[i]
        minus_di[i] = 100.0 * minus_smooth[i] / atr_s[i]
        denom = plus_di[i] + minus_di[i]
        dx[i] = 100.0 * safe_div(abs(plus_di[i] - minus_di[i]), denom)

    return rma(dx, period), plus_di, minus_di


def aroon(candles: Candles, period: int = 25) -> tuple[Series, Series]:
    """Aroon Up/Down: hoe recent is de hoogste high resp. laagste low."""
    n = len(candles)
    up: Series = [None] * n
    down: Series = [None] * n
    for i in range(period, n):
        window_h = candles.high[i - period : i + 1]
        window_l = candles.low[i - period : i + 1]
        since_high = period - window_h.index(max(window_h))
        since_low = period - window_l.index(min(window_l))
        up[i] = (period - since_high) / period * 100.0
        down[i] = (period - since_low) / period * 100.0
    return up, down


def parabolic_sar(
    candles: Candles, step: float = 0.02, max_step: float = 0.2
) -> Series:
    """Parabolic SAR. Werkt slecht in zijwaartse markten; wordt daarom
    lager gewogen als de regime-detectie 'ranging' meldt."""
    n = len(candles)
    out: Series = [None] * n
    if n < 3:
        return out
    bullish = candles.close[1] >= candles.close[0]
    af = step
    ep = candles.high[1] if bullish else candles.low[1]
    sar = candles.low[0] if bullish else candles.high[0]
    out[1] = sar
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if bullish:
            sar = min(sar, candles.low[i - 1], candles.low[i - 2])
            if candles.low[i] < sar:
                bullish = False
                sar = ep
                ep = candles.low[i]
                af = step
            elif candles.high[i] > ep:
                ep = candles.high[i]
                af = min(af + step, max_step)
        else:
            sar = max(sar, candles.high[i - 1], candles.high[i - 2])
            if candles.high[i] > sar:
                bullish = True
                sar = ep
                ep = candles.high[i]
                af = step
            elif candles.low[i] < ep:
                ep = candles.low[i]
                af = min(af + step, max_step)
        out[i] = sar
    return out


def supertrend(
    candles: Candles, period: int = 10, multiplier: float = 3.0
) -> tuple[Series, list[int | None]]:
    """Supertrend-lijn plus richting (1 = bullish, -1 = bearish)."""
    n = len(candles)
    tr = true_range(candles.high, candles.low, candles.close)
    atr_s = rma(tr, period)
    hl2 = candles.hl2
    line: Series = [None] * n
    direction: list[int | None] = [None] * n
    upper = lower = None
    for i in range(n):
        if atr_s[i] is None:
            continue
        basic_upper = hl2[i] + multiplier * atr_s[i]
        basic_lower = hl2[i] - multiplier * atr_s[i]
        prev_close = candles.close[i - 1] if i > 0 else candles.close[i]
        upper = (
            basic_upper
            if upper is None or basic_upper < upper or prev_close > upper
            else upper
        )
        lower = (
            basic_lower
            if lower is None or basic_lower > lower or prev_close < lower
            else lower
        )
        prev_dir = direction[i - 1] if i > 0 else None
        if prev_dir is None:
            direction[i] = 1 if candles.close[i] >= basic_lower else -1
        elif prev_dir == 1 and candles.close[i] < lower:
            direction[i] = -1
        elif prev_dir == -1 and candles.close[i] > upper:
            direction[i] = 1
        else:
            direction[i] = prev_dir
        line[i] = lower if direction[i] == 1 else upper
    return line, direction


def ichimoku(
    candles: Candles, conversion: int = 9, base: int = 26, span_b: int = 52
) -> dict[str, Series]:
    """Ichimoku Kinko Hyo.

    De spans worden hier *niet* vooruit verschoven opgeslagen; de engine
    vergelijkt de huidige koers met de cloud die ``base`` candles geleden is
    geprojecteerd, wat neerkomt op hetzelfde maar zonder index-acrobatiek.
    """
    high_c, low_c = highest(candles.high, conversion), lowest(candles.low, conversion)
    high_b, low_b = highest(candles.high, base), lowest(candles.low, base)
    high_s, low_s = highest(candles.high, span_b), lowest(candles.low, span_b)

    tenkan: Series = [
        None if (h is None or l is None) else (h + l) / 2 for h, l in zip(high_c, low_c)
    ]
    kijun: Series = [
        None if (h is None or l is None) else (h + l) / 2 for h, l in zip(high_b, low_b)
    ]
    senkou_a: Series = [
        None if (t is None or k is None) else (t + k) / 2 for t, k in zip(tenkan, kijun)
    ]
    senkou_b: Series = [
        None if (h is None or l is None) else (h + l) / 2 for h, l in zip(high_s, low_s)
    ]
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
    }


def vortex(candles: Candles, period: int = 14) -> tuple[Series, Series]:
    n = len(candles)
    vm_plus: Series = [None] * n
    vm_minus: Series = [None] * n
    for i in range(1, n):
        vm_plus[i] = abs(candles.high[i] - candles.low[i - 1])
        vm_minus[i] = abs(candles.low[i] - candles.high[i - 1])
    tr = true_range(candles.high, candles.low, candles.close)
    sum_tr = sma(tr, period)
    sum_p = sma(vm_plus, period)
    sum_m = sma(vm_minus, period)
    vi_plus: Series = [
        None if (p is None or t in (None, 0)) else p / t for p, t in zip(sum_p, sum_tr)
    ]
    vi_minus: Series = [
        None if (m is None or t in (None, 0)) else m / t for m, t in zip(sum_m, sum_tr)
    ]
    return vi_plus, vi_minus


def trix(close: list[float], period: int = 15) -> Series:
    """TRIX: procentuele verandering van een drievoudig gladgestreken EMA."""
    e3 = ema(ema(ema(close, period), period), period)
    out: Series = [None] * len(close)
    for i in range(1, len(e3)):
        if e3[i] is None or e3[i - 1] in (None, 0):
            continue
        out[i] = (e3[i] - e3[i - 1]) / e3[i - 1] * 10000.0
    return out


def evaluate(candles: Candles, cfg: dict) -> list[Signal]:
    """Draai alle trendindicatoren en vertaal ze naar signalen."""
    close = candles.close
    price = close[-1]
    out: list[Signal] = []

    # --- Moving average stack -------------------------------------------------
    fast_p, slow_p = cfg.get("ma_fast", 50), cfg.get("ma_slow", 200)
    ema_fast, ema_slow = ema(close, fast_p), ema(close, slow_p)
    if ema_fast[-1] is not None and ema_slow[-1] is not None:
        gap = safe_div(ema_fast[-1] - ema_slow[-1], ema_slow[-1]) * 100.0
        golden = crossover(ema_fast, ema_slow)
        death = crossunder(ema_fast, ema_slow)
        score = clamp(gap / 5.0)
        if golden:
            score, note = 1.0, "Golden cross zojuist voltooid"
        elif death:
            score, note = -1.0, "Death cross zojuist voltooid"
        else:
            note = (
                f"EMA{fast_p} ligt {gap:+.2f}% ten opzichte van EMA{slow_p}"
            )
        out.append(
            Signal(
                key="ema_cross",
                category=CATEGORY_TREND,
                label=f"EMA {fast_p}/{slow_p}",
                value=round(gap, 3),
                score=score,
                weight=1.4,
                rationale=note,
                extra={"ema_fast": round(ema_fast[-1], 6), "ema_slow": round(ema_slow[-1], 6)},
            )
        )

    for period in cfg.get("sma_periods", (20, 50, 100, 200)):
        line = sma(close, period)
        if line[-1] is None:
            continue
        dist = safe_div(price - line[-1], line[-1]) * 100.0
        out.append(
            Signal(
                key=f"sma_{period}",
                category=CATEGORY_TREND,
                label=f"SMA {period}",
                value=round(line[-1], 6),
                score=clamp(dist / 8.0),
                weight=0.5 if period < 100 else 0.8,
                rationale=f"Koers staat {dist:+.2f}% t.o.v. de SMA{period}",
            )
        )

    hull = hma(close, cfg.get("hma_period", 21))
    if hull[-1] is not None and hull[-2] is not None:
        slope_pct = safe_div(hull[-1] - hull[-2], hull[-2]) * 100.0
        out.append(
            Signal(
                key="hma",
                category=CATEGORY_TREND,
                label="Hull MA 21",
                value=round(hull[-1], 6),
                score=clamp(slope_pct / 1.5),
                weight=0.7,
                rationale=f"Hull MA helt {slope_pct:+.3f}% per candle",
            )
        )

    adaptive = kama(close)
    if adaptive[-1] is not None:
        dist = safe_div(price - adaptive[-1], adaptive[-1]) * 100.0
        out.append(
            Signal(
                key="kama",
                category=CATEGORY_TREND,
                label="KAMA",
                value=round(adaptive[-1], 6),
                score=clamp(dist / 5.0),
                weight=0.6,
                rationale=f"Koers {dist:+.2f}% t.o.v. de adaptieve MA",
            )
        )

    # --- MACD -----------------------------------------------------------------
    macd_line, signal_line, hist = macd(
        close, cfg.get("macd_fast", 12), cfg.get("macd_slow", 26), cfg.get("macd_signal", 9)
    )
    if hist[-1] is not None:
        rising = hist[-2] is not None and hist[-1] > hist[-2]
        norm = safe_div(hist[-1], price) * 100.0
        score = clamp(norm * 20.0)
        if crossover(macd_line, signal_line):
            score = max(score, 0.8)
            note = "MACD kruist zijn signaallijn opwaarts"
        elif crossunder(macd_line, signal_line):
            score = min(score, -0.8)
            note = "MACD kruist zijn signaallijn neerwaarts"
        else:
            note = (
                f"Histogram {'loopt op' if rising else 'loopt terug'} "
                f"op {norm:+.4f}% van de koers"
            )
        out.append(
            Signal(
                key="macd",
                category=CATEGORY_TREND,
                label="MACD",
                value=round(macd_line[-1], 6) if macd_line[-1] is not None else None,
                score=score,
                weight=1.2,
                rationale=note,
                extra={"histogram": round(hist[-1], 6)},
            )
        )

    # --- ADX / DMI ------------------------------------------------------------
    adx_line, plus_di, minus_di = adx(candles, cfg.get("adx_period", 14))
    if adx_line[-1] is not None and plus_di[-1] is not None and minus_di[-1] is not None:
        strength = min(1.0, adx_line[-1] / 40.0)
        direction = 1.0 if plus_di[-1] > minus_di[-1] else -1.0
        out.append(
            Signal(
                key="adx",
                category=CATEGORY_TREND,
                label="ADX / DMI",
                value=round(adx_line[-1], 2),
                score=direction * strength,
                weight=1.1,
                rationale=(
                    f"ADX {adx_line[-1]:.1f} "
                    f"({'sterke' if adx_line[-1] > 25 else 'zwakke'} trend), "
                    f"+DI {plus_di[-1]:.1f} vs -DI {minus_di[-1]:.1f}"
                ),
                extra={"plus_di": round(plus_di[-1], 2), "minus_di": round(minus_di[-1], 2)},
            )
        )

    # --- Aroon ----------------------------------------------------------------
    ar_up, ar_down = aroon(candles, cfg.get("aroon_period", 25))
    if ar_up[-1] is not None and ar_down[-1] is not None:
        out.append(
            Signal(
                key="aroon",
                category=CATEGORY_TREND,
                label="Aroon",
                value=round(ar_up[-1] - ar_down[-1], 1),
                score=clamp((ar_up[-1] - ar_down[-1]) / 100.0),
                weight=0.7,
                rationale=f"Aroon Up {ar_up[-1]:.0f} tegen Down {ar_down[-1]:.0f}",
            )
        )

    # --- Parabolic SAR --------------------------------------------------------
    sar = parabolic_sar(candles)
    if sar[-1] is not None:
        bullish = price > sar[-1]
        dist = abs(safe_div(price - sar[-1], price)) * 100.0
        out.append(
            Signal(
                key="psar",
                category=CATEGORY_TREND,
                label="Parabolic SAR",
                value=round(sar[-1], 6),
                score=(1.0 if bullish else -1.0) * min(1.0, dist / 4.0 + 0.4),
                weight=0.6,
                rationale=(
                    f"SAR staat {'onder' if bullish else 'boven'} de koers "
                    f"op {dist:.2f}% afstand"
                ),
            )
        )

    # --- Supertrend -----------------------------------------------------------
    st_line, st_dir = supertrend(candles)
    if st_dir[-1] is not None:
        flipped = len(st_dir) > 1 and st_dir[-2] is not None and st_dir[-2] != st_dir[-1]
        out.append(
            Signal(
                key="supertrend",
                category=CATEGORY_TREND,
                label="Supertrend",
                value=round(st_line[-1], 6) if st_line[-1] is not None else None,
                score=float(st_dir[-1]) * (1.0 if flipped else 0.7),
                weight=1.0,
                rationale=(
                    ("Supertrend is zojuist omgeslagen naar " if flipped else "Supertrend staat op ")
                    + ("bullish" if st_dir[-1] == 1 else "bearish")
                ),
            )
        )

    # --- Ichimoku -------------------------------------------------------------
    ich = ichimoku(candles)
    sa, sb = ich["senkou_a"][-1], ich["senkou_b"][-1]
    if sa is not None and sb is not None:
        cloud_top, cloud_bottom = max(sa, sb), min(sa, sb)
        if price > cloud_top:
            score, note = 0.9, "Koers ligt boven de Ichimoku-cloud"
        elif price < cloud_bottom:
            score, note = -0.9, "Koers ligt onder de Ichimoku-cloud"
        else:
            score, note = 0.0, "Koers zit ín de cloud: geen richting"
        tenkan, kijun = ich["tenkan"][-1], ich["kijun"][-1]
        if tenkan is not None and kijun is not None and score != 0:
            score = clamp(score + (0.1 if tenkan > kijun else -0.1))
        out.append(
            Signal(
                key="ichimoku",
                category=CATEGORY_TREND,
                label="Ichimoku",
                value=round(cloud_top, 6),
                score=score,
                weight=1.0,
                rationale=note,
                extra={"cloud_top": round(cloud_top, 6), "cloud_bottom": round(cloud_bottom, 6)},
            )
        )

    # --- Vortex ---------------------------------------------------------------
    vi_p, vi_m = vortex(candles)
    if vi_p[-1] is not None and vi_m[-1] is not None:
        out.append(
            Signal(
                key="vortex",
                category=CATEGORY_TREND,
                label="Vortex",
                value=round(vi_p[-1] - vi_m[-1], 4),
                score=clamp((vi_p[-1] - vi_m[-1]) * 3.0),
                weight=0.6,
                rationale=f"VI+ {vi_p[-1]:.3f} tegen VI- {vi_m[-1]:.3f}",
            )
        )

    # --- TRIX -----------------------------------------------------------------
    tx = trix(close)
    if tx[-1] is not None:
        out.append(
            Signal(
                key="trix",
                category=CATEGORY_TREND,
                label="TRIX",
                value=round(tx[-1], 3),
                score=scale(tx[-1], -20, 20),
                weight=0.5,
                rationale=f"TRIX op {tx[-1]:+.2f} basispunten",
            )
        )

    return out
