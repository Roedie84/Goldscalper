"""Incrementele indicatoren moeten numeriek gelijk zijn aan de batch-versie.
Snelheid die de cijfers verandert is geen optimalisatie maar een bug."""
import os, sys, math, random
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.analysis.signals import Candles
from gold_scalper.analysis.core import ema, linreg_slope
from gold_scalper.analysis.momentum import rsi
from gold_scalper.analysis.volatility import atr, bollinger
from gold_scalper.strategy.streaming import (
    StreamState, RollingExtreme, RollingStdev, IncrementalLinReg,
)


@pytest.fixture(scope="module")
def series():
    random.seed(1)
    mid = 3300.0
    rows = []
    for _ in range(2000):
        mid += random.gauss(0, 0.35) - 0.02 * (mid - 3300.0)
        o = mid + random.gauss(0, 0.05); c = mid
        h = max(o, c) + abs(random.gauss(0, 0.12))
        l = min(o, c) - abs(random.gauss(0, 0.12))
        rows.append((o, h, l, c, 500.0))
    cd = Candles(list(range(len(rows))), *[[r[i] for r in rows] for i in range(5)])
    st = StreamState()
    for r in rows:
        st.push_candle(*r)
    return cd, st


def test_ema_matches_batch(series):
    cd, st = series
    assert st.ema_fast.value == pytest.approx(ema(cd.close, 9)[-1], abs=1e-9)
    assert st.ema_slow.value == pytest.approx(ema(cd.close, 21)[-1], abs=1e-9)


def test_rsi_matches_batch(series):
    cd, st = series
    assert st.rsi.value == pytest.approx(rsi(cd.close, 7)[-1], abs=1e-9)


def test_atr_matches_batch(series):
    cd, st = series
    assert st.atr.value == pytest.approx(atr(cd, 14)[-1], abs=1e-9)


def test_percent_b_matches_batch(series):
    cd, st = series
    expected = bollinger(cd.close, 20, 2.0)[3][-1]
    assert st.bollinger.percent_b(cd.close[-1]) == pytest.approx(expected, abs=1e-9)


def test_linreg_matches_batch(series):
    cd, st = series
    exp_slope, exp_r2 = linreg_slope(cd.close, 15)
    slope, r2 = st.linreg.result()
    assert slope == pytest.approx(exp_slope, abs=1e-8)
    assert r2 == pytest.approx(exp_r2, abs=1e-7)


def test_rolling_extreme_matches_naive():
    random.seed(5)
    values = [random.gauss(0, 1) for _ in range(500)]
    hi = RollingExtreme(20, maximum=True)
    lo = RollingExtreme(20, maximum=False)
    for i, v in enumerate(values):
        got_hi, got_lo = hi.push(v), lo.push(v)
        if i >= 19:
            window = values[i - 19 : i + 1]
            assert got_hi == pytest.approx(max(window))
            assert got_lo == pytest.approx(min(window))


def test_rolling_stdev_stays_accurate_on_large_values():
    """Gouddata rond 3300 met kleine spreiding is precies waar naïeve
    som-van-kwadraten catastrofaal afrondt."""
    random.seed(7)
    values = [3300.0 + random.gauss(0, 0.3) for _ in range(3000)]
    sd = RollingStdev(20)
    for v in values:
        sd.push(v)
    window = values[-20:]
    mean = sum(window) / 20
    expected = math.sqrt(sum((x - mean) ** 2 for x in window) / 20)
    assert sd.value == pytest.approx(expected, rel=1e-9)


def test_warmup_equals_sequential_push(series):
    cd, st = series
    fresh = StreamState()
    fresh.warm_up(cd)
    assert fresh.atr.value == pytest.approx(st.atr.value, abs=1e-12)
    assert fresh.rsi.value == pytest.approx(st.rsi.value, abs=1e-12)


def test_state_not_ready_before_sixty_bars():
    st = StreamState()
    for i in range(30):
        st.push_candle(3300, 3301, 3299, 3300.5)
    assert not st.ready
